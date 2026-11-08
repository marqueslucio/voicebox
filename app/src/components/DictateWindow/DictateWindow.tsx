import { invoke } from '@tauri-apps/api/core';
import { emit, listen, type UnlistenFn } from '@tauri-apps/api/event';
import { useEffect, useRef } from 'react';
import { CapturePill } from '@/components/CapturePill/CapturePill';
import type { FocusSnapshot } from '@/lib/api/types';
import { useCaptureRecordingSession } from '@/lib/hooks/useCaptureRecordingSession';

/** Floating recording surface controlled by the global shortcut. */
export function DictateWindow() {
  // Force the host document chrome to be transparent so the Tauri window
  // takes on the pill's own shape.
  useEffect(() => {
    const prevHtml = document.documentElement.style.background;
    const prevBody = document.body.style.background;
    document.documentElement.style.background = 'transparent';
    document.body.style.background = 'transparent';
    return () => {
      document.documentElement.style.background = prevHtml;
      document.body.style.background = prevBody;
    };
  }, []);

  // Snapshot of the focused UI element at chord-start, shipped over from
  // Rust on the ``dictate:start`` payload. Held in a ref so it survives
  // the 1–2 s transcription window — the paste only fires once the
  // final text comes back.
  const focusRef = useRef<FocusSnapshot | null>(null);

  const session = useCaptureRecordingSession({
    onFinalText: async (text, _capture, allowAutoPaste) => {
      const focus = focusRef.current;
      // Consume-once: a second chord before this fires would overwrite
      // focusRef, but nulling it here guards against the late-arriving
      // transcript firing a paste after the user has moved on.
      focusRef.current = null;
      if (!text.trim()) return;
      try {
        await invoke('paste_final_text', { text, focus, allowAutoPaste });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (/accessibility/i.test(msg)) {
          emit('system:accessibility-missing').catch(() => {});
        }
        console.warn('[dictate] copy or paste failed:', err);
      }
    },
  });

  // Route the chord events emitted from Rust into the session hook. Using a
  // ref so the `listen` effect only subscribes once — rebinding every render
  // would thrash the Tauri event bridge.
  const sessionRef = useRef(session);
  sessionRef.current = session;

  // Warm the microphone path as soon as the pill webview exists — the
  // first getUserMedia of a session pays 1–2 s of TCC + audio-driver
  // spin-up, and every word spoken before the stream is live is lost.
  // Warming here moves that cost to window creation (enable_hotkey),
  // long before the first chord press.
  useEffect(() => {
    sessionRef.current.prewarmMic();
  }, []);

  useEffect(() => {
    const unlistens: Promise<UnlistenFn>[] = [];
    unlistens.push(
      listen<{ focus: FocusSnapshot | null }>('dictate:start', (event) => {
        focusRef.current = event.payload?.focus ?? null;
        sessionRef.current.startRecording();
      }),
    );
    unlistens.push(
      listen('dictate:stop', () => {
        // Unconditional: stopRecording is safe in every phase and marks a
        // pending stop when the chord is released while the recorder is
        // still spinning up ('starting'), so a fast tap can't leave a
        // recording running.
        sessionRef.current.stopRecording();
      }),
    );
    unlistens.push(
      // Escape, observed globally by the Rust chord matcher. A no-op unless
      // a capture is currently transcribing.
      listen('dictate:cancel', () => {
        sessionRef.current.cancelCapture();
      }),
    );
    return () => {
      for (const p of unlistens) p.then((fn) => fn()).catch(() => {});
    };
  }, []);

  const effectiveState = session.pillState;
  const effectiveElapsed = session.pillElapsedMs;

  // When the pill cycle ends (no capture), tell Rust to tuck
  // the window away. Rust owns the hide + park-off-screen + click-through
  // combo because calling hide() directly from JS has been unreliable for
  // transparent always-on-top windows on macOS.
  useEffect(() => {
    if (effectiveState === 'hidden') {
      emit('dictate:hide').catch(() => {});
    }
  }, [effectiveState]);

  return (
    <div
      className="h-screen w-screen flex items-center justify-center px-3"
      style={{ background: 'transparent' }}
    >
      {effectiveState !== 'hidden' ? (
        <CapturePill
          state={effectiveState}
          elapsedMs={effectiveElapsed}
          errorMessage={session.errorMessage}
          onDismiss={session.dismissError}
          onStop={session.isRecording ? session.stopRecording : undefined}
        />
      ) : null}
    </div>
  );
}
