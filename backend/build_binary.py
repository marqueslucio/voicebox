"""Build the Apple Silicon dictation server."""

import os
import platform
from pathlib import Path

import PyInstaller.__main__

backend_dir = Path(__file__).resolve().parent
native = backend_dir / "native" / "nemo-speech"
if platform.system() != "Darwin" or platform.machine() != "arm64":
    raise RuntimeError("Voicebox dictation requires Apple Silicon macOS")
if not (native / "lib/libnemo_speech_asr_c.1.dylib").is_file():
    raise RuntimeError("Native Parakeet runtime is missing")

os.chdir(backend_dir)
args = [
    "server.py", "--onefile", "--name", "voicebox-server",
    "--paths", str(backend_dir.parent),
    "--distpath", str(backend_dir / "dist"),
    "--workpath", str(backend_dir / "build"),
    "--specpath", str(backend_dir / "build"),
    "--noconfirm", "--clean",
    "--collect-all", "mlx",
    "--collect-all", "librosa",
    "--collect-all", "lazy_loader",
    "--hidden-import", "mlx_audio.stt.models.whisper",
    "--hidden-import", "backend.backends.mlx_backend",
    "--hidden-import", "backend.backends.parakeet_backend",
    "--hidden-import", "mlx_audio.stt",
    "--hidden-import", "miniaudio",
    "--copy-metadata", "transformers",
    "--copy-metadata", "huggingface-hub",
    "--copy-metadata", "tokenizers",
    "--copy-metadata", "safetensors",
    "--copy-metadata", "tqdm",
    "--add-data", f"{native / 'licenses'}:backend/native/nemo-speech/licenses",
]
for library in sorted((native / "lib").glob("*.dylib")):
    args.extend(["--add-binary", f"{library}:backend/native/nemo-speech/lib"])
for module in ("torch", "torchaudio", "torchvision", "mlx_audio.tts", "mlx_lm",
               "qwen_tts", "fastmcp", "mcp", "spacy", "pedalboard"):
    args.extend(["--exclude-module", module])
PyInstaller.__main__.run(args)
