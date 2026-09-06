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
from backend.database.migrations import _migrate_generations
from backend.routes import generations, history


def test_controls_validation_and_migration():
    for values in ({'temperature': 0.2}, {'temperature': float('nan')}):
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
