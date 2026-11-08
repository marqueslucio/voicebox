"""Model management endpoints."""

import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import models
from ..utils.platform_detect import get_backend_type
from ..services.task_queue import create_background_task
from ..utils.progress import get_progress_manager
from ..utils.tasks import get_task_manager

router = APIRouter()


@router.post("/models/{model_name}/unload")
async def unload_model_by_name(model_name: str):
    """Unload a specific model from memory without deleting it from disk."""
    from ..backends import get_model_config, unload_model_by_config

    config = get_model_config(model_name)
    if not config:
        raise HTTPException(status_code=400, detail=f"Unknown model: {model_name}")

    try:
        was_loaded = await asyncio.to_thread(unload_model_by_config, config)
        if not was_loaded:
            return {"message": f"Model {model_name} is not loaded"}
        return {"message": f"Model {model_name} unloaded successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/models/progress/{model_name}")
async def get_model_progress(model_name: str):
    """Get model download progress via Server-Sent Events."""
    progress_manager = get_progress_manager()

    async def event_generator():
        async for event in progress_manager.subscribe(model_name):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/models/status", response_model=models.ModelStatusListResponse)
async def get_model_status():
    """Get status of all available models."""
    from huggingface_hub import constants as hf_constants

    backend_type = get_backend_type()
    task_manager = get_task_manager()

    # Pending only — an errored task stays in the active list for the
    # error/retry UI, but reporting it as "downloading" here would mask
    # the model's real cache state until the app restarts (issue #925).
    active_download_names = {task.model_name for task in task_manager.get_pending_downloads()}

    try:
        from huggingface_hub import scan_cache_dir

        use_scan_cache = True
    except ImportError:
        use_scan_cache = False

    from ..backends import get_all_model_configs, check_model_cached, check_model_loaded

    registry_configs = get_all_model_configs()
    model_configs = [
        {
            "model_name": cfg.model_name,
            "display_name": cfg.display_name,
            "hf_repo_id": cfg.hf_repo_id,
            "model_size": cfg.model_size,
            "weight_filename": cfg.weight_filename,
            "check_loaded": lambda c=cfg: check_model_loaded(c),
            "check_cached": lambda c=cfg: check_model_cached(c),
        }
        for cfg in registry_configs
    ]

    model_to_repo = {cfg["model_name"]: cfg["hf_repo_id"] for cfg in model_configs}
    active_download_repos = {model_to_repo.get(name) for name in active_download_names if name in model_to_repo}

    cache_info = None
    if use_scan_cache:
        try:
            cache_info = scan_cache_dir()
        except Exception:
            pass

    statuses = []

    for config in model_configs:
        try:
            downloaded = False
            size_mb = None
            loaded = False

            if cache_info:
                repo_id = config["hf_repo_id"]
                for repo in cache_info.repos:
                    if repo.repo_id == repo_id:
                        has_model_weights = False
                        for rev in repo.revisions:
                            for f in rev.files:
                                fname = f.file_name.lower()
                                if (fname == config["weight_filename"] if config["weight_filename"] else
                                        fname.endswith((".safetensors", ".bin", ".pt", ".pth", ".npz"))):
                                    has_model_weights = True
                                    break
                            if has_model_weights:
                                break

                        has_incomplete = False
                        try:
                            cache_dir = hf_constants.HF_HUB_CACHE
                            blobs_dir = Path(cache_dir) / ("models--" + repo_id.replace("/", "--")) / "blobs"
                            if blobs_dir.exists():
                                has_incomplete = any(blobs_dir.glob("*.incomplete"))
                        except Exception:
                            pass

                        if has_model_weights and not has_incomplete:
                            downloaded = True
                            try:
                                total_size = sum(revision.size_on_disk for revision in repo.revisions)
                                size_mb = total_size / (1024 * 1024)
                            except Exception:
                                pass
                        break

            if not downloaded:
                try:
                    cache_dir = hf_constants.HF_HUB_CACHE
                    repo_cache = Path(cache_dir) / ("models--" + config["hf_repo_id"].replace("/", "--"))

                    if repo_cache.exists():
                        blobs_dir = repo_cache / "blobs"
                        has_incomplete = blobs_dir.exists() and any(blobs_dir.glob("*.incomplete"))

                        if not has_incomplete:
                            snapshots_dir = repo_cache / "snapshots"
                            has_model_files = False
                            if snapshots_dir.exists():
                                has_model_files = (any(snapshots_dir.rglob(config["weight_filename"]))
                                    if config["weight_filename"] else (
                                    any(snapshots_dir.rglob("*.bin"))
                                    or any(snapshots_dir.rglob("*.safetensors"))
                                    or any(snapshots_dir.rglob("*.pt"))
                                    or any(snapshots_dir.rglob("*.pth"))
                                    or any(snapshots_dir.rglob("*.npz"))
                                ))

                            if has_model_files:
                                downloaded = True
                                try:
                                    total_size = sum(
                                        f.stat().st_size
                                        for f in repo_cache.rglob("*")
                                        if f.is_file() and not f.name.endswith(".incomplete")
                                    )
                                    size_mb = total_size / (1024 * 1024)
                                except Exception:
                                    pass
                except Exception:
                    pass

            if config["weight_filename"]:
                downloaded = config["check_cached"]()
                if not downloaded:
                    size_mb = None

            try:
                loaded = config["check_loaded"]()
            except Exception:
                loaded = False

            is_downloading = config["hf_repo_id"] in active_download_repos

            if is_downloading:
                downloaded = False
                size_mb = None

            statuses.append(
                models.ModelStatus(
                    model_name=config["model_name"],
                    display_name=config["display_name"],
                    hf_repo_id=config["hf_repo_id"],
                    downloaded=downloaded,
                    downloading=is_downloading,
                    size_mb=size_mb,
                    loaded=loaded,
                )
            )
        except Exception:
            try:
                loaded = config["check_loaded"]()
            except Exception:
                loaded = False

            is_downloading = config["hf_repo_id"] in active_download_repos

            statuses.append(
                models.ModelStatus(
                    model_name=config["model_name"],
                    display_name=config["display_name"],
                    hf_repo_id=config["hf_repo_id"],
                    downloaded=False,
                    downloading=is_downloading,
                    size_mb=None,
                    loaded=loaded,
                )
            )

    return models.ModelStatusListResponse(models=statuses)


