"""Retention boundaries, file safety, legacy schema, and settings validation."""

import os
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend import config
from backend.app import app
from backend.database import Capture, CaptureSettings, session
from backend.services.retention import audio_available, sweep

NOW = datetime(2026, 11, 8, 12)


@pytest.fixture
def db(tmp_path):
    config.set_data_dir(tmp_path)
    session.init_db()
    with session.SessionLocal() as db:
        db.add(CaptureSettings(id=1, audio_retention_hours=24, history_retention_days=7))
        db.commit()
        yield db


def add(db, name, age, *, source="dictation", path=None):
    row = Capture(id=name, audio_path=path if path is not None else f"captures/{name}.wav",
                  source=source, transcript_raw="", created_at=NOW - age)
    db.add(row)
    db.commit()
    return row


def test_boundaries_dry_run_and_idempotence(db, tmp_path):
    root = tmp_path / "captures"
    root.mkdir()
    for name in ("new", "audio_due", "history_due", "file", "file_old"):
        (root / f"{name}.wav").write_bytes(name.encode())
    add(db, "new", timedelta(hours=24) - timedelta(microseconds=1))
    add(db, "audio_due", timedelta(hours=24))
    add(db, "history_due", timedelta(days=7))
    add(db, "file", timedelta(days=2), source="file")
    add(db, "file_old", timedelta(days=7), source="file")
    preview = sweep(db, now=NOW, dry_run=True)
    assert preview == {"audio_files": 3, "audio_bytes": 28, "history_entries": 2, "errors": 0}
    assert len(list(root.iterdir())) == 5 and db.query(Capture).count() == 5
    assert sweep(db, now=NOW) == preview
    assert {p.name for p in root.iterdir()} == {"new.wav", "file.wav"}
    assert {r.id for r in db.query(Capture)} == {"new", "audio_due", "file"}
    assert not audio_available("captures/audio_due.wav")
    assert sweep(db, now=NOW) == {"audio_files": 0, "audio_bytes": 0, "history_entries": 0, "errors": 0}


def test_symlinks_escapes_and_missing_audio(db, tmp_path):
    root = tmp_path / "captures"
    root.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    (root / "link.wav").symlink_to(outside)
    (root / "internal.wav").write_bytes(b"internal")
    (root / "inner-link.wav").symlink_to(root / "internal.wav")
    add(db, "link", timedelta(days=8), path="captures/link.wav")
    add(db, "inner-link", timedelta(days=8), path="captures/inner-link.wav")
    add(db, "escape", timedelta(days=8), path="captures/../outside.wav")
    add(db, "external", timedelta(days=8), path=str(outside))
    add(db, "missing", timedelta(days=8))
    result = sweep(db, now=NOW)
    assert result["errors"] == 4 and result["history_entries"] == 1
    assert outside.read_bytes() == b"outside"
    assert (root / "internal.wav").read_bytes() == b"internal"
    assert {r.id for r in db.query(Capture)} == {"link", "inner-link", "escape", "external"}
    assert not audio_available("captures/link.wav")


def test_symlink_root_is_never_followed(db, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "old.wav").write_bytes(b"protected")
    (tmp_path / "captures").symlink_to(outside, target_is_directory=True)
    add(db, "old", timedelta(days=8))
    assert sweep(db, now=NOW)["errors"] == 1
    assert db.query(Capture).count() == 1
    assert (outside / "old.wav").read_bytes() == b"protected"


def test_unlink_failure_preserves_history_for_retry(db, tmp_path, monkeypatch):
    root = tmp_path / "captures"
    root.mkdir()
    (root / "old.wav").write_bytes(b"data")
    add(db, "old", timedelta(days=8))
    original = os.unlink
    def fail(name, *, dir_fd=None):
        if name == "old.wav":
            raise PermissionError("denied")
        return original(name, dir_fd=dir_fd)
    monkeypatch.setattr(os, "unlink", fail)
    assert sweep(db, now=NOW)["errors"] == 1
    assert db.query(Capture).count() == 1
    monkeypatch.setattr(os, "unlink", original)
    assert sweep(db, now=NOW)["history_entries"] == 1
    assert not (root / "old.wav").exists()


def test_shared_audio_is_preserved_for_live_entry(db, tmp_path):
    root = tmp_path / "captures"
    root.mkdir()
    (root / "shared.wav").write_bytes(b"shared")
    add(db, "old", timedelta(days=8), path="captures/shared.wav")
    add(db, "new", timedelta(hours=1), path="captures/shared.wav")
    assert sweep(db, now=NOW)["history_entries"] == 1
    assert (root / "shared.wav").exists()


