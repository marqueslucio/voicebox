"""One active STT model, with inference and model switching serialized."""

import asyncio
import gc
import threading

from ..utils.platform_detect import get_backend_type
from . import WHISPER_HF_REPOS
from .base import DownloadCancelled, is_model_cached
from .parakeet_backend import MODEL_SIZE, ParakeetSTTBackend


class STTBackendRouter:
    def __init__(self):
        self.model_size = "base"
        self._backend = None
        # ponytail: serialize STT; GPU batching can replace this if throughput matters.
        self._lock = threading.RLock()

    @staticmethod
    def validate_model(model_size):
        if model_size not in (*WHISPER_HF_REPOS, MODEL_SIZE):
            raise ValueError(f"Invalid STT model '{model_size}'")

    @staticmethod
    def validate_language(model_size, language):
        if model_size == MODEL_SIZE:
            ParakeetSTTBackend.validate_language(language)

    def _is_model_cached(self, model_size):
        self.validate_model(model_size)
        if model_size == MODEL_SIZE:
            return ParakeetSTTBackend()._is_model_cached(model_size)
        return is_model_cached(WHISPER_HF_REPOS[model_size], weight_extensions=(".safetensors", ".bin", ".npz"))

    def is_loaded(self):
        backend = self._backend
        return backend is not None and backend.is_loaded()

    def unload_model(self, model_size=None):
        with self._lock:
            if model_size is not None and self.model_size != model_size:
                return False
            was_loaded = self.is_loaded()
            if self._backend is not None:
                self._backend.unload_model()
                self._backend = None
                gc.collect()
                if get_backend_type() == "mlx":
                    import mlx.core as mx

                    mx.clear_cache()
            return was_loaded

    def _select(self, model_size):
        self.validate_model(model_size)
        if self._backend is None or self.model_size != model_size:
            self.unload_model()
            if model_size == MODEL_SIZE:
                self._backend = ParakeetSTTBackend()
            else:
                from .mlx_backend import MLXSTTBackend

                self._backend = MLXSTTBackend(model_size)
            self.model_size = model_size
        return self._backend

    def _load_sync(self, model_size, download_task):
        with self._lock:
            if download_task is not None and download_task.cancelled.is_set():
                raise DownloadCancelled("Model download cancelled")
            backend = self._select(model_size)
            if model_size == MODEL_SIZE:
                backend._download_task = download_task
            if download_task is not None and download_task.cancelled.is_set():
                raise DownloadCancelled("Model download cancelled")
            asyncio.run(backend.load_model_async(model_size))

    async def load_model_async(self, model_size=None, download_task=None):
        await asyncio.to_thread(self._load_sync, model_size or self.model_size, download_task)

    load_model = load_model_async

    def _transcribe_sync(self, audio_path, language, model_size):
        with self._lock:
            backend = self._select(model_size)
            if model_size == MODEL_SIZE:
                backend._download_task = None
            return asyncio.run(backend.transcribe(audio_path, language, model_size))

    async def transcribe(self, audio_path, language=None, model_size=None):
        model_size = model_size or self.model_size
        self.validate_model(model_size)
        self.validate_language(model_size, language)
        return await asyncio.to_thread(self._transcribe_sync, audio_path, language, model_size)
