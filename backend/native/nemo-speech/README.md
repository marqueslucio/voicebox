# NeMo Speech native ASR runtime

Official NVIDIA NeMo-Speech.cpp 0.2.0 macOS aarch64 Metal runtime.
Source: https://github.com/NVIDIA/NeMo-Speech.cpp/releases/tag/v0.2.0
Archive SHA-256: 5cb02ba7c04f0b5585ce5cde9c830be5f0b7dfb4c83083c500109c381b4f2da9
Only ASR, text normalization, and their ggml dependencies are bundled.
The stable C ABI is used by `backend/backends/parakeet_backend.py`.
Runtime libraries retain their relative `@loader_path` search paths.
Models stay in the configured Hugging Face cache, outside the app bundle.
