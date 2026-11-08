export function createAudioUrl(audioId: string, serverUrl: string): string {
  return `${serverUrl}/audio/${audioId}`;
}

export function downloadAudio(url: string, filename: string): void {
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

export function formatAudioDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

/**
 * Get audio duration from a File.
 * If the file has a recordedDuration property (from recording hooks),
 * use that instead of trying to read metadata. This fixes issues on Windows
 * where WebM files from MediaRecorder don't have proper duration metadata.
 *
 * For uploaded files we use AudioContext.decodeAudioData which fully decodes
 * the audio and returns the exact duration. This is more reliable than
 * HTMLMediaElement.duration which can return incorrect large values for VBR
 * MP3 files that lack a proper XING/VBRI header.
 */
export async function getAudioDuration(
  file: File & { recordedDuration?: number },
): Promise<number> {
  if (file.recordedDuration !== undefined && Number.isFinite(file.recordedDuration)) {
    return file.recordedDuration;
  }

  // Use Web Audio API for accurate duration — avoids VBR MP3 metadata issues.
  try {
    const audioContext = new AudioContext();
    try {
      const arrayBuffer = await file.arrayBuffer();
      const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
      return audioBuffer.duration;
    } finally {
      await audioContext.close();
    }
  } catch {
    // Fallback: read duration from the media element (less accurate but works for WAV).
    return new Promise((resolve, reject) => {
      const audio = new Audio();
      const url = URL.createObjectURL(file);

      audio.addEventListener('loadedmetadata', () => {
        URL.revokeObjectURL(url);
        if (Number.isFinite(audio.duration) && audio.duration > 0) {
          resolve(audio.duration);
        } else {
          reject(new Error('Audio file has invalid duration metadata'));
        }
      });

      audio.addEventListener('error', () => {
        URL.revokeObjectURL(url);
        reject(new Error('Failed to load audio file'));
      });

      audio.src = url;
    });
  }
}

async function decodeToBuffer(audioBlob: Blob): Promise<AudioBuffer> {
  const audioContext = new AudioContext();
  try {
    return await audioContext.decodeAudioData(await audioBlob.arrayBuffer());
  } finally {
    // Close the context to free resources.
    await audioContext.close();
  }
}

/**
 * Convert any audio blob to WAV format using Web Audio API.
 * This ensures compatibility without requiring ffmpeg on the backend.
 */
export async function convertToWav(audioBlob: Blob): Promise<Blob> {
  return audioBufferToWav(await decodeToBuffer(audioBlob));
}

/**
 * Same as `convertToWav`, but for audio that came off the microphone — it
 * additionally lifts a ramped-up recording start (see
 * `compensateInputRamp`). Uploaded files must not go through here: a
 * deliberate fade-in is exactly the shape the compensation looks for.
 */
export async function convertRecordingToWav(recording: Blob): Promise<Blob> {
  const buffer = await decodeToBuffer(recording);
  compensateInputRamp(buffer);
  return audioBufferToWav(buffer);
}

/**
 * Convert AudioBuffer to WAV blob.
 */
function audioBufferToWav(buffer: AudioBuffer): Blob {
  const numberOfChannels = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const format = 1; // PCM
  const bitDepth = 16;

  const bytesPerSample = bitDepth / 8;
  const blockAlign = numberOfChannels * bytesPerSample;

  // Interleave channels
  const interleaved = interleaveChannels(buffer);

  // Create WAV file
  const dataLength = interleaved.length * bytesPerSample;
  const buffer2 = new ArrayBuffer(44 + dataLength);
  const view = new DataView(buffer2);

  // Write WAV header
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataLength, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, format, true); // audio format (PCM)
  view.setUint16(22, numberOfChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true); // byte rate
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitDepth, true);
  writeString(view, 36, 'data');
  view.setUint32(40, dataLength, true);

  // Write audio data
  floatTo16BitPCM(view, 44, interleaved);

  return new Blob([buffer2], { type: 'audio/wav' });
}

