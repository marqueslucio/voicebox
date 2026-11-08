"""Parakeet v3 Q8 via the stable NeMo Speech C ABI (Metal on Apple Silicon)."""

import asyncio
import ctypes as ct
import hashlib
import logging
from pathlib import Path

import numpy as np

from ..utils.audio import load_audio
from .base import model_load_progress

logger = logging.getLogger(__name__)

MODEL_NAME = "parakeet-tdt-0.6b-v3"
MODEL_SIZE = "parakeet-v3"
HF_REPO = "nvidia/parakeet-tdt-0.6b-v3"
MODEL_REVISION = "541d1f99c6b0c3cd0b11a95167540bb8edefd82b"
MODEL_FILENAME = "parakeet-tdt-0.6b-v3.q8_0.gguf"
MODEL_BYTES = 713975456
MODEL_SHA256 = "e3880d0aaaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e"
LANGUAGES = frozenset(
    [
        "bg",
        "hr",
        "cs",
        "da",
        "nl",
        "en",
        "et",
        "fi",
        "fr",
        "de",
        "el",
        "hu",
        "it",
        "lv",
        "lt",
        "mt",
        "pl",
        "pt",
        "ro",
        "ru",
        "sk",
        "sl",
        "es",
        "sv",
        "uk",
    ]
)


# These layouts follow include/nemo_speech/asr.h from the bundled 0.2.0 SDK.
class _BackendConfig(ct.Structure):
    _fields_ = [("size", ct.c_size_t), ("gpu", ct.c_int32)]


class _ModelConfig(ct.Structure):
    _fields_ = [("size", ct.c_size_t), ("path", ct.c_char_p), ("name", ct.c_char_p)]


class _RecognizerConfig(ct.Structure):
    _fields_ = [
        ("size", ct.c_size_t),
        ("backend", ct.POINTER(_BackendConfig)),
        ("model", ct.POINTER(_ModelConfig)),
        *[
            (name, ct.c_void_p)
            for name in ("streaming", "decoder", "vad", "endpointing", "postproc", "diar", "batching")
        ],
    ]


class _RecognitionOptions(ct.Structure):
    _fields_ = [
        ("size", ct.c_size_t),
        ("request_id", ct.c_char_p),
        ("language_code", ct.c_char_p),
        ("interim_results", ct.c_bool),
        ("enable_word_time_offsets", ct.c_bool),
        ("enable_automatic_punctuation", ct.c_bool),
        ("verbatim_transcripts", ct.c_bool),
        ("profanity_filter", ct.c_bool),
        ("stop_history_eou_ms", ct.c_int32),
        ("speech_contexts", ct.c_void_p),
        ("speech_context_count", ct.c_size_t),
        ("max_alternatives", ct.c_int32),
        ("enable_speaker_diarization", ct.c_bool),
        ("max_speaker_count", ct.c_int32),
    ]


