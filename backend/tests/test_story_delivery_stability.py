import asyncio
import io
from datetime import datetime

import numpy as np
import pytest
import soundfile as sf
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from backend import models
from backend.app import app as _app
from backend.database.migrations import run_migrations
from backend.database.models import Base, Generation, GenerationVersion, Story, StoryItem, VoiceProfile
from backend.routes import generations as generation_routes
from backend.routes import history as history_routes
from backend.services import stories
from backend.utils.audio import time_stretch_audio


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def add_profile(db, profile_id="profile", **kwargs):
    profile = VoiceProfile(
        id=profile_id,
        name=profile_id,
        language="de",
        voice_type="cloned",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        **kwargs,
    )
    db.add(profile)
    db.commit()
    return profile


def add_generation(db, profile_id="profile", generation_id="generation", **kwargs):
    generation = Generation(
        id=generation_id,
        profile_id=profile_id,
        text="Text stays unchanged",
        language="de",
        audio_path="default.wav",
        duration=10.0,
        engine="qwen",
        model_size="1.7B",
        status="completed",
        source="manual",
        created_at=datetime.utcnow(),
        **kwargs,
    )
    db.add(generation)
    db.commit()
    return generation


def test_delivery_validation_and_story_roundtrip(db):
    add_profile(db)
    settings = models.DeliverySettings(enabled=True, profile_id="profile", gap_ms=321)
    created = asyncio.run(stories.create_story(models.StoryCreate(name="Story", delivery_settings=settings), db))
    assert created.delivery_settings == settings

    updated = asyncio.run(stories.update_story(created.id, models.StoryCreate(name="Renamed"), db))
    assert updated.delivery_settings == settings
    assert asyncio.run(stories.get_story(created.id, db)).delivery_settings == settings

    with pytest.raises(ValidationError):
        models.DeliverySettings(enabled=True, profile_id="profile", engine="qwen", energy=0.8)
    with pytest.raises(ValidationError):
        models.DeliverySettings(
            enabled=True,
            profile_id="profile",
            engine="qwen_custom_voice",
            model_size="0.6B",
            warmth=0.8,
        )
    with pytest.raises(ValueError, match="does not support"):
        asyncio.run(stories.update_story(
            created.id,
            models.StoryCreate(
                name="Invalid",
                delivery_settings=models.DeliverySettings(
                    enabled=True,
                    profile_id="profile",
                    engine="qwen_custom_voice",
                ),
            ),
            db,
        ))


def test_reorder_uses_exact_item_ids_pinned_duration_and_trims(db):
    add_profile(db)
    generation = add_generation(db)
    story = Story(id="story", name="Story", delivery_settings={"gap_ms": 350})
    pinned = GenerationVersion(
        id="pinned",
        generation_id=generation.id,
        label="old",
        audio_path="pinned.wav",
        duration=6.0,
        is_default=False,
        created_at=datetime.utcnow(),
    )
    current = GenerationVersion(
        id="current",
        generation_id=generation.id,
        label="current",
        audio_path="default.wav",
        duration=8.0,
        is_default=True,
        created_at=datetime.utcnow(),
    )
    first = StoryItem(
        id="first", story_id=story.id, generation_id=generation.id, version_id=pinned.id,
        start_time_ms=0, trim_start_ms=500, trim_end_ms=500, track=2, created_at=datetime.utcnow(),
    )
    second = StoryItem(
        id="second", story_id=story.id, generation_id=generation.id,
        start_time_ms=0, trim_start_ms=1000, trim_end_ms=1000, track=1, created_at=datetime.utcnow(),
    )
    db.add_all([story, pinned, current, first, second])
    db.commit()

    reordered = asyncio.run(stories.reorder_story_items(story.id, [second.id, first.id], db))
    assert [(item.id, item.start_time_ms, item.track) for item in reordered] == [
        ("second", 0, 1),
        ("first", 6350, 2),
    ]
    assert reordered[1].duration == 6.0
    assert asyncio.run(stories.reorder_story_items(story.id, [first.id], db)) is None
    assert asyncio.run(stories.reorder_story_items(
        story.id, None, db, generation_ids=[generation.id, generation.id]
    )) is None


def test_history_detail_includes_delivery_snapshot_and_versions(db):
    add_profile(db)
    settings = models.DeliverySettings(enabled=True, profile_id="profile", speed=1.05)
    generation = add_generation(db, delivery_settings=settings.model_dump())
    db.add_all([
        GenerationVersion(
            id="old", generation_id=generation.id, label="old", audio_path="old.wav",
            duration=9.0, is_default=False, created_at=datetime.utcnow(),
        ),
        GenerationVersion(
            id="current", generation_id=generation.id, label="current", audio_path="current.wav",
            duration=10.0, is_default=True, created_at=datetime.utcnow(),
        ),
    ])
    db.commit()

    response = asyncio.run(history_routes.get_generation(generation.id, db))
    assert response.delivery_settings.speed == 1.05
    assert [version.id for version in response.versions] == ["old", "current"]
    assert response.active_version_id == "current"


