"""
Task tracking for active downloads.
"""

from typing import Optional, Dict, List
from datetime import datetime
from dataclasses import dataclass, field
from threading import Event


@dataclass
class DownloadTask:
    """Represents an active download task."""
    model_name: str
    status: str = "downloading"  # downloading, extracting, complete, error
    started_at: datetime = field(default_factory=datetime.utcnow)
    error: Optional[str] = None
    cancelled: Event = field(default_factory=Event, repr=False)


class TaskManager:
    """Manages active downloads."""
    
    def __init__(self):
        self._active_downloads: Dict[str, DownloadTask] = {}
    
    def start_download(self, model_name: str) -> DownloadTask:
        """Reuse a pending task so loaders keep the endpoint's cancellation token."""
        current = self._active_downloads.get(model_name)
        if current and current.status in ("downloading", "extracting"):
            return current
        self._active_downloads[model_name] = DownloadTask(
            model_name=model_name,
            status="downloading",
        )
        return self._active_downloads[model_name]
    
    def complete_download(self, model_name: str, task: Optional[DownloadTask] = None) -> None:
        """Mark a download as complete."""
        if model_name in self._active_downloads and (task is None or self._active_downloads[model_name] is task):
            del self._active_downloads[model_name]
    
    def error_download(self, model_name: str, error: str, task: Optional[DownloadTask] = None) -> None:
        """Mark a download as failed."""
        if model_name in self._active_downloads and (task is None or self._active_downloads[model_name] is task):
            self._active_downloads[model_name].status = "error"
            self._active_downloads[model_name].error = error
    
    def get_active_downloads(self) -> List[DownloadTask]:
        """Get all active downloads."""
        return list(self._active_downloads.values())

    def get_pending_downloads(self) -> List[DownloadTask]:
        """Get downloads that are still in flight.

        Excludes errored tasks, which stay in the active list so the
        error/retry UI can show them but must not be reported as
        "downloading" by /models/status.
        """
        return [
            task
            for task in self._active_downloads.values()
            if task.status in ("downloading", "extracting")
        ]
    
    def cancel_download(self, model_name: str) -> bool:
        """Signal the running transfer, then dismiss its UI task."""
        task = self._active_downloads.pop(model_name, None)
        if task:
            task.cancelled.set()
        return task is not None

    def clear_all(self) -> None:
        """Clear all download tasks."""
        for task in self._active_downloads.values():
            task.cancelled.set()
        self._active_downloads.clear()

    def is_download_active(self, model_name: str) -> bool:
        """Check if a download is active."""
        return model_name in self._active_downloads
    
# Global task manager instance
_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """Get or create the global task manager."""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
