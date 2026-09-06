import asyncio
import threading
from types import SimpleNamespace

import pytest

import backend.app  # noqa: F401 - initialize routes through the production app order
from backend import models
from backend.backends.qwen_llm_backend import MLXQwenLLMBackend
from backend.routes import generations, profiles
from backend.services import personality


@pytest.mark.asyncio
async def test_personality_uses_requested_model_and_output_language(monkeypatch):
    calls = []

    class FakeBackend:
        model_size = "0.6B"

        async def generate(self, **kwargs):
            calls.append(kwargs)
            return "Guten Tag."

    monkeypatch.setattr(personality.llm_service, "get_llm_model", FakeBackend)

    result = await personality.compose_as_profile("A concise librarian.", model_size="1.7B", language="de")

    assert result.model_size == "1.7B"
    assert calls[0]["model_size"] == "1.7B"
    assert "Write only in german." in calls[0]["system"]


@pytest.mark.asyncio
async def test_mlx_llm_keeps_model_switch_and_generation_on_one_thread(monkeypatch):
    backend = MLXQwenLLMBackend()
    events = []

    def fake_load(model_size):
        backend.model = object()
        backend._current_model_size = model_size
        backend.model_size = model_size
        events.append(("load", model_size, threading.get_ident()))

    def fake_generate(prompt, *_args):
        events.append(("generate", backend._current_model_size, threading.get_ident()))
        return f"{prompt}:{backend._current_model_size}"

    monkeypatch.setattr(backend, "_load_model_sync", fake_load)
    monkeypatch.setattr(backend, "_generate_sync", fake_generate)

    try:
        outputs = await asyncio.gather(
            backend.generate("first", model_size="1.7B"),
            backend.generate("second", model_size="4B"),
        )
    finally:
        backend._executor.shutdown(wait=True)

    assert outputs == ["first:1.7B", "second:4B"]
    assert len({thread_id for _, _, thread_id in events}) == 1


@pytest.mark.asyncio
async def test_compose_route_uses_saved_llm_model_and_profile_language(monkeypatch):
    profile = SimpleNamespace(personality="A concise librarian.", language="de")
    db = SimpleNamespace(
        query=lambda *_args: SimpleNamespace(filter_by=lambda **_kwargs: SimpleNamespace(first=lambda: profile))
    )
    captured = {}

    monkeypatch.setattr(
        profiles.settings,
        "get_capture_settings",
        lambda _db: SimpleNamespace(llm_model="1.7B"),
    )

    async def fake_compose(personality_text, **kwargs):
        captured.update(personality=personality_text, **kwargs)
        return personality.PersonalityResult("Guten Tag.", kwargs["model_size"])

    monkeypatch.setattr(profiles.personality, "compose_as_profile", fake_compose)

    response = await profiles.compose_in_character("profile-1", db)

    assert response.text == "Guten Tag."
    assert captured == {
        "personality": "A concise librarian.",
        "model_size": "1.7B",
        "language": "de",
    }


@pytest.mark.asyncio
async def test_generation_rewrite_uses_saved_llm_model_and_resolved_language(monkeypatch):
    profile = SimpleNamespace(personality="A concise librarian.")
    context = {
        "profile": profile,
        "profile_id": "profile-1",
        "engine": "qwen",
        "model_size": "1.7B",
        "language": "de",
        "seed": None,
        "instruct": None,
        "speed": 1.0,
        "snapshot": None,
        "temperature": None,
    }
    captured = {}

    async def fake_context(_data, _db):
        return context

    async def fake_rewrite(personality_text, user_text, **kwargs):
        captured.update(personality=personality_text, user_text=user_text, **kwargs)
        return personality.PersonalityResult("Heute gehe ich raus.", kwargs["model_size"])

    async def fake_create_generation(**kwargs):
        return SimpleNamespace(**kwargs)

    async def fake_run_generation(**_kwargs):
        return None

    class FakeTaskManager:
        def start_generation(self, **_kwargs):
            pass

    def fake_enqueue(_generation_id, job):
        job.close()

    monkeypatch.setattr(generations, "_resolve_request_context", fake_context)
    monkeypatch.setattr(generations.personality, "rewrite_as_profile", fake_rewrite)
    monkeypatch.setattr(
        generations.settings,
        "get_capture_settings",
        lambda _db: SimpleNamespace(llm_model="1.7B"),
    )
    monkeypatch.setattr(generations.history, "create_generation", fake_create_generation)
    monkeypatch.setattr(generations, "run_generation", fake_run_generation)
    monkeypatch.setattr(generations, "enqueue_generation", fake_enqueue)
    monkeypatch.setattr(generations, "get_task_manager", FakeTaskManager)

    response = await generations.generate_speech(
        models.GenerationRequest(
            profile_id="profile-1",
            text="Heute gehe ich spazieren.",
            language="de",
            personality=True,
            effects_chain=[],
        ),
        db=SimpleNamespace(),
    )

    assert response.text == "Heute gehe ich raus."
    assert captured == {
        "personality": "A concise librarian.",
        "user_text": "Heute gehe ich spazieren.",
        "model_size": "1.7B",
        "language": "de",
    }
