# Voicebox — Lokaler Fork: Entwicklungsstand

> Stand: 2026-07-31 | App-Version: **0.5.1** | Branch `fix/dictation-dev-isolation` = `main` = 6fbcd86, **11 Commits vor `origin/main`** | Kein Push/PR ohne explizites Go (siehe AGENTS.md)

Diese Datei ist die Übergabeakte für neue Agent-Sessions. Sie beschreibt den lokalen Stand dieses Forks von `jamiepine/voicebox`, nicht das Upstream-Projekt (dafür: `docs/PROJECT_STATUS.md`, Stand 02.07., veraltet).

## Worktree-Layout

| Pfad | Branch | Rolle |
|---|---|---|
| `05-projects/software/voicebox` | `main` | Kanonischer Checkout, nur Merge-Ziel |
| `05-projects/software/voicebox-dictation-fix` | `fix/dictation-dev-isolation` | Arbeits-Worktree (hier bauen!), enthält Sidecar-Binaries, venv, node_modules |

Nach jedem Commit auf dem Arbeitsbranch: `git -C ../voicebox merge --no-edit fix/dictation-dev-isolation` (bisher immer Fast-Forward).

## Build & Install (verbindliche Pipeline)

```bash
# Nur bei Backend-Änderungen (PyInstaller, ~5 min):
PATH="$PWD/backend/venv/bin:$PATH" ./scripts/build-server.sh

# App-Bundle (Rust/Frontend inkrementell, ~1-2 min):
cd tauri && bun run tauri build --bundles app --config '{"bundle":{"createUpdaterArtifacts":false}}'

# Installieren (App muss beendet sein; alte Version wandert in den Papierkorb):
APPLE_SIGNING_IDENTITY="Apple Development: contact@marqueslucio.com (ULTQS3R2AQ)" \
  ./script/build_and_install.sh --install-built
```

**Signatur ist kritisch:** macOS bindet TCC-Grants (Mikrofon, Input Monitoring, Accessibility) an das Designated Requirement der Signatur. Adhoc-Signaturen haben keins → Grants sterben bei jedem Rebuild. Deshalb immer mit der Developer-Identity signieren (macht das Skript über `APPLE_SIGNING_IDENTITY`); solange die Identity gleich bleibt, überleben alle Grants jeden Update-Zyklus.

**Iterieren ohne PyInstaller:** Backend direkt aus dem Source testen:
```bash
VOICEBOX_MODELS_DIR="$HOME/.cache/huggingface/hub" backend/venv/bin/python -m backend.main \
  --data-dir <scratch-dir> --port 17999
```
Echte deutsche Test-WAVs liegen unter `~/Library/Application Support/sh.voicebox.app/captures/`.

## In dieser Session gefixt (Commits 8013f07..6fbcd86, alle installiert & verifiziert)

1. **Electron-Paste komplett repariert** — drei unabhängige Ursachen (768c473, 8013f07):
   - `focus_capture`: Electron-Apps beantworten die systemweite `AXFocusedUIElement`-Abfrage nicht → Fallback auf Frontmost-App.
   - `synthetic_keys`: Cmd-Übergänge als `kCGEventFlagsChanged` posten (Chromium trackt Modifier nur daraus).
   - `PASTE_CONSUME_MS` 400→2000 ms: Electron liest das Clipboard asynchron nach dem Cmd+V.
2. **Server-Watchdog lief nie** (ed5e49e): `@app.on_event("startup")` wird bei gesetztem `lifespan=` von FastAPI ignoriert → Direktstart aus `__main__`. Dazu `_PipeSafeStream` gegen `[Errno 32] Broken pipe` bei toten stdio-Pipes (Keep-Running-Modus). Verifiziert: Server beendet sich ~3 s nach Parent-Tod.
3. **Pill-Position** (744d76a, 7bc9de1, 6935ddf): unten mittig, auf dem Monitor des Cursors, in **logischen** Koordinaten (physische Positionen sind bei gemischten Scale-Faktoren mehrdeutig — Ursache der "random Position").
4. **Close-to-Tray** (744d76a, 6fbcd86): X versteckt ins Menüleisten-Icon (Template-Rendering, sonst auf dunkler Leiste unsichtbar), Accessory-Policy ohne Dock-Icon, Server + Hotkey laufen weiter. `RunEvent::Reopen` (Spotlight/Dock) holt das Fenster zurück. Achtung: Menüleisten-Manager (Ice/Bartender) stecken das Icon anfangs in die versteckte Sektion.
5. **Deutsches Refinement** (8e56738): Sprach-Lock + deutsche Few-Shots + Greedy-Decoding. Vorher übersetzte/kollabierte Qwen3 deutsche Transkripte (Prompt und alle Beispiele waren englisch).
6. **STT-Halluzinationen** (a313f25, a39594b): Loop-Collapse (≥6 Wiederholungen) direkt am Roh-Transkript; Trailing-Silence-Trim (librosa, top_db=40, **nur hinten** — vorne frisst er leise Satzanfänge); `condition_on_previous_text=False` (tötet Loop-Kaskaden wie „canste"×100 an der Quelle).
7. **Aufnahmestart** (a313f25, 1d1f08f): Mikrofon-Prewarm beim Pill-Fenster-Mount; ehrlicher Pill-Zustand `starting` → „Recording" erst ab `MediaRecorder.onstart`; Stream bleibt 60 s warm (Folge-Diktate starten sofort; oranger Mikro-Punkt leuchtet solange); Chord-Release während Spin-up wird sauber abgebrochen.

## Empfohlene Nutzer-Einstellungen (Stand heute)

- STT: **turbo** (~1,6 GB RAM solange App läuft). `small`/`base` sind für Deutsch gemessen unbrauchbar (Wortverfälschung bzw. Halluzinations-Loops und Sinn-Umkehrung) — nicht wieder vorschlagen.
- Auto-Refine: **aus** für den Alltag (Turbo liefert Deutsch bereits interpunktiert; Refine spart nichts und Qwen3-1.7B glitcht bei langen Texten). Mit Refine an: kein Übersetzen/Kollabieren mehr, aber sporadische Großschreibfehler („ausgabelt Hast") bleiben — Modellgrenze.

## Bekannte Grenzen & offene Punkte

- **Tail-Restwort:** Nach dem Trim kann ein einzelnes halluziniertes Wort übrig bleiben („ja"). Der saubere Fix (`word_timestamps` + `hallucination_silence_threshold`) ist **upstream kaputt**: mlx_audio 0.x DTW-Pfad liest `model.alignment_heads`, das nie gesetzt wird; manuell verdrahtet → nativer Crash. Nicht wieder aktivieren, bis mlx_audio das fixt.
- **Nächstes sinnvolles Feature:** Whisper-Vokabular-Hint über `initial_prompt` (API in `mlx_audio.stt` vorhanden) gegen Verhörer von Fachbegriffen/Markennamen — z. B. als editierbare Begriffsliste in den Captures-Settings. Vom User gewünscht, noch nicht beauftragt.
- **Refine-Qualität lang:** Qwen3-1.7B-4bit bleibt die Grenze; größere Modelle sprengen das RAM-Ziel.
- `docs/PROJECT_STATUS.md` ist Upstream-Analyse vom 02.07. und für den Fork-Stand irrelevant.

## Nicht committen

`AGENTS.md` (lokaler Agent-Vertrag) und `.codex/` bleiben bewusst untracked.