def test_settings_validation_and_persistence(tmp_path):
    config.set_data_dir(tmp_path)
    with TestClient(app) as client:
        assert client.get("/settings/captures").json()["audio_retention_hours"] == 24
        for patch in ({"audio_retention_hours": 0}, {"history_retention_days": -1},
                      {"history_retention_days": 1.5}, {"audio_retention_hours": True},
                      {"audio_retention_hours": 876001}, {"history_retention_days": 36501}):
            assert client.put("/settings/captures", json=patch).status_code == 422
        response = client.put("/settings/captures", json={"audio_retention_hours": 1, "history_retention_days": 3})
        assert response.status_code == 200
        assert client.get("/settings/captures").json()["history_retention_days"] == 3


def test_existing_settings_are_migrated_without_changing_values(tmp_path):
    config.set_data_dir(tmp_path)
    session.init_db()
    with session.SessionLocal() as db:
        db.add(CaptureSettings(id=1, stt_model="base", language="de"))
        db.commit()
        db.execute(text("ALTER TABLE capture_settings DROP COLUMN audio_retention_hours"))
        db.execute(text("ALTER TABLE capture_settings DROP COLUMN history_retention_days"))
        db.commit()
    session.init_db()
    with session.SessionLocal() as db:
        settings = db.query(CaptureSettings).one()
        assert (settings.stt_model, settings.language, settings.audio_retention_hours, settings.history_retention_days) == ("base", "de", 24, 7)


def test_startup_sweeps_and_keeps_transcript(tmp_path):
    config.set_data_dir(tmp_path)
    session.init_db()
    root = tmp_path / "captures"
    root.mkdir()
    (root / "startup.wav").write_bytes(b"audio")
    with session.SessionLocal() as db:
        db.add(Capture(id="startup", audio_path="captures/startup.wav", source="dictation",
                       transcript_raw="keep text", created_at=datetime.utcnow() - timedelta(days=2)))
        db.commit()
    with TestClient(app) as client:
        entry = client.get("/captures/startup").json()
        assert entry["transcript_raw"] == "keep text"
        assert entry["audio_available"] is False
    assert not (root / "startup.wav").exists()


def test_preview_without_settings_does_not_create_row(tmp_path):
    config.set_data_dir(tmp_path)
    session.init_db()
    with session.SessionLocal() as db:
        assert sweep(db, now=NOW, dry_run=True)["history_entries"] == 0
        assert db.query(CaptureSettings).count() == 0


def test_invalid_stored_settings_do_not_delete_data(db, tmp_path):
    root = tmp_path / "captures"
    root.mkdir()
    (root / "old.wav").write_bytes(b"audio")
    add(db, "old", timedelta(days=8))
    db.query(CaptureSettings).one().history_retention_days = 0
    db.commit()
    with pytest.raises(ValueError, match="Invalid capture retention"):
        sweep(db, now=NOW)
    assert db.query(Capture).count() == 1
    assert (root / "old.wav").exists()


def test_commit_failure_retains_text_and_retries(db, tmp_path, monkeypatch):
    root = tmp_path / "captures"
    root.mkdir()
    (root / "old.wav").write_bytes(b"audio")
    add(db, "old", timedelta(days=8))
    commit = db.commit
    def fail_commit():
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(db, "commit", fail_commit)
    result = sweep(db, now=NOW)
    assert result["audio_files"] == 1
    assert result["history_entries"] == 0 and result["errors"] == 1
    assert db.query(Capture).count() == 1
    monkeypatch.setattr(db, "commit", commit)
    assert sweep(db, now=NOW)["history_entries"] == 1


@pytest.mark.asyncio
async def test_periodic_sweep_runs_after_interval_and_stops(monkeypatch):
    from backend.services import retention

    calls = []
    async def wait_once(awaitable, timeout):
        awaitable.close()
        assert timeout == 3600
        if not calls:
            raise TimeoutError
        return True
    async def run_in_thread(func):
        calls.append(func)
    monkeypatch.setattr(retention.asyncio, "wait_for", wait_once)
    monkeypatch.setattr(retention.asyncio, "to_thread", run_in_thread)
    await retention.periodic_sweep(retention.asyncio.Event())
    assert calls == [retention.run_sweep]
