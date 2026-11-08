import { describe, expect, test } from 'bun:test';
import { compensateInputRamp } from './audio';

const SR = 48000;

/** Deterministic noise — a fixed seed keeps the gate tests reproducible. */
function noise(seconds: number, amp: number): Float32Array {
  const out = new Float32Array(Math.round(seconds * SR));
  let seed = 12345;
  for (let i = 0; i < out.length; i++) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    out[i] = (seed / 0x3fffffff - 1) * amp;
  }
  return out;
}

/** Speech stand-in: a tone under a syllable envelope, so frame levels swing
 *  between syllables the way real speech does. */
function speech(seconds: number, amp: number): Float32Array {
  const out = new Float32Array(Math.round(seconds * SR));
  for (let i = 0; i < out.length; i++) {
    const t = i / SR;
    const envelope = 0.5 + 0.5 * Math.sin(2 * Math.PI * 4 * t);
    out[i] = Math.sin(2 * Math.PI * 180 * t) * envelope * amp;
  }
  return out;
}

function concat(...parts: Float32Array[]): Float32Array {
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.length;
  }
  return out;
}

function buffer(samples: Float32Array, channels = 1) {
  const data = Array.from({ length: channels }, () => Float32Array.from(samples));
  return {
    numberOfChannels: channels,
    sampleRate: SR,
    getChannelData: (channel: number) => data[channel],
  };
}

function rms(samples: Float32Array, fromS: number, toS: number): number {
  let sum = 0;
  const from = Math.round(fromS * SR);
  const to = Math.round(toS * SR);
  for (let i = from; i < to; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / (to - from));
}

describe('compensateInputRamp', () => {
  test('lifts speech that the capture chain delivered attenuated', () => {
    // The measured shape: converter noise, then ~2s of speech 30 dB down,
    // then the chain settles and the rest arrives at full level.
    const samples = concat(noise(1, 0.0004), speech(2, 0.02), speech(10, 0.6));
    const buf = buffer(samples);
    const before = rms(buf.getChannelData(0), 1, 3);

    expect(compensateInputRamp(buf)).toBe(true);

    const after = rms(buf.getChannelData(0), 1, 3);
    expect(after / before).toBeGreaterThan(8);
    // Lifted towards the recording's own level, never past it.
    expect(after).toBeLessThan(rms(buf.getChannelData(0), 4, 10));
  });

  test('leaves a recording that starts at full level alone', () => {
    const samples = speech(12, 0.6);
    const buf = buffer(samples);
    expect(compensateInputRamp(buf)).toBe(false);
    expect(buf.getChannelData(0)).toEqual(samples);
  });

  test('leaves a genuinely silent start alone', () => {
    // Flat room tone in front of speech: nothing was lost, and amplifying
    // it would only feed Whisper hallucinations.
    const samples = concat(noise(2.5, 0.0006), speech(10, 0.6));
    const buf = buffer(samples);
    expect(compensateInputRamp(buf)).toBe(false);
    expect(buf.getChannelData(0)).toEqual(samples);
  });

  test('leaves a short ramp alone', () => {
    // Recordings that settle quickly don't lose words — staying out keeps
    // the compensation off the recordings that were already fine.
    const samples = concat(noise(0.4, 0.0004), speech(0.4, 0.02), speech(10, 0.6));
    const buf = buffer(samples);
    expect(compensateInputRamp(buf)).toBe(false);
  });

  test('leaves a long quiet passage alone', () => {
    // Beyond MAX_RAMP_MS this is not a ramp any more.
    const samples = concat(speech(8, 0.02), speech(10, 0.6));
    const buf = buffer(samples);
    expect(compensateInputRamp(buf)).toBe(false);
  });

  test('ignores a recording too short to judge', () => {
    const buf = buffer(speech(0.2, 0.6));
    expect(compensateInputRamp(buf)).toBe(false);
  });

  test('applies one gain across all channels', () => {
    const samples = concat(noise(1, 0.0004), speech(2, 0.02), speech(10, 0.6));
    const buf = buffer(samples, 2);
    expect(compensateInputRamp(buf)).toBe(true);
    expect(buf.getChannelData(1)).toEqual(buf.getChannelData(0));
  });
});
