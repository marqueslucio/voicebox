import { expect, test } from 'bun:test';
import { getSTTModelDisplayName, isSTTLanguageSupported } from './stt';

test('Parakeet labels and language support preserve Whisper and automatic detection', () => {
  expect(getSTTModelDisplayName('parakeet-v3')).toBe('Parakeet v3 (Q8)');
  expect(getSTTModelDisplayName('turbo')).toBe('Whisper Turbo');
  expect(getSTTModelDisplayName('unknown')).toBe('unknown');
  expect(getSTTModelDisplayName('toString')).toBe('toString');
  expect(isSTTLanguageSupported('parakeet-v3', 'de')).toBe(true);
  expect(isSTTLanguageSupported('parakeet-v3', 'uk')).toBe(true);
  expect(isSTTLanguageSupported('parakeet-v3', 'auto')).toBe(true);
  for (const language of ['ja', 'zh', 'hi', 'ko']) {
    expect(isSTTLanguageSupported('parakeet-v3', language)).toBe(false);
    expect(isSTTLanguageSupported('turbo', language)).toBe(true);
  }
});
