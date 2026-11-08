# Development

Voicebox consists of a Tauri macOS app and a local Python transcription sidecar. Use the existing project environment and `bun install` for the frontend. `bun run typecheck` checks the frontend and `bun run --cwd tauri build` builds its web assets. `scripts/build-server.sh` builds the sidecar. The project install script documents the native build and installation path.

Dictation uses the existing Voicebox application-support directory and Hugging Face model cache. Do not remove existing captures or models as part of a build. The only installed application bundle is `/Applications/Voicebox.app`.