/**
 * Interleave multiple channels into a single array.
 */
function interleaveChannels(buffer: AudioBuffer): Float32Array {
  const numberOfChannels = buffer.numberOfChannels;
  const length = buffer.length;
  const interleaved = new Float32Array(length * numberOfChannels);

  for (let channel = 0; channel < numberOfChannels; channel++) {
    const channelData = buffer.getChannelData(channel);
    for (let i = 0; i < length; i++) {
      interleaved[i * numberOfChannels + channel] = channelData[i];
    }
  }

  return interleaved;
}

/**
 * Write string to DataView.
 */
function writeString(view: DataView, offset: number, string: string): void {
  for (let i = 0; i < string.length; i++) {
    view.setUint8(offset + i, string.charCodeAt(i));
  }
}

/**
 * Convert float32 audio data to 16-bit PCM.
 */
function floatTo16BitPCM(view: DataView, offset: number, input: Float32Array): void {
  for (let i = 0; i < input.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, input[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
}

/* --- Recording start ramp compensation ------------------------------------
 *
 * The capture chain does not deliver full level from the first sample. On
 * the built-in mic, measured across the stored captures, the opening
 * seconds arrive 30-36 dB down and only then step up to the level the rest
 * of the recording sits at. The words spoken in that window ARE in the
 * file — they are just far below what Whisper picks up, which is why
 * dictations came back starting mid-sentence. Boosting the head of an
 * affected capture by ~36 dB and re-running it through /transcribe returns
 * words the stored transcript never had.
 *
 * So the fix is a gain, not a re-recording: find the point where the level
 * settles, and lift everything before it. The gates below exist so this
 * only ever fires on that ramp shape — a recording that simply starts with
 * the user staying quiet has a flat noise head and must stay untouched,
 * otherwise the amplified room tone becomes hallucination fuel.
 */

const FRAME_MS = 50;
/** Level (relative to the recording's speech level) that counts as "settled". */
const SETTLED_RATIO = 0.25;
/** Frames inspected for the settle test, and how many must clear it. Speech
 *  has gaps; requiring every frame would settle on the first pause. */
const SETTLE_WINDOW = 6;
const SETTLE_MIN_ABOVE = 4;
/** Only the long cold-start ramp costs words. Labelled against the stored
 *  captures (transcripts that begin mid-sentence vs. ones that don't), the
 *  damaging cases ramp for ~3.2 s while unaffected recordings settle in
 *  ~1.1 s; cutting in at 1.5 s keeps 11 of 12 damaged captures and halves
 *  how often the compensation touches a recording that was fine.
 *  Beyond MAX_RAMP_MS this is not a ramp any more but a quiet passage. */
const MIN_RAMP_MS = 1500;
const MAX_RAMP_MS = 6000;
/** Below this the whole recording is too quiet to reason about. */
const MIN_SPEECH_RMS = 0.005;
/** Peak-to-floor spread that separates attenuated speech from flat noise. */
const MIN_HEAD_MODULATION = 2;
/** Lift the head to just under the recording's own speech level, never past
 *  it — the point is to make it audible to Whisper, not to make it loud. */
const HEAD_TARGET_RATIO = 0.5;
const MAX_GAIN = 32; // ~30 dB
/** Highest sample the lifted head may reach — leaves headroom for the
 *  16-bit conversion instead of squaring off a syllable. */
const CLIP_CEILING = 0.9;
const MIN_GAIN = 2; // ~6 dB — below this the head was fine anyway
/** Blend back to unity across this window so the seam isn't a click. */
const FADE_MS = 100;

interface RampCorrection {
  gain: number;
  endSample: number;
}

function frameRms(samples: Float32Array, frameSize: number): Float32Array {
  const count = Math.floor(samples.length / frameSize);
  const levels = new Float32Array(count);
  for (let f = 0; f < count; f++) {
    const start = f * frameSize;
    let sum = 0;
    for (let i = start; i < start + frameSize; i++) sum += samples[i] * samples[i];
    levels[f] = Math.sqrt(sum / frameSize);
  }
  return levels;
}

function percentile(levels: Float32Array, p: number): number {
  if (levels.length === 0) return 0;
  const sorted = Float32Array.from(levels).sort();
  return sorted[Math.round((sorted.length - 1) * p)];
}

function planRampCorrection(samples: Float32Array, sampleRate: number): RampCorrection | null {
  const frameSize = Math.max(1, Math.round((sampleRate * FRAME_MS) / 1000));
  const levels = frameRms(samples, frameSize);
  if (levels.length < SETTLE_WINDOW * 2) return null;

  // The recording's own speech level is the reference — an absolute
  // threshold would misjudge both a quiet room and a hot input.
  const speechLevel = percentile(levels, 0.9);
  if (speechLevel < MIN_SPEECH_RMS) return null;

  const settled = speechLevel * SETTLED_RATIO;
  let rampFrames = -1;
  for (let f = 0; f + SETTLE_WINDOW <= levels.length; f++) {
    let above = 0;
    for (let k = f; k < f + SETTLE_WINDOW; k++) {
      if (levels[k] >= settled) above++;
    }
    if (above >= SETTLE_MIN_ABOVE) {
      rampFrames = f;
      break;
    }
  }
  if (rampFrames <= 0) return null; // already at level on the first frame

  const rampMs = rampFrames * FRAME_MS;
  if (rampMs < MIN_RAMP_MS || rampMs > MAX_RAMP_MS) return null;

  // Attenuated speech still swings between syllables and pauses; converter
  // noise in front of a silent start does not. That spread is what tells a
  // lost sentence apart from a user who hadn't started talking yet.
  const head = levels.subarray(0, rampFrames);
  const headPeak = percentile(head, 0.9);
  const headFloor = percentile(head, 0.1);
  if (headPeak <= 0 || headPeak < headFloor * MIN_HEAD_MODULATION) return null;

  // Never drive any part of the head into the ceiling: a clipped syllable
  // reads worse to Whisper than a quiet one, and the head is not uniformly
  // attenuated — the ramp is still climbing inside it.
  const endSample = rampFrames * frameSize;
  let headMax = 0;
  for (let i = 0; i < endSample; i++) {
    const magnitude = Math.abs(samples[i]);
    if (magnitude > headMax) headMax = magnitude;
  }

  const gain = Math.min(
    MAX_GAIN,
    (speechLevel * HEAD_TARGET_RATIO) / headPeak,
    headMax > 0 ? CLIP_CEILING / headMax : MAX_GAIN,
  );
  if (gain < MIN_GAIN) return null;

  return { gain, endSample };
}

function applyRampCorrection(
  samples: Float32Array,
  { gain, endSample }: RampCorrection,
  sampleRate: number,
): void {
  const fade = Math.min(Math.round((sampleRate * FADE_MS) / 1000), endSample);
  const plateau = endSample - fade;
  for (let i = 0; i < endSample; i++) {
    const factor = i < plateau ? gain : gain + (1 - gain) * ((i - plateau) / fade);
    samples[i] = Math.max(-1, Math.min(1, samples[i] * factor));
  }
}

/** The parts of an AudioBuffer the compensation touches. */
type RampBuffer = Pick<AudioBuffer, 'numberOfChannels' | 'sampleRate' | 'getChannelData'>;

/**
 * Lift an attenuated recording start in place. Returns whether a ramp was
 * found and corrected. Channel 0 decides for all channels so a stereo
 * capture keeps its balance.
 */
export function compensateInputRamp(buffer: RampBuffer): boolean {
  const plan = planRampCorrection(buffer.getChannelData(0), buffer.sampleRate);
  if (!plan) return false;

  for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
    applyRampCorrection(buffer.getChannelData(channel), plan, buffer.sampleRate);
  }
  return true;
}
