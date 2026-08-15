import { useCallback, useEffect, useRef, useState } from 'react';
import { usePlatform } from '@/platform/PlatformContext';
import { convertToWav } from '@/lib/utils/audio';

interface UseAudioRecordingOptions {
  maxDurationSeconds?: number;
  onRecordingComplete?: (blob: Blob, duration?: number) => void;
}

/**
 * Raw capture, no voice processing. Echo cancellation / noise suppression /
 * AGC route the mic through CoreAudio's VoiceProcessingIO unit, which costs
 * a few hundred extra ms to start (time in which spoken words are lost) and
 * makes macOS duck every other app's output for as long as the unit is
 * live. Whisper wants the unprocessed signal anyway, and voice-clone
 * reference samples are better off without AGC riding the gain.
 */
const MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false,
  },
};

/** Give up waiting for the first audio frame after this long and treat the
 *  recording as live anyway — a muted or broken input must not strand the
 *  pill in its "starting" state forever. Measured ramp-up on a stream that
 *  has just been acquired is ~30 ms, so this only ever fires when something
 *  is genuinely wrong with the input. */
const AUDIO_FLOW_TIMEOUT_MS = 600;

/**
 * Resolve once the stream actually delivers samples. `MediaRecorder.onstart`
 * only says the encoder is armed; the capture unit can still be ramping, and
 * during that window it emits digital silence. Polling an analyser for the
 * first non-zero sample is the honest "the mic is live now" signal — that's
 * what the pill's Recording label and the elapsed timer hang off.
 *
 * Returns a cancel function; call it to tear the probe down early.
 */
function probeAudioFlow(stream: MediaStream, onLive: () => void): () => void {
  let context: AudioContext | null = null;
  let interval: number | null = null;
  let timeout: number | null = null;
  let done = false;

  const finish = (live: boolean) => {
    if (done) return;
    done = true;
    if (interval !== null) window.clearInterval(interval);
    if (timeout !== null) window.clearTimeout(timeout);
    context?.close().catch(() => {});
    context = null;
    if (live) onLive();
  };

  try {
    const audio = new AudioContext();
    context = audio;
    const analyser = audio.createAnalyser();
    analyser.fftSize = 2048;
    audio.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);

    // A context that won't leave 'suspended' (autoplay policy) never feeds
    // the analyser. Don't sit out the timeout for that — fall straight back
    // to trusting MediaRecorder.
    audio
      .resume()
      .then(() => {
        if (audio.state !== 'running') finish(true);
      })
      .catch(() => finish(true));

    interval = window.setInterval(() => {
      analyser.getFloatTimeDomainData(samples);
      if (samples.some((value) => value !== 0)) finish(true);
    }, 16);
    timeout = window.setTimeout(() => finish(true), AUDIO_FLOW_TIMEOUT_MS);
  } catch {
    // No Web Audio (or the context refused to start) — fall back to
    // trusting MediaRecorder rather than blocking the recording.
    finish(true);
  }

  return () => finish(false);
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
  const liveProbeRef = useRef<(() => void) | null>(null);

  const releaseStream = useCallback(() => {
    liveProbeRef.current?.();
    liveProbeRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
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
   *
   * Uses the same constraints as the real acquisition — a different
   * constraint set builds a different audio unit, so warming with
   * `{audio: true}` would warm a path we never use.
   */
  const prewarm = useCallback(async () => {
    if (prewarmedRef.current) return;
    prewarmedRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
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

      // Always a fresh stream: the mic is released the moment a recording
      // ends, so nothing is ever held open between dictations (no lit
      // mic indicator, no system-wide output ducking).
      releaseStream();
      const stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
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

        // Drop the mic immediately. Holding it open would keep the macOS
        // recording indicator lit and keep other apps' audio ducked.
        releaseStream();

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
      // flip once the mic actually delivers samples — the UI must not claim
      // "Recording" while words can still be lost to mic spin-up.
      const onAudioLive = () => {
        liveProbeRef.current = null;
        // Released while the capture unit was still ramping — stopRecording
        // has already run, so don't arm a recording that's over.
        if (pendingStopRef.current || mediaRecorder.state !== 'recording') return;
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

      mediaRecorder.onstart = () => {
        // Chord already released during spin-up: end immediately. The tiny
        // blob is surfaced as "Recording too short" by the session layer.
        if (pendingStopRef.current) {
          mediaRecorder.stop();
          return;
        }
        liveProbeRef.current = probeAudioFlow(stream, onAudioLive);
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
  }, [maxDurationSeconds, onRecordingComplete, releaseStream]);

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
      releaseStream();
    };
  }, [releaseStream]);

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
