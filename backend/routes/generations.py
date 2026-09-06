"""TTS generation endpoints."""

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import config, models
from ..database import (
    Generation as DBGeneration,
    Story as DBStory,
    StoryItem as DBStoryItem,
    VoiceProfile as DBVoiceProfile,
    get_db,
)
from ..services import history, personality, profiles, settings, tts
from ..services.generation import run_generation
from ..services.task_queue import cancel_generation as cancel_generation_job, enqueue_generation
from ..utils.audio import load_audio
from ..utils.tasks import get_task_manager

logger = logging.getLogger(__name__)

router = APIRouter()

IMPORTED_AUDIO_PROFILE_NAME = "Imported Audio"
IMPORT_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".webm"}
IMPORT_AUDIO_MAX_BYTES = 200 * 1024 * 1024  # 200 MB


def _get_or_create_import_profile(db: Session) -> DBVoiceProfile:
    """Singleton profile every imported audio clip points at — keeps the
    Generation FK happy without making profile_id nullable across the schema."""
    row = (
        db.query(DBVoiceProfile)
        .filter(DBVoiceProfile.name == IMPORTED_AUDIO_PROFILE_NAME)
        .first()
    )
    if row is not None:
        return row
    row = DBVoiceProfile(
        id=str(uuid.uuid4()),
        name=IMPORTED_AUDIO_PROFILE_NAME,
        description="External audio imported into a story timeline.",
        language="en",
        voice_type="import",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _resolve_generation_engine(data: models.GenerationRequest, profile) -> str:
    return data.engine or getattr(profile, "default_engine", None) or getattr(profile, "preset_engine", None) or "qwen"


def _parse_delivery_settings(raw) -> models.DeliverySettings | None:
    if not raw:
        return None
    try:
        settings = models.DeliverySettings.model_validate(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid saved delivery settings: {exc}") from exc
    return settings if settings.enabled else None


def _delivery_instruction(settings: models.DeliverySettings) -> str | None:
    if settings.engine != "qwen_custom_voice":
        return None

    return "Use energy {}/100, warmth {}/100, and expressive variation {}/100.".format(
        round(settings.energy * 100),
        round(settings.warmth * 100),
        round(settings.expression * 100),
    )


async def _resolve_request_context(data: models.GenerationRequest, db: Session) -> dict:
    settings = None
    if data.story_id:
        story = db.query(DBStory).filter_by(id=data.story_id).first()
        if not story:
            raise HTTPException(status_code=404, detail="Story not found")
        settings = _parse_delivery_settings(story.delivery_settings)

    profile_id = settings.profile_id if settings else data.profile_id
    profile = await profiles.get_profile(profile_id, db)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    engine = settings.engine if settings else _resolve_generation_engine(data, profile)
    try:
        profiles.validate_profile_engine(profile, engine)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from ..backends import engine_has_model_sizes

    requested_model = settings.model_size if settings else data.model_size or "1.7B"
    temperature = data.temperature
    if engine == "qwen" and temperature is None:
        temperature = round(0.3 + 0.105 * profile.default_voice_variation, 3)
    return {
        "profile": profile,
        "profile_id": profile_id,
        "engine": engine,
        "model_size": requested_model if engine_has_model_sizes(engine) else None,
        "language": settings.language if settings else data.language,
        "seed": settings.seed if settings else data.seed,
        "instruct": _delivery_instruction(settings) if settings else data.instruct,
        "speed": settings.speed if settings else 1.0,
        "snapshot": settings.model_dump() if settings else None,
        "temperature": temperature if engine == "qwen" else None,
    }


@router.post("/generate", response_model=models.GenerationResponse)
async def generate_speech(
    data: models.GenerationRequest,
    db: Session = Depends(get_db),
):
    """Generate speech from text using a voice profile."""
    task_manager = get_task_manager()
    generation_id = str(uuid.uuid4())

    context = await _resolve_request_context(data, db)
    profile = context["profile"]

    text = data.text
    source = "manual"
    if data.personality and getattr(profile, "personality", None):
        try:
            capture_settings = settings.get_capture_settings(db)
            llm_result = await personality.rewrite_as_profile(
                profile.personality,
                data.text,
                model_size=capture_settings.llm_model,
                language=context["language"],
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.exception("Personality rewrite failed for profile %s", context["profile_id"])
            raise HTTPException(status_code=500, detail="Personality rewrite failed") from e
        text = llm_result.text.strip()
        if not text:
            raise HTTPException(status_code=500, detail="LLM produced empty output; nothing to speak.")
        source = "personality_speak"

    generation = await history.create_generation(
        profile_id=context["profile_id"],
        text=text,
        language=context["language"],
        audio_path="",
        duration=0,
        seed=context["seed"],
        db=db,
        instruct=context["instruct"],
        generation_id=generation_id,
        status="generating",
        engine=context["engine"],
        model_size=context["model_size"],
        source=source,
        delivery_settings=context["snapshot"],
        temperature=context["temperature"],
    )

    task_manager.start_generation(
        task_id=generation_id,
        profile_id=context["profile_id"],
        text=text,
    )

    effects_chain_config = None
    if data.effects_chain is not None:
        effects_chain_config = [e.model_dump() for e in data.effects_chain]
    else:
        import json as _json

        profile_obj = db.query(DBVoiceProfile).filter_by(id=context["profile_id"]).first()
        if profile_obj and profile_obj.effects_chain:
            try:
                effects_chain_config = _json.loads(profile_obj.effects_chain)
            except Exception:
                pass

    enqueue_generation(
        generation_id,
        run_generation(
            generation_id=generation_id,
            profile_id=context["profile_id"],
            text=text,
            language=context["language"],
            engine=context["engine"],
            model_size=context["model_size"],
            seed=context["seed"],
            normalize=data.normalize,
            effects_chain=effects_chain_config,
            instruct=context["instruct"],
            mode="generate",
            max_chunk_chars=data.max_chunk_chars,
            crossfade_ms=data.crossfade_ms,
            speed=context["speed"],
            temperature=context["temperature"],
        )
    )

    return generation


@router.post("/generate/{generation_id}/retry", response_model=models.GenerationResponse)
async def retry_generation(generation_id: str, db: Session = Depends(get_db)):
    """Retry a failed generation using the same parameters."""
    gen = db.query(DBGeneration).filter_by(id=generation_id).first()
    if not gen:
        raise HTTPException(status_code=404, detail="Generation not found")

    if (gen.status or "completed") != "failed":
        raise HTTPException(status_code=400, detail="Only failed generations can be retried")

    settings = _parse_delivery_settings(gen.delivery_settings)
    if settings:
        if settings.profile_id != gen.profile_id:
            raise HTTPException(status_code=400, detail="Saved delivery profile does not match generation voice")
        profile = db.query(DBVoiceProfile).filter_by(id=gen.profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        try:
            profiles.validate_profile_engine(profile, settings.engine)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    gen.status = "generating"
    gen.error = None
    gen.audio_path = ""
    gen.duration = 0
    db.commit()
    db.refresh(gen)

    task_manager = get_task_manager()
    task_manager.start_generation(
        task_id=generation_id,
        profile_id=gen.profile_id,
        text=gen.text,
    )

    enqueue_generation(
        generation_id,
        run_generation(
            generation_id=generation_id,
            profile_id=gen.profile_id,
            text=gen.text,
            language=settings.language if settings else gen.language,
            engine=settings.engine if settings else gen.engine or "qwen",
            model_size=settings.model_size if settings else gen.model_size or "1.7B",
            seed=settings.seed if settings else gen.seed,
            instruct=_delivery_instruction(settings) if settings else gen.instruct,
            mode="retry",
            speed=settings.speed if settings else 1.0,
            temperature=gen.temperature,
        )
    )

    return models.GenerationResponse.model_validate(gen)


@router.post(
    "/generate/{generation_id}/regenerate",
    response_model=models.GenerationResponse,
)
async def regenerate_generation(
    generation_id: str,
    data: models.RegenerateRequest | None = None,
    db: Session = Depends(get_db),
):
    """Re-run TTS with the same parameters and save the result as a new version."""
    gen = db.query(DBGeneration).filter_by(id=generation_id).first()
    if not gen:
        raise HTTPException(status_code=404, detail="Generation not found")
    if (gen.status or "completed") != "completed":
        raise HTTPException(status_code=400, detail="Generation must be completed to regenerate")

    settings = None
    if data and data.story_id:
        story = db.query(DBStory).filter_by(id=data.story_id).first()
        if not story:
            raise HTTPException(status_code=404, detail="Story not found")
        if not db.query(DBStoryItem).filter_by(
            story_id=story.id,
            generation_id=gen.id,
        ).first():
            raise HTTPException(status_code=400, detail="Generation does not belong to this story")
        settings = _parse_delivery_settings(story.delivery_settings)
    elif gen.delivery_settings:
        settings = _parse_delivery_settings(gen.delivery_settings)

    if settings:
        if settings.profile_id != gen.profile_id:
            raise HTTPException(
                status_code=400,
                detail="Story delivery profile differs from this generation; create a new clip to preserve voice identity",
            )
        if settings.engine != (gen.engine or "qwen"):
            raise HTTPException(
                status_code=400,
                detail="Story delivery engine differs from this generation; create a new clip to preserve voice identity",
            )
        profile = db.query(DBVoiceProfile).filter_by(id=gen.profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        try:
            profiles.validate_profile_engine(profile, settings.engine)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        gen.language = settings.language
        gen.model_size = settings.model_size
        gen.seed = settings.seed
        gen.instruct = _delivery_instruction(settings)
        gen.delivery_settings = settings.model_dump()

    gen.status = "generating"
    gen.error = None
    db.commit()
    db.refresh(gen)

    task_manager = get_task_manager()
    task_manager.start_generation(
        task_id=generation_id,
        profile_id=gen.profile_id,
        text=gen.text,
    )

    version_id = str(uuid.uuid4())

    enqueue_generation(
        generation_id,
        run_generation(
            generation_id=generation_id,
            profile_id=gen.profile_id,
            text=gen.text,
            language=settings.language if settings else gen.language,
            engine=settings.engine if settings else gen.engine or "qwen",
            model_size=settings.model_size if settings else gen.model_size or "1.7B",
            seed=settings.seed if settings else None,
            instruct=_delivery_instruction(settings) if settings else gen.instruct,
            mode="regenerate",
            version_id=version_id,
            speed=settings.speed if settings else 1.0,
            temperature=gen.temperature,
        )
    )

    return models.GenerationResponse.model_validate(gen)


@router.post("/generate/{generation_id}/cancel")
async def cancel_generation(generation_id: str, db: Session = Depends(get_db)):
    """Cancel a queued or running generation."""
    gen = db.query(DBGeneration).filter_by(id=generation_id).first()
    if not gen:
        raise HTTPException(status_code=404, detail="Generation not found")

    if (gen.status or "completed") not in ("loading_model", "generating"):
        raise HTTPException(status_code=400, detail="Only active generations can be cancelled")

    cancellation_state = cancel_generation_job(generation_id)
    if cancellation_state is None:
        # Row says active but the worker is no longer tracking it — the gen
        # coroutine exited without writing a terminal status (most often a
        # SQLite lock racing with the failed-status write inside the worker's
        # exception handler). Fail the row here so the user can move on.
        task_manager = get_task_manager()
        task_manager.complete_generation(generation_id)
        await history.update_generation_status(
            generation_id=generation_id,
            status="failed",
            db=db,
            error="Generation orphaned by worker",
        )
        return {"message": "Orphaned generation cleared"}

    if cancellation_state == "queued":
        task_manager = get_task_manager()
        task_manager.complete_generation(generation_id)
        await history.update_generation_status(
            generation_id=generation_id,
            status="failed",
            db=db,
            error="Generation cancelled",
        )
        return {"message": "Queued generation cancelled"}

    return {"message": "Generation cancellation requested"}


@router.get("/generate/{generation_id}/status")
async def get_generation_status(generation_id: str, db: Session = Depends(get_db)):
    """SSE endpoint that streams generation status updates."""
    import json

    async def event_stream():
        try:
            while True:
                db.expire_all()
                gen = db.query(DBGeneration).filter_by(id=generation_id).first()
                if not gen:
                    yield f"data: {json.dumps({'status': 'not_found', 'id': generation_id})}\n\n"
                    return

                payload = {
                    "id": gen.id,
                    "status": gen.status or "completed",
                    "duration": gen.duration,
                    "error": gen.error,
                    # Agent-originated sources ("mcp", "rest") skip main-window
                    # autoplay — the floating pill plays those directly.
                    "source": gen.source,
                }
                yield f"data: {json.dumps(payload)}\n\n"

                if (gen.status or "completed") in ("completed", "failed"):
                    return

                await asyncio.sleep(1)
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            logger.debug("SSE client disconnected for generation %s", generation_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/generate/stream")
async def stream_speech(
    data: models.GenerationRequest,
    db: Session = Depends(get_db),
):
    """Generate speech and stream the WAV audio directly without saving to disk."""
    from ..backends import (
        engine_needs_trim,
        engine_retries_runaway,
        ensure_model_cached_or_raise,
        get_tts_backend_for_engine,
        load_engine_model,
    )

    context = await _resolve_request_context(data, db)
    profile = context["profile"]
    engine = context["engine"]
    tts_model = get_tts_backend_for_engine(engine)
    model_size = context["model_size"] or data.model_size or "1.7B"

    await ensure_model_cached_or_raise(engine, model_size)
    await load_engine_model(engine, model_size)

    voice_prompt = await profiles.create_voice_prompt_for_profile(
        context["profile_id"],
        db,
        engine=engine,
    )

    from ..utils.chunked_tts import generate_chunked

    trim_fn = None
    runaway_detector = None
    if engine_needs_trim(engine):
        from ..utils.audio import trim_tts_output

        trim_fn = trim_tts_output
    if engine_retries_runaway(engine):
        from ..utils.audio import has_tts_runaway

        runaway_detector = has_tts_runaway

    audio, sample_rate = await generate_chunked(
        tts_model,
        data.text,
        voice_prompt,
        language=context["language"],
        seed=context["seed"],
        instruct=context["instruct"],
        max_chunk_chars=data.max_chunk_chars,
        crossfade_ms=data.crossfade_ms,
        temperature=context["temperature"],
        trim_fn=trim_fn,
        runaway_detector=runaway_detector,
    )

    from ..utils.audio import time_stretch_audio

    audio = time_stretch_audio(audio, context["speed"])

    effects_chain_config = None
    if data.effects_chain is not None:
        effects_chain_config = [e.model_dump() for e in data.effects_chain]
    elif profile.effects_chain:
        import json as _json

        try:
            effects_chain_config = _json.loads(profile.effects_chain)
        except Exception:
            effects_chain_config = None

    if effects_chain_config:
        from ..utils.effects import apply_effects

        audio = apply_effects(audio, sample_rate, effects_chain_config)

    if data.normalize:
        from ..utils.audio import normalize_audio

        audio = normalize_audio(audio)

    wav_bytes = tts.audio_to_wav_bytes(audio, sample_rate)

    async def _wav_stream():
        try:
            chunk_size = 64 * 1024
            for i in range(0, len(wav_bytes), chunk_size):
                yield wav_bytes[i : i + chunk_size]
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            logger.debug("Client disconnected during audio stream")

    return StreamingResponse(
        _wav_stream(),
        media_type="audio/wav",
        headers={"Content-Disposition": 'attachment; filename="speech.wav"'},
    )


@router.post("/generate/import", response_model=models.GenerationResponse)
async def import_audio(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Register an external audio file as a generation row.

    Designed for the story timeline so users can drop in music or other
    non-TTS audio. The row points at a singleton "Imported Audio" profile
    so the existing generation/story plumbing keeps working unchanged."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMPORT_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format '{suffix}'. Allowed: {sorted(IMPORT_AUDIO_EXTENSIONS)}",
        )

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > IMPORT_AUDIO_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds {IMPORT_AUDIO_MAX_BYTES // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)
    audio_bytes = b"".join(chunks)
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")

    generation_id = str(uuid.uuid4())
    target = config.get_generations_dir() / f"{generation_id}{suffix}"
    target.write_bytes(audio_bytes)

    try:
        audio, sr = load_audio(str(target))
        duration = float(len(audio) / sr) if sr else 0.0
    except Exception as decode_err:
        try:
            target.unlink()
        except OSError:
            pass
        raise HTTPException(
            status_code=400,
            detail=f"Could not decode audio: {decode_err}",
        ) from decode_err

    profile = _get_or_create_import_profile(db)
    display_name = Path(file.filename or "Imported audio").stem or "Imported audio"

    generation = await history.create_generation(
        profile_id=profile.id,
        text=display_name,
        language="en",
        audio_path=config.to_storage_path(target),
        duration=duration,
        seed=None,
        db=db,
        generation_id=generation_id,
        status="completed",
        engine="import",
        model_size=None,
        source="import",
    )
    from ..services import versions as versions_mod

    version = versions_mod.create_version(
        generation_id=generation_id,
        label="original",
        audio_path=config.to_storage_path(target),
        db=db,
        is_default=True,
        duration=duration,
    )
    generation.versions = [version]
    generation.active_version_id = version.id
    return generation
