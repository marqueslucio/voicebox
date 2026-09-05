import { describe, expect, test } from 'bun:test';
import type { StoryItemDetail } from '@/lib/api/types';
import { reorderStoryItems, resolveAudioVersionId } from '@/lib/story';

const item = (id: string, duration: number, trimStart = 0, trimEnd = 0) =>
  ({
    id,
    duration,
    trim_start_ms: trimStart,
    trim_end_ms: trimEnd,
  }) as StoryItemDetail;

describe('story audio identity', () => {
  test('a pin wins, otherwise the current default version is used', () => {
    expect(resolveAudioVersionId({ version_id: 'pinned', active_version_id: 'latest' })).toBe(
      'pinned',
    );
    expect(resolveAudioVersionId({ active_version_id: 'latest' })).toBe('latest');
  });
});

describe('story reorder', () => {
  test('uses item identity and trim-aware server timing', () => {
    const reordered = reorderStoryItems(
      [item('a', 1.9999, 100, 200), item('b', 2)],
      ['b', 'a'],
      200,
    );
    expect(reordered.map(({ id, start_time_ms }) => [id, start_time_ms])).toEqual([
      ['b', 0],
      ['a', 2200],
    ]);
  });
});
