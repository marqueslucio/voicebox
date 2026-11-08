"""Legacy capture settings and data remain usable in the dictation-only API."""

import io

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from backend import config
from backend.app import app
from backend.database import Capture, CaptureSettings
from backend.database import session
from backend.services import captures


def test_legacy_data_and_dictation_contract(tmp_path, monkeypatch):
    config.set_data_dir(tmp_path)
    with TestClient(app) as client:
        db = session.SessionLocal()
        db.add(CaptureSettings(id=1, auto_refine=True, stt_model="turbo", language="de"))
        db.add(Capture(id="old", audio_path="captures/old.wav", source="dictation",
                       transcript_raw="old raw", transcript_refined="old refined"))
        db.commit()
        db.close()

        class FakeSTT:
            async def transcribe(self, path, language, model):
                assert language == "de" and model == "turbo"
                return "new transcript"

        monkeypatch.setattr(captures, "get_whisper_model", lambda: FakeSTT())
        audio = io.BytesIO()
        sf.write(audio, np.ones(1600, dtype=np.float32) * 0.1, 16000, format="WAV")
        response = client.post("/captures", files={"file": ("test.wav", audio.getvalue(), "audio/wav")})
        assert response.status_code == 200, response.text
        assert response.json()["auto_refine"] is False
        assert response.json()["transcript_raw"] == "new transcript"
        assert client.get("/captures/old").json()["transcript_refined"] == "old refined"
        assert client.get("/capture/readiness").json().keys() == {"stt"}
        assert {m["model_name"] for m in client.get("/models/status").json()["models"]} == {
            "whisper-base", "whisper-small", "whisper-medium", "whisper-large", "whisper-turbo", "parakeet-tdt-0.6b-v3"
        }
        assert client.post("/captures/old/refine", json={}).status_code == 404
        assert client.post("/generate", json={}).status_code == 404
        db = session.SessionLocal()
        assert db.query(CaptureSettings).one().auto_refine is True
        db.close()