@router.post("/models/download")
async def trigger_model_download(request: models.ModelDownloadRequest):
    """Trigger download of a specific model."""
    from ..backends import get_model_config, get_model_load_func

    task_manager = get_task_manager()
    progress_manager = get_progress_manager()

    config = get_model_config(request.model_name)
    if not config:
        raise HTTPException(status_code=400, detail=f"Unknown model: {request.model_name}")

    download_task = task_manager.start_download(request.model_name)
    load_func = get_model_load_func(config, download_task=download_task)

    async def download_in_background():
        try:
            result = load_func()
            if asyncio.iscoroutine(result):
                await result
            task_manager.complete_download(request.model_name, download_task)
        except Exception as e:
            task_manager.error_download(request.model_name, str(e), download_task)

    progress_manager.update_progress(
        model_name=request.model_name,
        current=0,
        total=0,
        filename="Connecting to HuggingFace...",
        status="downloading",
    )

    create_background_task(download_in_background())

    return {"message": f"Model {request.model_name} download started"}


@router.post("/models/download/cancel")
async def cancel_model_download(request: models.ModelDownloadRequest):
    """Cancel or dismiss an errored/stale download task."""
    task_manager = get_task_manager()
    progress_manager = get_progress_manager()

    removed = task_manager.cancel_download(request.model_name)

    progress_removed = False
    with progress_manager._lock:
        if request.model_name in progress_manager._progress:
            del progress_manager._progress[request.model_name]
            progress_removed = True

    if removed or progress_removed:
        return {"message": f"Download task for {request.model_name} cancelled"}
    return {"message": f"No active task found for {request.model_name}"}


@router.delete("/models/{model_name}")
async def delete_model(model_name: str):
    """Delete a downloaded model from the HuggingFace cache."""
    from huggingface_hub import constants as hf_constants
    from ..backends import get_model_config, unload_model_by_config

    config = get_model_config(model_name)
    if not config:
        raise HTTPException(status_code=400, detail=f"Unknown model: {model_name}")

    hf_repo_id = config.hf_repo_id

    try:
        await asyncio.to_thread(unload_model_by_config, config)

        cache_dir = hf_constants.HF_HUB_CACHE
        repo_cache_dir = Path(cache_dir) / ("models--" + hf_repo_id.replace("/", "--"))

        if not repo_cache_dir.exists():
            raise HTTPException(status_code=404, detail=f"Model {model_name} not found in cache")

        try:
            shutil.rmtree(repo_cache_dir)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete model cache directory: {str(e)}")

        return {"message": f"Model {model_name} deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete model: {str(e)}")
