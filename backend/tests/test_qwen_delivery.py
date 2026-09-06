"""Focused regression coverage for Qwen delivery controls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from backend.backends.mlx_backend import MLXTTSBackend
from backend.backends.pytorch_backend import PyTorchTTSBackend
from backend.utils.chunked_tts import generate_chunked


@pytest.mark.asyncio
async def test_default_chunked_call_does_not_send_optional_kwargs():
    class PositionalOnlyBackend:
        async def generate(self, text, voice_prompt, language, seed, instruct):
            assert (text, voice_prompt, language, seed, instruct) == (
                "Hello. Again.",
                {},
                "en",
                7,
                None,
            )
            return np.ones(4, dtype=np.float32), 24000

    audio, sample_rate = await generate_chunked(
        PositionalOnlyBackend(), "Hello. Again.", {}, seed=7
    )

    assert sample_rate == 24000
    assert audio.tolist() == [1.0] * 4


@pytest.mark.asyncio
async def test_temperature_is_forwarded_through_runaway_retry():
    class RetryBackend:
        def __init__(self):
            self.calls = []

        async def generate(
            self, text, voice_prompt, language, seed, instruct, *, temperature
        ):
            self.calls.append((text, temperature))
            return np.array([len(text) > 200], dtype=np.float32), 1000

    backend = RetryBackend()
    text = f"{'A' * 119}. {'B' * 119}."

    await generate_chunked(
        backend,
        text,
        {},
        temperature=0.65,
        runaway_detector=lambda audio, _sample_rate: bool(audio[0]),
    )

    assert [temperature for _, temperature in backend.calls] == [0.65] * 3


@pytest.mark.asyncio
async def test_mlx_qwen_uses_temperature_and_never_substitutes_a_voice(tmp_path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"wav")

    class Model:
        def __init__(self):
            self.calls = []

        def generate(self, text, **kwargs):
            self.calls.append((text, kwargs))
            yield SimpleNamespace(audio=np.ones(2), sample_rate=24000)

    backend = MLXTTSBackend()
    backend.model = Model()
    backend.load_model_async = AsyncMock()

    await backend.generate(
        "Hello", {"ref_audio": str(reference), "ref_text": "Reference"}, temperature=0.6
    )
    await backend.generate("Hello", {"ref_audio": str(reference)})

    assert backend.model.calls[0][1]["temperature"] == 0.6
    assert backend.model.calls[0][1]["ref_audio"] == str(reference)
    assert "temperature" not in backend.model.calls[1][1]

    with pytest.raises(FileNotFoundError, match="reference audio not found"):
        await backend.generate("Hello", {"ref_audio": str(tmp_path / "missing.wav")})
    assert len(backend.model.calls) == 2


@pytest.mark.asyncio
async def test_mlx_qwen_surfaces_clone_failure(tmp_path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"wav")

    class FailingModel:
        def __init__(self):
            self.calls = 0

        def generate(self, _text, **_kwargs):
            self.calls += 1
            raise ValueError("bad prompt")

    backend = MLXTTSBackend()
    backend.model = FailingModel()
    backend.load_model_async = AsyncMock()

    with pytest.raises(RuntimeError, match="voice cloning failed"):
        await backend.generate("Hello", {"ref_audio": str(reference)})
    assert backend.model.calls == 1


@pytest.mark.asyncio
async def test_pytorch_qwen_forwards_both_temperatures(monkeypatch):
    monkeypatch.setattr(PyTorchTTSBackend, "_get_device", lambda _self: "cpu")

    class Model:
        def __init__(self):
            self.kwargs = None

        def generate_voice_clone(self, **kwargs):
            self.kwargs = kwargs
            return [np.ones(2, dtype=np.float32)], 24000

    backend = PyTorchTTSBackend()
    backend.model = Model()
    backend.load_model_async = AsyncMock()

    await backend.generate("Hello", {}, temperature=0.6)

    assert backend.model.kwargs["temperature"] == 0.6
    assert backend.model.kwargs["subtalker_temperature"] == 0.6

    await backend.generate("Hello", {})
    assert "temperature" not in backend.model.kwargs
    assert "subtalker_temperature" not in backend.model.kwargs


def test_qwen_repetition_penalty_forgets_old_tokens_without_changing_sampling():
    from types import SimpleNamespace
    from backend.backends.mlx_backend import _use_recent_repetition_context

    received = []
    model = SimpleNamespace(_sample_token=lambda *args, **kwargs: received.append((args, kwargs)))
    _use_recent_repetition_context(model)
    model._sample_token("logits", generated_tokens=list(range(100)), temperature=0.8, repetition_penalty=1.5)
    assert received[-1] == (("logits",), {"generated_tokens": list(range(36, 100)), "temperature": 0.8, "repetition_penalty": 1.5})
    model._sample_token("subtalker", generated_tokens=None, temperature=0.8)
    assert received[-1][1]["generated_tokens"] is None
