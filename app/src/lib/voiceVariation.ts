export const VOICE_VARIATION_MIN = 0;
export const VOICE_VARIATION_MAX = 10;
export const DEFAULT_VOICE_VARIATION = 6;

// Keep the model-facing range here so endpoint tuning never leaks into UI components.
export const VOICE_VARIATION_TEMPERATURE_MIN = 0.3;
export const VOICE_VARIATION_TEMPERATURE_MAX = 1.35;

export interface VoiceVariationLevel {
  level: number;
  approximate: boolean;
}

function clampLevel(level: number): number {
  return Math.min(VOICE_VARIATION_MAX, Math.max(VOICE_VARIATION_MIN, Math.round(level)));
}

export function voiceVariationToTemperature(level: number): number {
  const normalizedLevel = clampLevel(level);
  const ratio = normalizedLevel / (VOICE_VARIATION_MAX - VOICE_VARIATION_MIN);
  const temperature =
    VOICE_VARIATION_TEMPERATURE_MIN +
    ratio * (VOICE_VARIATION_TEMPERATURE_MAX - VOICE_VARIATION_TEMPERATURE_MIN);
  return Math.round(temperature * 1000) / 1000;
}

export function temperatureToVoiceVariation(temperature: number): VoiceVariationLevel {
  const ratio =
    (temperature - VOICE_VARIATION_TEMPERATURE_MIN) /
    (VOICE_VARIATION_TEMPERATURE_MAX - VOICE_VARIATION_TEMPERATURE_MIN);
  const level = clampLevel(ratio * (VOICE_VARIATION_MAX - VOICE_VARIATION_MIN));
  const gridTemperature = voiceVariationToTemperature(level);

  return {
    level,
    approximate: Math.abs(temperature - gridTemperature) > 0.005,
  };
}
