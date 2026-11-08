"""Detect the local Apple Silicon MLX backend."""

import platform


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def get_backend_type() -> str:
    if is_apple_silicon():
        try:
            import mlx.core  # noqa: F401
            return "mlx"
        except (ImportError, OSError, RuntimeError):
            pass
    return "unavailable"
