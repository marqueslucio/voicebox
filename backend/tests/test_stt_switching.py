"""STT switching must preserve one model and serialize native handle destruction."""

import asyncio
import ctypes
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.backends import get_model_config
from backend.backends.parakeet_backend import (
    LANGUAGES,
    MODEL_FILENAME,
    MODEL_SIZE,
    ParakeetSTTBackend,
    _BackendConfig,
    _ModelConfig,
    _RecognitionOptions,
    _RecognizerConfig,
)
from backend.backends.stt_backend import STTBackendRouter
from backend.models import CaptureRetranscribeRequest, CaptureSettingsUpdate
from backend.services.transcribe import resolve_transcription_settings


def test_switching_serializes_inference_and_unload():
    active = set()
    entered, release = threading.Event(), threading.Event()

    class FakeBackend:
        validate_language = staticmethod(ParakeetSTTBackend.validate_language)

        def __init__(self, model_size=MODEL_SIZE):
            self.model_size = model_size

        def is_loaded(self):
            return self.model_size in active

        async def load_model_async(self, model_size):
            assert not (active - {model_size}), "previous weights must be released first"
            active.add(model_size)

        async def transcribe(self, path, language, model_size):
            await self.load_model_async(model_size)
            if path == "slow.wav":
                entered.set()
                assert release.wait(5)
                assert self.is_loaded(), "unload must wait until inference ends"
            return f"{model_size}:{language}"

        def unload_model(self):
            active.discard(self.model_size)

    router = STTBackendRouter()
    with (
        patch("backend.backends.stt_backend.ParakeetSTTBackend", FakeBackend),
        patch("backend.backends.stt_backend.get_backend_type", return_value="mlx"),
        patch("backend.backends.mlx_backend.MLXSTTBackend", FakeBackend),
    ):
        assert asyncio.run(router.transcribe("file.wav", "de", "turbo")) == "turbo:de"
        assert asyncio.run(router.transcribe("file.wav", None, MODEL_SIZE)) == f"{MODEL_SIZE}:None"
        assert active == {MODEL_SIZE}
        assert router.unload_model("turbo") is False
        assert active == {MODEL_SIZE}
        with ThreadPoolExecutor(max_workers=2) as pool:
            infer = pool.submit(asyncio.run, router.transcribe("slow.wav", "de", "turbo"))
            assert entered.wait(5)
            unload = pool.submit(router.unload_model, "turbo")
            assert not unload.done()
            release.set()
            assert infer.result(timeout=5) == "turbo:de"
            assert unload.result(timeout=5) is True
        assert not router.is_loaded()
        assert not active


def test_parakeet_contract_and_language_validation():
    cfg = get_model_config("parakeet-tdt-0.6b-v3")
    assert cfg.engine == "parakeet"
    assert cfg.model_size == MODEL_SIZE
    assert cfg.weight_filename == MODEL_FILENAME
    assert CaptureSettingsUpdate(stt_model=MODEL_SIZE).stt_model == MODEL_SIZE
    assert CaptureRetranscribeRequest(model=MODEL_SIZE).model == MODEL_SIZE
    assert len(LANGUAGES) == 25
    assert "uk" in LANGUAGES
    for language in (None, "auto", "de", "de-DE", "uk"):
        ParakeetSTTBackend.validate_language(language)
    with pytest.raises(ValueError, match="does not support"):
        asyncio.run(STTBackendRouter().transcribe("never-opened.wav", "ja", MODEL_SIZE))
    # Stable SDK layouts from its header on supported 64-bit macOS.
    assert [ctypes.sizeof(t) for t in (_BackendConfig, _ModelConfig, _RecognizerConfig, _RecognitionOptions)] == [
        16,
        24,
        80,
        72,
    ]


