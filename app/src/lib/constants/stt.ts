import type { STTModel } from '@/lib/api/types';

const STT_MODEL_NAMES = new Map([
  ['base', 'Whisper Base'],
  ['small', 'Whisper Small'],
  ['medium', 'Whisper Medium'],
  ['large', 'Whisper Large'],
  ['turbo', 'Whisper Turbo'],
  ['parakeet-v3', 'Parakeet v3 (Q8)'],
]);

const PARAKEET_LANGUAGES = new Set([
  'bg', 'hr', 'cs', 'da', 'nl', 'en', 'et', 'fi', 'fr', 'de', 'el', 'hu', 'it',
  'lv', 'lt', 'mt', 'pl', 'pt', 'ro', 'sk', 'sl', 'es', 'sv', 'ru', 'uk',
]);

export function getSTTModelDisplayName(model: string): string {
  return STT_MODEL_NAMES.get(model) ?? model;
}

export function isSTTLanguageSupported(model: STTModel, language: string): boolean {
  return model !== 'parakeet-v3' || language === 'auto' || PARAKEET_LANGUAGES.has(language);
}
