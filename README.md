# Voicebox

Voicebox is a local macOS dictation app. Record in the window or use a global shortcut, transcribe with Whisper or Parakeet, and copy a transcript from history. Audio and transcripts remain in the existing local Voicebox data directory; model downloads use the Hugging Face cache.

Choose and download an STT model in Settings. macOS may ask for Microphone, Input Monitoring and Accessibility access. Accessibility is needed for automatic paste; each completed dictation is also copied to the clipboard and kept in history.

For local development and build instructions see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

Dictation audio is removed after 24 hours by default; capture history and transcripts are removed after 7 days. Both periods can be changed in Settings. Cleanup runs at startup and hourly.
