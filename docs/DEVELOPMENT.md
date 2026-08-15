# Voicebox — Lokaler Fork: Entwicklungsstand

> Stand: 2026-08-15 | App-Version: **0.5.1** | `main` = e31e166 (**15 Commits vor `origin/main`**), Arbeitsbranch `fix/input-ramp-compensation` | Kein Push/PR ohne explizites Go (siehe AGENTS.md)

Diese Datei ist die Übergabeakte für neue Agent-Sessions. Sie beschreibt den lokalen Stand dieses Forks von `jamiepine/voicebox`, nicht das Upstream-Projekt (dafür: `docs/PROJECT_STATUS.md`, Stand 02.07., veraltet).

## Repo-Layout

Das Repo liegt seit der Workspace-Migration (14.08.) unter `~/Code/voicebox`, die Projektakte unter `…/Helios/projects/software/voicebox` (mit `code`-Symlink). Der frühere Arbeits-Worktree `voicebox-dictation-fix` existiert nicht mehr; `fix/dictation-dev-isolation` ist in `main` gemergt. Gearbeitet wird direkt im Hauptcheckout auf `main`.

`node_modules`, `backend/venv` und die Sidecar-Binaries sind nach der Migration **nicht** mehr vorhanden und müssen bei Bedarf neu erzeugt werden — siehe Build-Abschnitt.

## Build & Install (verbindliche Pipeline)

```bash
# Einmalig nach der Migration: JS-Dependencies
bun install

# Nur bei Backend-Änderungen (PyInstaller, ~5 min; braucht backend/venv):
PATH="$PWD/backend/venv/bin:$PATH" ./scripts/build-server.sh

# Ohne Backend-Änderung: Sidecar aus der installierten App übernehmen
# (spart den PyInstaller-Lauf, Binaries sind identisch zur laufenden Version):
mkdir -p tauri/src-tauri/binaries
cp /Applications/Voicebox.app/Contents/MacOS/voicebox-server \
   tauri/src-tauri/binaries/voicebox-server-aarch64-apple-darwin
cp /Applications/Voicebox.app/Contents/MacOS/voicebox-mcp \
   tauri/src-tauri/binaries/voicebox-mcp-aarch64-apple-darwin

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

## Session 15.08.2026 — Mikrofon-Lebenszyklus, Transkriptions-Schutz

1. **Mikro geht sofort nach der Aufnahme aus** (`useAudioRecording`): Die 60-s-Keepalive des Streams ist entfernt. Sie hielt das macOS-Mikro-Indikator-Licht an und ließ (über die Voice-Processing-Kette) alle anderen Apps leiser spielen. Jede Aufnahme holt jetzt einen frischen Stream und gibt ihn in `onstop`/`cancel` sofort frei.
2. **Rohaufnahme statt Voice Processing**: `echoCancellation`/`noiseSuppression`/`autoGainControl` sind aus. Sie zwingen CoreAudio in die VoiceProcessingIO-Unit — langsamer Start (Wortverluste) und systemweites Ducking. Whisper will das unverarbeitete Signal ohnehin. `prewarm` nutzt jetzt dieselben Constraints, sonst wärmt es eine Audio-Unit, die nie benutzt wird.
3. **„Recording" erst wenn wirklich Samples fließen**: `MediaRecorder.onstart` sagt nur, dass der Encoder scharf ist. Ein Analyser pollt jetzt bis zum ersten Sample ≠ 0 (`probeAudioFlow`, Timeout 600 ms, Fallback bei suspendiertem AudioContext); erst dann kippen Pill-Label, Elapsed-Timer und `isRecording`.
4. **Laufende Transkription ist gegen den Aufnahme-Trigger geschützt**: `startRecording` ignoriert Chord/Button in `starting`/`transcribing`/`refining`. Vorher warf ein zweiter Chord-Druck die Transkription weg und startete neu.
5. **Escape bricht die Transkription ab**: Neuer, nicht bindbarer Cancel-Chord (`ChordAction::Cancel` auf `Key::Escape`) im `hotkey_monitor` → Event `dictate:cancel`; im Haupt-Tab derselbe Abbruch über einen DOM-Keydown. `cancelCapture` zählt eine Generation hoch, die jede Mutation beim Start snapshottet — spätes Ergebnis pastet nicht mehr und öffnet die Pill nicht wieder. Der Request selbst läuft serverseitig zu Ende, die Capture-Zeile bleibt in der Liste.
   - Der Cancel-Chord wird nicht registriert, wenn Escape Teil eines Nutzer-Chords ist (Longest-Match wäre sonst mehrdeutig), und wird nie mit einem Recording-Chord zu `RestartRecording` zusammengefasst.
6. **Gedämpfter Aufnahmestart wird angehoben** (Branch `fix/input-ramp-compensation`, noch nicht in `main`): Die Aufnahmekette liefert nicht ab dem ersten Sample vollen Pegel. Über die gespeicherten Captures gemessen kommen betroffene Aufnahmen ~3,2 s lang 30–36 dB gedämpft an; die dort gesprochenen Wörter stehen in der Datei, nur unterhalb dessen, was Whisper aufnimmt — daher die Diktate, die mitten im Satz beginnen. Der Aufnahmepfad konvertiert jetzt über `convertRecordingToWav`, das den Kopf im Float-Pfad vor der 16-Bit-Wandlung anhebt (`compensateInputRamp` in `app/src/lib/utils/audio.ts`); Uploads behalten `convertToWav`, weil ein absichtlicher Fade-in dieselbe Form hat. Zwei Gates halten es von gesunden Aufnahmen fern: Modulation im Kopf (gedämpfte Sprache schwankt zwischen Silben, Raumton nicht) und eine Mindest-Rampe von 1,5 s — gegen die DB gelabelt rampen angeschnittene Aufnahmen ~3,2 s, unauffällige ~1,1 s. Der Gain ist zusätzlich gegen Clipping gedeckelt. **Bekannte Grenze:** im gedämpften Fenster liegt das Signal nur ~10 dB über dem Mikrorauschen, ein Gain hebt beides — an fünf betroffenen Captures gemessen: drei gewinnen den verlorenen Anfang zurück, eine bleibt gleich, eine wird schlechter. Der ursächliche Fix wäre ein warmgehaltener Stream (der lange Ramp ist der Kaltstart, den das am 15.08. entfernte Keepalive verhindert hat); Marques hat sich bewusst gegen das Keepalive und für die reine Kompensation entschieden, um den Mikro-Indikator aus zu halten.

## Session 31.07.2026 (Commits 8013f07..6fbcd86, alle installiert & verifiziert)

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