def test_pinned_version_drives_trim_split_and_export(db, tmp_path, monkeypatch):
    add_profile(db)
    generation = add_generation(db)
    story = Story(id="story", name="Story")
    version = GenerationVersion(
        id="pinned", generation_id=generation.id, label="old", audio_path="pinned.wav",
        duration=2.0, is_default=False, created_at=datetime.utcnow(),
    )
    item = StoryItem(
        id="item", story_id=story.id, generation_id=generation.id, version_id=version.id,
        start_time_ms=0, track=0, trim_start_ms=500, trim_end_ms=500, created_at=datetime.utcnow(),
    )
    db.add_all([story, version, item])
    db.commit()

    assert asyncio.run(stories.trim_story_item(
        story.id, item.id, models.StoryItemTrim(trim_start_ms=1000, trim_end_ms=1000), db
    )) is None
    split = asyncio.run(stories.split_story_item(
        story.id, item.id, models.StoryItemSplit(split_time_ms=500), db
    ))
    assert [clip.duration for clip in split] == [2.0, 2.0]

    db.query(StoryItem).filter(StoryItem.id != item.id).delete()
    item.trim_start_ms = 500
    item.trim_end_ms = 500
    db.commit()
    path = tmp_path / "pinned.wav"
    sf.write(path, np.zeros(48_000, dtype=np.float32), 24_000)
    monkeypatch.setattr(stories.config, "resolve_storage_path", lambda _path: path)
    exported = asyncio.run(stories.export_story_audio(story.id, db))
    audio, sample_rate = sf.read(io.BytesIO(exported))
    assert sample_rate == 24_000
    assert len(audio) == 24_000


def test_time_stretch_changes_duration_without_shifting_pitch():
    sample_rate = 24_000
    source = np.sin(2 * np.pi * 440 * np.arange(sample_rate * 2) / sample_rate).astype(np.float32)
    stretched = time_stretch_audio(source, 1.25)
    assert len(stretched) == pytest.approx(len(source) / 1.25, abs=2)
    peak_hz = np.argmax(np.abs(np.fft.rfft(stretched))) * sample_rate / len(stretched)
    assert peak_hz == pytest.approx(440, abs=3)
    assert len(time_stretch_audio(source, 99)) == pytest.approx(len(stretched), abs=2)
    with pytest.raises(ValueError, match="finite"):
        time_stretch_audio(source, float("nan"))


@pytest.mark.asyncio
async def test_story_request_and_retry_regenerate_use_delivery_snapshot(db, monkeypatch):
    add_profile(db)
    settings = models.DeliverySettings(
        enabled=True,
        profile_id="profile",
        language="de",
        speed=0.8,
        seed=42,
    )
    story = Story(id="story", name="Story", delivery_settings=settings.model_dump())
    generation = add_generation(db, delivery_settings=settings.model_dump())
    item = StoryItem(
        id="item", story_id=story.id, generation_id=generation.id,
        start_time_ms=0, track=0, created_at=datetime.utcnow(),
    )
    db.add_all([story, item])
    db.commit()

    request = models.GenerationRequest(
        profile_id="ignored",
        story_id=story.id,
        text="Exact input",
        language="en",
        seed=999,
        engine="chatterbox",
    )
    context = await generation_routes._resolve_request_context(request, db)
    assert (context["profile_id"], context["language"], context["seed"], context["speed"]) == (
        "profile", "de", 42, 0.8
    )

    calls = []
    monkeypatch.setattr(generation_routes, "run_generation", lambda **kwargs: calls.append(kwargs) or object())
    monkeypatch.setattr(generation_routes, "enqueue_generation", lambda _id, _job: None)
    monkeypatch.setattr(
        generation_routes,
        "get_task_manager",
        lambda: type("Tasks", (), {"start_generation": lambda self, **kwargs: None})(),
    )

    await generation_routes.regenerate_generation(
        generation.id,
        models.RegenerateRequest(story_id=story.id),
        db,
    )
    assert calls[-1]["seed"] == 42
    assert calls[-1]["speed"] == 0.8
    assert generation.text == "Text stays unchanged"

    story.delivery_settings = {**settings.model_dump(), "engine": "chatterbox"}
    generation.status = "completed"
    db.commit()
    with pytest.raises(HTTPException, match="engine differs"):
        await generation_routes.regenerate_generation(
            generation.id,
            models.RegenerateRequest(story_id=story.id),
            db,
        )

    generation.status = "failed"
    db.commit()
    await generation_routes.retry_generation(generation.id, db)
    assert calls[-1]["seed"] == 42
    assert calls[-1]["speed"] == 0.8


def test_additive_migrations(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.sqlite'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE stories (id VARCHAR PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE generations (id VARCHAR PRIMARY KEY, audio_path VARCHAR)")
        connection.exec_driver_sql("CREATE TABLE generation_versions (id VARCHAR PRIMARY KEY, audio_path VARCHAR)")
    run_migrations(engine)
    inspector = inspect(engine)
    assert "delivery_settings" in {column["name"] for column in inspector.get_columns("stories")}
    assert "delivery_settings" in {column["name"] for column in inspector.get_columns("generations")}
    assert "duration" in {column["name"] for column in inspector.get_columns("generation_versions")}
