"""
Pydantic models for request/response validation.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from .utils.capture_chords import (
    default_push_to_talk_chord,
    default_toggle_to_talk_chord,
)


class TranscriptionRequest(BaseModel):
    """Request model for audio transcription."""

    language: Optional[str] = None
    model: Optional[str] = Field(None, pattern="^(base|small|medium|large|turbo|parakeet-v3)$")


class TranscriptionResponse(BaseModel):
    """Response model for transcription."""

    text: str
    duration: float


class RefinementFlagsModel(BaseModel):
    """Boolean toggles that drive the refinement prompt builder."""

    smart_cleanup: bool = True
    self_correction: bool = True
    preserve_technical: bool = True


class CaptureResponse(BaseModel):
    """Response model for a capture."""

    id: str
    audio_path: str
    source: str
    language: Optional[str] = None
    duration_ms: Optional[int] = None
    transcript_raw: str
    transcript_refined: Optional[str] = None
    stt_model: Optional[str] = None
    llm_model: Optional[str] = None
    refinement_flags: Optional[RefinementFlagsModel] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CaptureListResponse(BaseModel):
    """Response model for paginated capture list."""

    items: List[CaptureResponse]
    total: int


class CaptureCreateResponse(CaptureResponse):
    """
    Response model for ``POST /captures``.

    Adds ``auto_refine`` and ``allow_auto_paste`` — the server-side settings
    captured at the moment the capture was created. The client reads these to
    decide whether to chain a refinement request and whether to fire the
    synthetic-paste pipeline, so it doesn't need a synced local copy of the
    capture_settings table across sibling Tauri webviews.
    """

    auto_refine: bool
    allow_auto_paste: bool


class CaptureRetranscribeRequest(BaseModel):
    """Request to re-run STT on a capture's audio with a different model."""

    model: Optional[str] = Field(None, pattern="^(base|small|medium|large|turbo|parakeet-v3)$")
    language: Optional[str] = None


class CaptureSettingsResponse(BaseModel):
    """Server-persisted defaults for the capture / refine flow."""

    stt_model: str = Field(default="turbo", pattern="^(base|small|medium|large|turbo|parakeet-v3)$")
    language: str = Field(default="auto")
    auto_refine: bool = True
    llm_model: str = Field(default="0.6B", pattern="^(0\\.6B|1\\.7B|4B)$")
    smart_cleanup: bool = True
    self_correction: bool = True
    preserve_technical: bool = True
    allow_auto_paste: bool = True
    default_playback_voice_id: Optional[str] = None
    hotkey_enabled: bool = False
    chord_push_to_talk_keys: List[str] = Field(
        default_factory=default_push_to_talk_chord
    )
    chord_toggle_to_talk_keys: List[str] = Field(
        default_factory=default_toggle_to_talk_chord
    )

    class Config:
        from_attributes = True


class CaptureSettingsUpdate(BaseModel):
    """Partial update for capture settings — every field is optional."""

    stt_model: Optional[str] = Field(default=None, pattern="^(base|small|medium|large|turbo|parakeet-v3)$")
    language: Optional[str] = None
    auto_refine: Optional[bool] = None
    llm_model: Optional[str] = Field(default=None, pattern="^(0\\.6B|1\\.7B|4B)$")
    smart_cleanup: Optional[bool] = None
    self_correction: Optional[bool] = None
    preserve_technical: Optional[bool] = None
    allow_auto_paste: Optional[bool] = None
    default_playback_voice_id: Optional[str] = None
    hotkey_enabled: Optional[bool] = None
    chord_push_to_talk_keys: Optional[List[str]] = Field(default=None, min_length=1, max_length=6)
    chord_toggle_to_talk_keys: Optional[List[str]] = Field(default=None, min_length=1, max_length=6)


class ModelReadiness(BaseModel):
    """Per-model entry in the dictation readiness checklist.

    ``model_name`` is the canonical id used by ``POST /models/download`` so the
    frontend can wire a one-click "Download" button without a second lookup.
    ``size`` is the user's chosen variant (e.g. "turbo", "0.6B"); ``display_name``
    is what the checklist row should show ("Whisper Turbo").
    """

    ready: bool
    model_name: str
    display_name: str
    size: str
    size_mb: Optional[int] = None


class CaptureReadinessResponse(BaseModel):
    """Backend gates that must be green before the global hotkey will fire.

    The frontend combines this with its own TCC permission checks (input
    monitoring, accessibility) into the full dictation readiness checklist.
    Hotkey-enabled is the user's intent toggle and lives outside this struct.
    """

    stt: ModelReadiness


class HealthResponse(BaseModel):
    """Response model for health check."""

    status: str
    model_loaded: bool
    model_downloaded: Optional[bool] = None  # Whether model is cached/downloaded
    model_size: Optional[str] = None  # Current model size if loaded
    gpu_available: bool
    gpu_type: Optional[str] = None  # GPU type (CUDA, MPS, or None)
    vram_used_mb: Optional[float] = None
    backend_type: Optional[str] = None  # Backend type (mlx or pytorch)
    backend_variant: Optional[str] = None  # Binary variant (cpu, cuda, or rocm)
    supports_rocm: bool = False  # AMD GPU on Windows — the ROCm backend is applicable
    gpu_compatibility_warning: Optional[str] = None  # Warning if GPU arch unsupported


class DirectoryCheck(BaseModel):
    """Health status for a single directory."""

    path: str
    exists: bool
    writable: bool
    error: Optional[str] = None


class FilesystemHealthResponse(BaseModel):
    """Response model for filesystem health check."""

    healthy: bool
    disk_free_mb: Optional[float] = None
    disk_total_mb: Optional[float] = None
    directories: List[DirectoryCheck]


class ModelStatus(BaseModel):
    """Response model for model status."""

    model_name: str
    display_name: str
    hf_repo_id: Optional[str] = None  # HuggingFace repository ID
    downloaded: bool
    downloading: bool = False  # True if download is in progress
    size_mb: Optional[float] = None
    loaded: bool = False


class ModelStatusListResponse(BaseModel):
    """Response model for model status list."""

    models: List[ModelStatus]


class ModelDownloadRequest(BaseModel):
    """Request model for triggering model download."""

    model_name: str


class ActiveDownloadTask(BaseModel):
    """Response model for active download task."""

    model_name: str
    status: str
    started_at: datetime
    error: Optional[str] = None
    progress: Optional[float] = None  # 0-100 percentage
    current: Optional[int] = None  # bytes downloaded
    total: Optional[int] = None  # total bytes
    filename: Optional[str] = None  # current file being downloaded


class ActiveTasksResponse(BaseModel):
    """Response model for active tasks."""

    downloads: List[ActiveDownloadTask]
