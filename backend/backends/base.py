"""
Shared cache and download-progress utilities for speech recognition.
"""

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


from ..utils.progress import get_progress_manager
from ..utils.hf_progress import HFProgressTracker, create_hf_progress_callback
from ..utils.tasks import get_task_manager

logger = logging.getLogger(__name__)


def is_model_cached(
    hf_repo: str,
    *,
    weight_extensions: tuple[str, ...] = (".safetensors", ".bin"),
    required_files: Optional[list[str]] = None,
) -> bool:
    """
    Check if a HuggingFace model is fully cached locally.

    Args:
        hf_repo: HuggingFace repo ID.
        weight_extensions: File extensions that count as model weights.
        required_files: If set, check that these specific filenames exist
                        in snapshots instead of checking by extension.

    Returns:
        True if model is fully cached, False if missing or incomplete.
    """
    try:
        from huggingface_hub import constants as hf_constants

        repo_cache = Path(hf_constants.HF_HUB_CACHE) / ("models--" + hf_repo.replace("/", "--"))

        if not repo_cache.exists():
            return False

        # Incomplete blobs mean a download is still in progress
        blobs_dir = repo_cache / "blobs"
        if blobs_dir.exists() and any(blobs_dir.glob("*.incomplete")):
            logger.debug(f"Found .incomplete files for {hf_repo}")
            return False

        snapshots_dir = repo_cache / "snapshots"
        if not snapshots_dir.exists():
            return False

        if required_files:
            # Check that every required filename exists somewhere in snapshots
            for fname in required_files:
                if not any(snapshots_dir.rglob(fname)):
                    return False
            return True

        # Check that at least one weight file exists
        for ext in weight_extensions:
            if any(snapshots_dir.rglob(f"*{ext}")):
                return True

        logger.debug(f"No model weights found for {hf_repo}")
        return False

    except Exception as e:
        logger.warning(f"Error checking cache for {hf_repo}: {e}")
        return False


class DownloadCancelled(RuntimeError):
    """A transfer was explicitly cancelled by its user."""


@contextmanager
def model_load_progress(
    model_name: str,
    is_cached: bool,
    filter_non_downloads: Optional[bool] = None,
    download_task=None,
):
    """
    Context manager for model loading with HF download progress tracking.

    Handles the tqdm patching, progress_manager/task_manager lifecycle,
    and error reporting that every backend duplicates.

    Args:
        model_name: Progress tracking key (e.g. "whisper-base").
        is_cached: Whether the model is already downloaded.
        filter_non_downloads: Whether to filter non-download tqdm bars.
                              Defaults to `is_cached`.

    Yields:
        A cancellation checker for callers to invoke before creating native handles.
        The caller loads the model inside the block; tqdm is restored on exit.

    Usage:
        with model_load_progress("qwen-tts-1.7B", is_cached) as ctx:
            self.model = SomeModel.from_pretrained(...)
    """
    if filter_non_downloads is None:
        filter_non_downloads = is_cached

    progress_manager = get_progress_manager()
    task_manager = get_task_manager()

    if download_task is None and (not is_cached or task_manager.is_download_active(model_name)):
        download_task = task_manager.start_download(model_name)

    def check_cancelled():
        if download_task is not None and download_task.cancelled.is_set():
            raise DownloadCancelled("Model download cancelled")

    check_cancelled()
    report_progress = create_hf_progress_callback(model_name, progress_manager)

    def progress_callback(current, total, filename=""):
        check_cancelled()
        report_progress(current, total, filename)

    tracker = HFProgressTracker(progress_callback, filter_non_downloads=filter_non_downloads)

    tracker_context = tracker.patch_download()
    tracker_context.__enter__()

    if not is_cached:
        progress_manager.update_progress(
            model_name=model_name,
            current=0,
            total=0,
            filename="Connecting to HuggingFace...",
            status="downloading",
        )

    try:
        check_cancelled()
        yield check_cancelled
        check_cancelled()
    except DownloadCancelled:
        raise
    except Exception as e:
        # Report error to both managers
        progress_manager.mark_error(model_name, str(e))
        task_manager.error_download(model_name, str(e), download_task)
        raise
    else:
        # Only mark complete if we were tracking a download
        if not is_cached:
            progress_manager.mark_complete(model_name)
            task_manager.complete_download(model_name, download_task)
    finally:
        tracker_context.__exit__(None, None, None)
