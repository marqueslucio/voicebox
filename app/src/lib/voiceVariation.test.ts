import { describe, expect, it } from 'bun:test';
import {
  DEFAULT_VOICE_VARIATION,
  temperatureToVoiceVariation,
  voiceVariationToTemperature,
} from './voiceVariation';

describe('voice variation mapping', () => {
  it('maps the full 0–10 control range to the model temperature range', () => {
    expect(voiceVariationToTemperature(0)).toBe(0.3);
    expect(voiceVariationToTemperature(1)).toBe(0.405);
    expect(voiceVariationToTemperature(DEFAULT_VOICE_VARIATION)).toBe(0.93);
    expect(voiceVariationToTemperature(10)).toBe(1.35);
  });

  it('clamps and rounds control values before mapping them', () => {
    expect(voiceVariationToTemperature(-1)).toBe(0.3);
    expect(voiceVariationToTemperature(4.6)).toBe(0.825);
    expect(voiceVariationToTemperature(11)).toBe(1.35);
  });

  it('marks legacy non-grid temperatures as approximate', () => {
    expect(temperatureToVoiceVariation(0.93)).toEqual({ level: 6, approximate: false });
    expect(temperatureToVoiceVariation(0.9)).toEqual({ level: 6, approximate: true });
  });
});
