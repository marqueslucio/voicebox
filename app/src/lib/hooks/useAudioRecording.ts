import { useCallback, useEffect, useRef, useState } from 'react';
import { usePlatform } from '@/platform/PlatformContext';
import { convertToWav } from '@/lib/utils/audio';

interface UseAudioRecordingOptions {
  maxDurationSeconds?: number;
  onRecordingComplete?: (blob: Blob, duration?: number) => void;
}

export function useAudioRecording({
  maxDurationSeconds,
  onRecordingComplete,
}: UseAudioRecordingOptions = {}) {
  const platform = usePlatform();
  const [isRecording, setIsRecording] = useState(false);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);
  const startTimeRef = useRef<number | null>(null);
  const cancelledRef = useRef<boolean>(false);
  // Chord released before MediaRecorder.onstart fired — consume in onstart
  // so a tap shorter than the mic spin-up still terminates the recording.
  const pendingStopRef = useRef<boolean>(false);
  // Keeps the mic stream alive briefly after a recording so back-to-back
  // dictations skip the getUserMedia spin-up entirely.
  const streamReleaseTimerRef = useRef<number | null>(null);

  const releaseStream = useCallback(() => {
    if (streamReleaseTimerRef.current !== null) {
      window.clearTimeout(streamReleaseTimerRef.current);
      streamReleaseTimerRef.current = null;
    }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  /** How long the mic stream stays open after a recording ends. The macOS
   *  mic-in-use indicator stays lit for this window — the visible cost of
   *  making consecutive dictations start instantly. */
  const STREAM_KEEPALIVE_MS = 60_000;

  const scheduleStreamRelease = useCallback(() => {
    if (streamReleaseTimerRef.current !== null) {
      window.clearTimeout(streamReleaseTimerRef.current);
    }
    streamReleaseTimerRef.current = window.setTimeout(() => {
      streamReleaseTimerRef.current = null;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }, STREAM_KEEPALIVE_MS);
  }, []);

  const prewarmedRef = useRef(false);

  /**
   * One-shot microphone warm-up: acquire and immediately release an audio
   * stream. The first `getUserMedia` of a session pays for the TCC check
   * and audio-driver spin-up — cold that costs 1–2 s, and every word
   * spoken before the stream exists is physically lost, which is why the
   * first seconds of a dictation went missing. After a warm-up the real
   * acquisition in `startRecording` settles in a few hundred ms. Callers
   * invoke this when the recording surface mounts (e.g. the dictate pill
   * webview), long before the user hits the chord.
   */
  const prewarm = useCallback(async () => {
    if (prewarmedRef.current) return;
    prewarmedRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((track) => track.stop());
    } catch {
      // Permission missing or device busy — let a later attempt retry.
      prewarmedRef.current = false;
    }
  }, []);

  const startRecording = useCallback(async () => {
    try {
      setError(null);
      chunksRef.current = [];
      cancelledRef.current = false;
      pendingStopRef.current = false;
      setDuration(0);

      // Check if getUserMedia is available
      // In Tauri, navigator.mediaDevices might not be available immediately
      if (typeof navigator === 'undefined') {
        const errorMsg =
          'Navigator API is not available. This might be a Tauri configuration issue.';
        setError(errorMsg);
        throw new Error(errorMsg);
      }

      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        // Try waiting a bit for Tauri webview to initialize
        await new Promise((resolve) => setTimeout(resolve, 100));

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          console.error('MediaDevices check:', {
            hasNavigator: typeof navigator !== 'undefined',
            hasMediaDevices: !!navigator?.mediaDevices,
            hasGetUserMedia: !!navigator?.mediaDevices?.getUserMedia,
            isTauri: platform.metadata.isTauri,
          });

          const errorMsg = platform.metadata.isTauri
            ? 'Microphone access is not available. Please ensure:\n1. The app has microphone permissions in System Settings (macOS: System Settings > Privacy & Security > Microphone)\n2. You restart the app after granting permissions\n3. You are using Tauri v2 with a webview that supports getUserMedia'
            : 'Microphone access is not available. Please ensure you are using a secure context (HTTPS or localhost) and that your browser has microphone permissions enabled.';
          setError(errorMsg);
          throw new Error(errorMsg);
        }
      }

      // Reuse the still-warm stream from the previous recording when it's
      // alive — skips the getUserMedia spin-up entirely for back-to-back
      // dictations. Otherwise request a fresh one.
      if (streamReleaseTimerRef.current !== null) {
        window.clearTimeout(streamReleaseTimerRef.current);
        streamReleaseTimerRef.current = null;
      }
      let stream = streamRef.current;
      const streamAlive =
        stream !== null &&
        stream.getTracks().length > 0 &&
        stream.getTracks().every((track) => track.readyState === 'live');
      if (!streamAlive) {
        releaseStream();
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
      }

      if (!stream) {
        throw new Error('Microphone stream unavailable');
      }
      streamRef.current = stream;

      // Create MediaRecorder with preferred MIME type
      const options: MediaRecorderOptions = {
        mimeType: 'audio/webm;codecs=opus',
      };

      // Fallback to default if webm not supported
      if (!MediaRecorder.isTypeSupported(options.mimeType!)) {
        delete options.mimeType;
      }

      const mediaRecorder = new MediaRecorder(stream, options);
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        // Snapshot the cancellation flag and recorded duration immediately —
        // cancelRecording() clears chunks and sets cancelledRef synchronously
        // before this async handler runs, so we must check it first.
        const wasCancelled = cancelledRef.current;
        const recordedDuration = startTimeRef.current
          ? (Date.now() - startTimeRef.current) / 1000
          : undefined;

        const webmBlob = new Blob(chunksRef.current, { type: 'audio/webm' });

        // Keep the stream warm for the next dictation instead of stopping
        // the tracks — released by the keep-alive timer.
        scheduleStreamRelease();

        // Don't fire completion callback if the recording was cancelled
        if (wasCancelled) return;

        // Convert to WAV format to avoid needing ffmpeg on backend
        try {
          const wavBlob = await convertToWav(webmBlob);
          onRecordingComplete?.(wavBlob, recordedDuration);
        } catch (err) {
          console.error('Error converting audio to WAV:', err);
          // Fallback to original blob if conversion fails
          onRecordingComplete?.(webmBlob, recordedDuration);
        }
      };

      mediaRecorder.onerror = (event) => {
        setError('Recording error occurred');
        console.error('MediaRecorder error:', event);
      };

      // `isRecording`, the elapsed timer, and the duration anchor only
      // flip once capture has actually begun — the UI must not claim
      // "Recording" while words can still be lost to mic spin-up.
      mediaRecorder.onstart = () => {
        // Chord already released during spin-up: end immediately. The tiny
        // blob is surfaced as "Recording too short" by the session layer.
        if (pendingStopRef.current) {
          mediaRecorder.stop();
          return;
        }
        setIsRecording(true);
        startTimeRef.current = Date.now();

        timerRef.current = window.setInterval(() => {
          if (startTimeRef.current) {
            const elapsed = (Date.now() - startTimeRef.current) / 1000;
            setDuration(elapsed);

            // Auto-stop at max duration when the caller opts in — dictation
            // sessions pass undefined and run until the user releases the
            // chord or hits stop; voice-clone sample recorders pass 29s to
            // keep reference clips short.
            if (maxDurationSeconds !== undefined && elapsed >= maxDurationSeconds) {
              if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
                mediaRecorderRef.current.stop();
                setIsRecording(false);
                if (timerRef.current !== null) {
                  clearInterval(timerRef.current);
                  timerRef.current = null;
                }
              }
            }
          }
        }, 100);
      };

      // WebKit's MediaRecorder drops the WebM EBML header from chunks when
      // started with a timeslice, so concatenated blobs fail to parse in
      // both AudioContext and ffmpeg. Starting with no timeslice produces
      // exactly one dataavailable on stop() with a valid container.
      mediaRecorder.start();
    } catch (err) {
      const errorMessage =
        err instanceof Error
          ? err.message
          : 'Failed to access microphone. Please check permissions.';
      setError(errorMessage);
      setIsRecording(false);
    }
  }, [maxDurationSeconds, onRecordingComplete]);

  const stopRecording = useCallback(() => {
    // Mark unconditionally: if the recorder is still spinning up, onstart
    // consumes the flag and terminates the recording right away.
    pendingStopRef.current = true;
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state === 'recording') {
      recorder.stop();
      setIsRecording(false);

      if (timerRef.current !== null) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
  }, []);

  const cancelRecording = useCallback(() => {
    pendingStopRef.current = true;
    if (mediaRecorderRef.current) {
      cancelledRef.current = true; // Must be set before stop() triggers onstop
      chunksRef.current = [];
      if (mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
      setIsRecording(false);
      setDuration(0);
    }

    releaseStream();

    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, [releaseStream]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        clearInterval(timerRef.current);
      }
      if (streamReleaseTimerRef.current !== null) {
        clearTimeout(streamReleaseTimerRef.current);
      }
      streamRef.current?.getTracks().forEach((track) => {
        track.stop();
      });
    };
  }, []);

  return {
    isRecording,
    duration,
    error,
    prewarm,
    startRecording,
    stopRecording,
    cancelRecording,
  };
}