def test_transcription_defaults_use_saved_selection():
    saved = SimpleNamespace(stt_model=MODEL_SIZE, language="de")
    with patch("backend.services.settings.get_capture_settings", return_value=saved):
        assert resolve_transcription_settings(None, None, object()) == (MODEL_SIZE, "de")
        assert resolve_transcription_settings("turbo", "auto", object()) == ("turbo", None)
        assert resolve_transcription_settings(None, "en", object()) == (MODEL_SIZE, "en")


def test_cancelled_queued_load_never_switches_models():
    from backend.backends.base import DownloadCancelled
    from backend.utils.tasks import TaskManager

    router = STTBackendRouter()
    tm = TaskManager()
    task = tm.start_download("parakeet-tdt-0.6b-v3")
    with ThreadPoolExecutor(max_workers=1) as pool, patch.object(router, "_select") as select:
        with router._lock:
            queued = pool.submit(asyncio.run, router.load_model_async(MODEL_SIZE, download_task=task))
            tm.cancel_download(task.model_name)
        with pytest.raises(DownloadCancelled):
            queued.result(timeout=5)
        select.assert_not_called()
        assert not router.is_loaded()


def test_cancel_during_transfer_prevents_native_create(tmp_path):
    from unittest.mock import Mock

    from backend.backends.base import DownloadCancelled
    from backend.utils.tasks import TaskManager

    tm = TaskManager()
    task = tm.start_download("parakeet-tdt-0.6b-v3")
    entered, release = threading.Event(), threading.Event()
    backend = ParakeetSTTBackend()
    backend._download_task = task
    backend._library = SimpleNamespace(nemo_speech_asr_create=Mock())

    def download(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return str(tmp_path / "never-created.gguf")

    with (
        patch.object(backend, "_cached_path", return_value=None),
        patch("backend.backends.base.get_task_manager", return_value=tm),
        patch("huggingface_hub.hf_hub_download", side_effect=download),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        attempt = pool.submit(backend._load_model_sync)
        try:
            assert entered.wait(5)
            assert tm.cancel_download(task.model_name)
            retry = tm.start_download(task.model_name)
        finally:
            release.set()
        with pytest.raises(DownloadCancelled):
            attempt.result(timeout=5)
        backend._library.nemo_speech_asr_create.assert_not_called()
        assert not backend.is_loaded()
        assert tm.get_pending_downloads() == [retry]
        tm.complete_download(task.model_name, task)
        tm.error_download(task.model_name, "late old error", task)
        assert tm.get_pending_downloads() == [retry]


def test_parakeet_cache_criterion_is_shared():
    from backend.backends import check_model_cached

    cfg = get_model_config("parakeet-tdt-0.6b-v3")
    with patch.object(ParakeetSTTBackend, "_cached_path", return_value=None):
        assert not check_model_cached(cfg)
        assert not STTBackendRouter()._is_model_cached(MODEL_SIZE)
    with patch.object(ParakeetSTTBackend, "_cached_path", return_value="verified-pinned-file.gguf"):
        assert check_model_cached(cfg)
        assert STTBackendRouter()._is_model_cached(MODEL_SIZE)


def test_already_cancelled_load_does_not_recreate_progress():
    from backend.backends.base import DownloadCancelled, model_load_progress
    from backend.utils.progress import ProgressManager
    from backend.utils.tasks import TaskManager

    tm, pm = TaskManager(), ProgressManager()
    task = tm.start_download("parakeet-tdt-0.6b-v3")
    tm.cancel_download(task.model_name)
    with (
        patch("backend.backends.base.get_task_manager", return_value=tm),
        patch("backend.backends.base.get_progress_manager", return_value=pm),
        patch("backend.backends.base.HFProgressTracker.patch_download") as tracker,
    ):
        with pytest.raises(DownloadCancelled), model_load_progress(task.model_name, False, download_task=task):
            pytest.fail("Cancelled load must not enter the loader")
        tracker.assert_not_called()
        assert pm.get_progress(task.model_name) is None
        assert pm.get_all_active() == []
        assert tm.get_active_downloads() == []
