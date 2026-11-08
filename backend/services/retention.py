"""Expire capture audio and history without touching files outside capture storage."""

import asyncio
import logging
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config
from ..database import Capture, CaptureSettings
from ..database import session

logger = logging.getLogger(__name__)


def _capture_name(stored_path: str) -> str | None:
    """Accept only direct children of the configured capture directory."""
    if not stored_path:
        return None
    path = Path(stored_path)
    root = config.get_data_dir() / "captures"
    if path.is_absolute():
        if path.parent != root:
            raise ValueError("Capture audio is outside capture storage")
    else:
        if path.parts and path.parts[0] == "data":
            path = Path(*path.parts[1:])
        path = config.get_data_dir() / path
    if path.parent != root or path.name in ("", ".", ".."):
        raise ValueError("Capture audio is outside capture storage")
    return path.name


def _open_capture_dir():
    # O_NOFOLLOW rejects a substituted symlink to another directory.
    return os.open(config.get_data_dir() / "captures", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _file_size(directory_fd: int, name: str) -> int | None:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Capture audio is not a regular file")
    return info.st_size


def audio_available(stored_path: str) -> bool:
    try:
        name = _capture_name(stored_path)
        if name is None:
            return False
        fd = _open_capture_dir()
        try:
            return _file_size(fd, name) is not None
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return False


def sweep(db, *, now: datetime | None = None, dry_run: bool = False) -> dict[str, int]:
    """Delete eligible records; report actual or, for dry_run, planned counts and bytes."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    settings = db.query(CaptureSettings).filter(CaptureSettings.id == 1).first()
    hours = settings.audio_retention_hours if settings else 24
    days = settings.history_retention_days if settings else 7
    if not isinstance(hours, int) or not 1 <= hours <= 876000 or not isinstance(days, int) or not 1 <= days <= 36500:
        raise ValueError("Invalid capture retention settings")
    audio_cutoff = now - timedelta(hours=hours)
    history_cutoff = now - timedelta(days=days)
    rows = db.query(Capture).all()
    result = {"audio_files": 0, "audio_bytes": 0, "history_entries": 0, "errors": 0}

    # Keep an audio file if any still-live row refers to it.
    protected = set()
    for row in rows:
        if row.created_at is None or (row.created_at > history_cutoff and
                (row.source != "dictation" or row.created_at > audio_cutoff)):
            try:
                protected.add(_capture_name(row.audio_path))
            except ValueError:
                pass

    try:
        fd = _open_capture_dir()
    except FileNotFoundError:
        fd = None
    except OSError:
        logger.exception("Cannot safely open capture directory for retention")
        result["errors"] += 1
        return result

    seen = set()
    try:
        for row in rows:
            if row.created_at is None:
                continue
            history_due = row.created_at <= history_cutoff
            audio_due = row.source == "dictation" and row.created_at <= audio_cutoff
            if not history_due and not audio_due:
                continue
            try:
                name = _capture_name(row.audio_path)
                if name and name not in protected and name not in seen and fd is not None:
                    size = _file_size(fd, name)
                    if size is not None:
                        if not dry_run:
                            os.unlink(name, dir_fd=fd)
                        result["audio_files"] += 1
                        result["audio_bytes"] += size
                    seen.add(name)
                if history_due:
                    if not dry_run:
                        db.delete(row)
                    result["history_entries"] += 1
            except (OSError, ValueError):
                result["errors"] += 1
                logger.exception("Could not safely expire capture %s", row.id)
        if not dry_run:
            try:
                db.commit()
            except Exception:
                db.rollback()
                result["history_entries"] = 0
                result["errors"] += 1
                logger.exception("Could not commit capture retention")
    finally:
        if fd is not None:
            os.close(fd)
    return result


def run_sweep() -> None:
    try:
        with session.SessionLocal() as db:
            result = sweep(db)
        logger.info("Capture retention: %s", result)
    except Exception:
        logger.exception("Capture retention failed")


async def periodic_sweep(stop: asyncio.Event) -> None:
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=3600)
            return
        except TimeoutError:
            await asyncio.to_thread(run_sweep)