class ParakeetSTTBackend:
    def __init__(self):
        self.model_size = MODEL_SIZE
        self._library = None
        self._download_task = None
        self._recognizer = ct.c_void_p()

    @staticmethod
    def validate_language(language):
        if language and language != "auto" and language.split("-")[0].lower() not in LANGUAGES:
            raise ValueError(
                f"Parakeet v3 does not support language '{language}'. "
                "Choose a supported European language, automatic detection, or Whisper."
            )

    @staticmethod
    def _cached_path():
        from huggingface_hub import try_to_load_from_cache

        path = try_to_load_from_cache(HF_REPO, MODEL_FILENAME, revision=MODEL_REVISION)
        if isinstance(path, str) and Path(path).is_file() and Path(path).stat().st_size == MODEL_BYTES:
            return path
        return None

    def _is_model_cached(self, model_size=None):
        return model_size in (None, MODEL_SIZE) and self._cached_path() is not None

    def is_loaded(self):
        return bool(self._recognizer.value)

    def _load_library(self):
        path = Path(__file__).resolve().parents[1] / "native/nemo-speech/lib/libnemo_speech_asr_c.1.dylib"
        if not path.is_file():
            raise RuntimeError("Parakeet's native Metal runtime is missing. Reinstall Voicebox for Apple Silicon.")
        lib = ct.CDLL(str(path))
        signatures = {
            "create": ([ct.POINTER(_RecognizerConfig), ct.POINTER(ct.c_void_p)], ct.c_int),
            "destroy": ([ct.c_void_p], None),
            "recognition_options_default": ([], _RecognitionOptions),
            "recognize_f32": (
                [
                    ct.c_void_p,
                    ct.POINTER(_RecognitionOptions),
                    ct.POINTER(ct.c_float),
                    ct.c_size_t,
                    ct.c_int32,
                    ct.POINTER(ct.c_void_p),
                ],
                ct.c_int,
            ),
            "result_alternative_count": ([ct.c_void_p], ct.c_size_t),
            "result_transcript": ([ct.c_void_p, ct.c_size_t], ct.c_char_p),
            "result_destroy": ([ct.c_void_p], None),
            "last_error": ([], ct.c_char_p),
        }
        for name, (args, result) in signatures.items():
            function = getattr(lib, f"nemo_speech_asr_{name}")
            function.argtypes, function.restype = args, result
        self._library = lib

    def _check(self, status):
        if status:
            message = self._library.nemo_speech_asr_last_error()
            raise RuntimeError(message.decode("utf-8", errors="replace") if message else "Parakeet inference failed")

    async def load_model_async(self, model_size=None):
        if model_size not in (None, MODEL_SIZE):
            raise ValueError(f"Invalid Parakeet model: {model_size}")
        await asyncio.to_thread(self._load_model_sync)

    load_model = load_model_async

    def _load_model_sync(self):
        if self.is_loaded():
            return
        with model_load_progress(
            MODEL_NAME, self._is_model_cached(), download_task=self._download_task
        ) as check_cancelled:
            if self._library is None:
                self._load_library()
            check_cancelled()
            path = self._cached_path()
            if path is None:
                from huggingface_hub import hf_hub_download

                path = hf_hub_download(HF_REPO, MODEL_FILENAME, revision=MODEL_REVISION)
            check_cancelled()
            with open(path, "rb") as weights:
                if hashlib.file_digest(weights, "sha256").hexdigest() != MODEL_SHA256:
                    raise ValueError(
                        "Parakeet Q8 weights are incomplete or corrupt. Delete and download this model again."
                    )
            check_cancelled()
            backend = _BackendConfig(ct.sizeof(_BackendConfig), 0)
            model = _ModelConfig(ct.sizeof(_ModelConfig), str(path).encode(), None)
            config = _RecognizerConfig()
            config.size, config.backend, config.model = ct.sizeof(config), ct.pointer(backend), ct.pointer(model)
            handle = ct.c_void_p()
            try:
                self._check(self._library.nemo_speech_asr_create(ct.byref(config), ct.byref(handle)))
                check_cancelled()
            except Exception:
                if handle.value:
                    self._library.nemo_speech_asr_destroy(handle)
                raise
            self._recognizer = handle
        logger.info("Parakeet v3 Q8 loaded via native Metal")

    def unload_model(self):
        if self.is_loaded():
            self._library.nemo_speech_asr_destroy(self._recognizer)
            self._recognizer = ct.c_void_p()
            logger.info("Parakeet v3 unloaded")

    async def transcribe(self, audio_path, language=None, model_size=None):
        self.validate_language(language)
        await self.load_model_async(model_size)
        return await asyncio.to_thread(self._transcribe_sync, audio_path, language)

    def _transcribe_sync(self, audio_path, language):
        audio, rate = load_audio(audio_path, sample_rate=16000)
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
            raise ValueError("Audio must contain non-empty, finite mono samples")
        options = self._library.nemo_speech_asr_recognition_options_default()
        options.enable_automatic_punctuation = True
        if language and language != "auto":
            options.language_code = language.encode()
        result = ct.c_void_p()
        try:
            self._check(
                self._library.nemo_speech_asr_recognize_f32(
                    self._recognizer,
                    ct.byref(options),
                    audio.ctypes.data_as(ct.POINTER(ct.c_float)),
                    audio.size,
                    rate,
                    ct.byref(result),
                )
            )
            if not result.value or not self._library.nemo_speech_asr_result_alternative_count(result):
                return ""
            text = self._library.nemo_speech_asr_result_transcript(result, 0)
            return text.decode("utf-8").strip() if text else ""
        finally:
            if result.value:
                self._library.nemo_speech_asr_result_destroy(result)
