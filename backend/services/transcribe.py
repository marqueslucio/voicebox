"""
STT (Speech-to-Text) module - delegates to backend abstraction layer.
"""

from typing import Optional
from ..backends import get_stt_backend


def get_whisper_model():
    """
    Get the shared STT router (Whisper or native Parakeet).
    
    Returns:
        STT backend instance
    """
    return get_stt_backend()


def unload_whisper_model():
    """Unload the active STT model to free memory."""
    backend = get_stt_backend()
    backend.unload_model()


def resolve_transcription_settings(model, language, db):
    """Use the same saved model/language for captures, uploads, samples, and MCP."""
    from .settings import get_capture_settings

    saved = get_capture_settings(db)
    return (
        model or saved.stt_model,
        (None if saved.language == "auto" else saved.language) if language is None else
        (None if language == "auto" else language),
    )
