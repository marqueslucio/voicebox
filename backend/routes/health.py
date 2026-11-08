"""Health and infrastructure endpoints."""

import asyncio
import os
import signal
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import config, models
from ..services import transcribe
from ..database import get_db
from ..utils.platform_detect import get_backend_type

router = APIRouter()

# Frontend build directory — present in Docker, absent in dev/API-only mode
_frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"


@router.get("/")
async def root():
    """Root endpoint — serves SPA index.html in Docker, JSON otherwise."""
    from .. import __version__

    index = _frontend_dir / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html")
    return {"message": "voicebox API", "version": __version__}


@router.post("/shutdown")
async def shutdown():
    """Gracefully shutdown the server."""

    async def shutdown_async():
        await asyncio.sleep(0.1)
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(shutdown_async())
    return {"message": "Shutting down..."}


@router.post("/watchdog/disable")
async def watchdog_disable():
    """Disable the parent process watchdog so the server keeps running."""
    from backend.server import disable_watchdog

    disable_watchdog()
    return {"message": "Watchdog disabled"}


@router.get("/health", response_model=models.HealthResponse)
async def health():
    backend = transcribe.get_whisper_model()
    loaded = backend.is_loaded()
    backend_type = get_backend_type()
    return models.HealthResponse(
        status="healthy",
        model_loaded=loaded,
        model_size=backend.model_size if loaded else None,
        gpu_available=backend_type == "mlx",
        gpu_type="Metal (Apple Silicon via MLX)" if backend_type == "mlx" else None,
        backend_type=backend_type,
        backend_variant="cpu",
    )


@router.get("/health/filesystem", response_model=models.FilesystemHealthResponse)
async def filesystem_health():
    """Check filesystem health: directory existence, write permissions, and disk space."""
    import shutil

    dirs_to_check = {
        "captures": config.get_captures_dir(),
        "data": config.get_data_dir(),
    }

    checks: list[models.DirectoryCheck] = []
    all_ok = True

    for _label, dir_path in dirs_to_check.items():
        exists = dir_path.exists()
        writable = False
        error = None
        if exists:
            probe = dir_path / ".voicebox_probe"
            try:
                probe.write_text("ok")
                probe.unlink()
                writable = True
            except PermissionError:
                error = "Permission denied"
            except OSError as e:
                error = str(e)
            finally:
                try:
                    probe.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            error = "Directory does not exist"

        if not exists or not writable:
            all_ok = False

        checks.append(
            models.DirectoryCheck(
                path=str(dir_path.resolve()),
                exists=exists,
                writable=writable,
                error=error,
            )
        )

    disk_free_mb = None
    disk_total_mb = None
    try:
        usage = shutil.disk_usage(str(config.get_data_dir()))
        disk_free_mb = round(usage.free / (1024 * 1024), 1)
        disk_total_mb = round(usage.total / (1024 * 1024), 1)
        if disk_free_mb < 500:
            all_ok = False
    except OSError:
        all_ok = False

    return models.FilesystemHealthResponse(
        healthy=all_ok,
        disk_free_mb=disk_free_mb,
        disk_total_mb=disk_total_mb,
        directories=checks,
    )
