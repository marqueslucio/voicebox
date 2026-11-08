# Voicebox macOS development commands
venv := "backend/venv"
python := venv / "bin/python"
pip := venv / "bin/pip"
system_python := `command -v python3.12 2>/dev/null || echo python3`

# Install backend and frontend dependencies
setup: setup-python setup-js

# Create the Apple Silicon backend environment
setup-python:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ "$(uname -m)" != "arm64" ] || [ "$(uname)" != "Darwin" ]; then
        echo "Voicebox requires Apple Silicon macOS." >&2
        exit 1
    fi
    if [ ! -d "{{ venv }}" ]; then
        {{ system_python }} -m venv {{ venv }}
    fi
    {{ pip }} install -r backend/requirements.txt -r backend/requirements-mlx.txt
    {{ pip }} install --no-deps mlx-audio==0.4.1
    {{ pip }} install pyinstaller ruff pytest pytest-asyncio httpx

setup-js:
    bun install

# Run an isolated development backend and Tauri app
dev:
    ./script/build_and_run.sh

# Run only the local backend on the app port
server:
    {{ python }} -m backend.main --port 17495

build-server:
    ./scripts/build-server.sh

build-frontend:
    bun run --cwd tauri build

typecheck:
    bun run typecheck

test-frontend:
    bun test app

test-backend:
    PYTHONPATH="$PWD/backend:$PWD" {{ python }} -m pytest backend/tests

# Build, sign, and install the application (quit the installed app first)
install:
    ./script/build_and_install.sh build

# Sign and install an already-built app, moving it out of build staging
install-built:
    ./script/build_and_install.sh --install-built
