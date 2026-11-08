"""Registry and shared backend for local speech recognition."""

from dataclasses import dataclass, field
from typing import Optional

from ..utils import hf_offline_patch  # noqa: F401

WHISPER_HF_REPOS = {
    "base": "openai/whisper-base",
    "small": "openai/whisper-small",
    "medium": "openai/whisper-medium",
    "large": "openai/whisper-large-v3",
    "turbo": "openai/whisper-large-v3-turbo",
}

@dataclass
class ModelConfig:
    model_name: str
    display_name: str
    engine: str
    hf_repo_id: str
    model_size: str = "default"
    size_mb: int = 0
    weight_filename: Optional[str] = None
    languages: list[str] = field(default_factory=lambda: ["en"])

def _get_whisper_configs() -> list[ModelConfig]:
    """Return Whisper STT model configs."""
    return [
        ModelConfig(
            model_name="whisper-base",
            display_name="Whisper Base",
            engine="whisper",
            hf_repo_id="openai/whisper-base",
            model_size="base",
        ),
        ModelConfig(
            model_name="whisper-small",
            display_name="Whisper Small",
            engine="whisper",
            hf_repo_id="openai/whisper-small",
            model_size="small",
        ),
        ModelConfig(
            model_name="whisper-medium",
            display_name="Whisper Medium",
            engine="whisper",
            hf_repo_id="openai/whisper-medium",
            model_size="medium",
        ),
        ModelConfig(
            model_name="whisper-large",
            display_name="Whisper Large",
            engine="whisper",
            hf_repo_id="openai/whisper-large-v3",
            model_size="large",
        ),
        ModelConfig(
            model_name="whisper-turbo",
            display_name="Whisper Turbo",
            engine="whisper",
            hf_repo_id="openai/whisper-large-v3-turbo",
            model_size="turbo",
        ),
    ]


def get_stt_model_configs() -> list[ModelConfig]:
    """Return Whisper and quantized native Parakeet STT models."""
    from .parakeet_backend import HF_REPO, LANGUAGES, MODEL_FILENAME, MODEL_NAME, MODEL_SIZE

    return _get_whisper_configs() + [ModelConfig(
        model_name=MODEL_NAME,
        display_name="Parakeet v3 (Q8)",
        engine="parakeet",
        hf_repo_id=HF_REPO,
        model_size=MODEL_SIZE,
        size_mb=714,
        weight_filename=MODEL_FILENAME,
        languages=sorted(LANGUAGES),
    )]


def check_model_cached(config: ModelConfig) -> bool:
    """Native Q8 readiness must match the pinned file used by its loader."""
    if config.engine == "parakeet":
        from .parakeet_backend import ParakeetSTTBackend

        return ParakeetSTTBackend()._is_model_cached(config.model_size)
    from .base import is_model_cached

    return is_model_cached(config.hf_repo_id)


def get_model_config(model_name: str) -> Optional[ModelConfig]:
    """Look up a model config by model_name."""
    for cfg in get_all_model_configs():
        if cfg.model_name == model_name:
            return cfg
    return None


def get_all_model_configs():
    return get_stt_model_configs()


def unload_model_by_config(config):
    return get_stt_backend().unload_model(config.model_size)


def check_model_loaded(config):
    backend = get_stt_backend()
    return backend.is_loaded() and backend.model_size == config.model_size


def get_model_load_func(config, download_task=None):
    return lambda: get_stt_backend().load_model(config.model_size, download_task=download_task)


_stt_backend = None


def get_stt_backend():
    global _stt_backend
    if _stt_backend is None:
        from .stt_backend import STTBackendRouter
        _stt_backend = STTBackendRouter()
    return _stt_backend


def reset_backends():
    global _stt_backend
    _stt_backend = None
