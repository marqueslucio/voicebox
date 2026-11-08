import { expect, test } from 'bun:test';
import { parseRetention } from './retention';

test('retention accepts only whole, positive periods within the server limits', () => {
  for (const value of ['', ' ', '0', '-1', '1.5', '1e2', '876001', '9007199254740992']) {
    expect(parseRetention(value, 876000)).toBeNull();
  }
  expect(parseRetention('24', 876000)).toBe(24);
  expect(parseRetention('876000', 876000)).toBe(876000);
  expect(parseRetention('7', 36500)).toBe(7);
  expect(parseRetention('36501', 36500)).toBeNull();
});
