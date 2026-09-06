"""Generation controls survive persistence, retry and new takes."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import backend.app  # Initialize routes in production import order.
from backend import models
from backend.database.models import Base, VoiceProfile, Generation
from backend.database.migrations import _migrate_generations, _migrate_profiles
from backend.routes import generations, history
from backend.services import profiles
from backend import config


def test_controls_validation_and_migration():
    for values in ({'temperature': 0.2}, {'temperature': 1.36}, {'temperature': float('nan')}):
        with pytest.raises(ValidationError):
            models.GenerationRequest(profile_id='voice', text='Hallo.', **values)
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE generations (id TEXT, text TEXT)'))
        conn.execute(text("INSERT INTO generations VALUES ('old', 'Unverändert.')"))
    for _ in range(2):
        _migrate_generations(engine, inspect(engine), {'generations'})
    with engine.connect() as conn:
        assert conn.execute(text('SELECT id, text, temperature FROM generations')).one() == (
            'old', 'Unverändert.', None,
        )


def test_generate_retry_and_regenerate_keep_controls(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(VoiceProfile(id='voice', name='Test', language='de', voice_type='cloned'))
        db.commit()
        run = AsyncMock()
        monkeypatch.setattr(generations, 'run_generation', run)
        monkeypatch.setattr(generations, 'enqueue_generation', lambda _id, job: job.close())
        request = models.GenerationRequest(
            profile_id='voice', text='Hallo. Wie geht es dir?', language='de',
            temperature=0.65,
        )
        result = asyncio.run(generations.generate_speech(request, db))
        row = db.get(Generation, result.id)
        detail = asyncio.run(history.get_generation(row.id, db))
        assert detail.temperature == 0.65
        assert result.temperature == row.temperature == 0.65
        assert run.call_args.kwargs['temperature'] == 0.65
        row.status = 'failed'
        db.commit()
        asyncio.run(generations.retry_generation(row.id, db))
        assert run.call_args.kwargs['temperature'] == 0.65
        row.status = 'completed'
        db.commit()
        asyncio.run(generations.regenerate_generation(row.id, None, db))
        assert run.call_args.kwargs['temperature'] == 0.65


def test_voice_default_validation_and_migration():
    for level in (-1, 11, 1.5, True):
        with pytest.raises(ValidationError):
            models.VoiceProfileCreate(name='Voice', default_voice_variation=level)
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE profiles (id TEXT, name TEXT)'))
        conn.execute(text("INSERT INTO profiles VALUES ('old', 'Unverändert')"))
    for _ in range(2):
        _migrate_profiles(engine, inspect(engine), {'profiles'})
    with engine.connect() as conn:
        assert conn.execute(text('SELECT id, name, default_voice_variation FROM profiles')).one() == (
            'old', 'Unverändert', 6,
        )


def test_voice_default_is_saved_applied_and_overridable(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'get_profiles_dir', lambda: tmp_path / 'profiles')
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        voice = asyncio.run(profiles.create_profile(
            models.VoiceProfileCreate(name='Voice', default_voice_variation=0), db,
        ))
        assert voice.default_voice_variation == 0
        # Older clients updating a name must not silently reset the saved level.
        voice = asyncio.run(profiles.update_profile(
            voice.id, models.VoiceProfileCreate(name='Renamed'), db,
        ))
        assert voice.default_voice_variation == 0
        request = models.GenerationRequest(profile_id=voice.id, text='Hallo.')
        context = asyncio.run(generations._resolve_request_context(request, db))
        assert context['temperature'] == 0.3
        request.temperature = 0.65
        assert asyncio.run(generations._resolve_request_context(request, db))['temperature'] == 0.65
        voice = asyncio.run(profiles.update_profile(
            voice.id, models.VoiceProfileCreate(name='Renamed', default_voice_variation=10), db,
        ))
        assert voice.default_voice_variation == 10
        request.temperature = None
        assert asyncio.run(generations._resolve_request_context(request, db))['temperature'] == 1.35
        request.engine = 'luxtts'
        assert asyncio.run(generations._resolve_request_context(request, db))['temperature'] is None
