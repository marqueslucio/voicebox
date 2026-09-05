import type { GenerationResponse, StoryDeliverySettings, StoryItemDetail } from '@/lib/api/types';

export const DEFAULT_STORY_DELIVERY: StoryDeliverySettings = {
  enabled: false,
  profile_id: null,
  engine: 'qwen',
  model_size: '1.7B',
  language: 'de',
  speed: 1,
  gap_ms: 200,
  seed: null,
  energy: 0.5,
  warmth: 0.5,
  expression: 0.5,
};

export function normalizeStoryDelivery(
  settings?: StoryDeliverySettings | null,
): StoryDeliverySettings {
  const normalized = { ...DEFAULT_STORY_DELIVERY, ...settings };
  return normalized.engine === 'qwen_custom_voice' && normalized.model_size === '1.7B'
    ? normalized
    : { ...normalized, energy: 0.5, warmth: 0.5, expression: 0.5 };
}

export function resolveAudioVersionId(
  item: Pick<StoryItemDetail, 'version_id' | 'active_version_id'> | GenerationResponse,
): string | undefined {
  return 'version_id' in item && item.version_id
    ? item.version_id
    : (item.active_version_id ?? undefined);
}

export function reorderStoryItems(
  items: StoryItemDetail[],
  itemIds: string[],
  gapMs: number,
): StoryItemDetail[] {
  const byId = new Map(items.map((item) => [item.id, item]));
  let startTimeMs = 0;

  return itemIds.flatMap((id) => {
    const item = byId.get(id);
    if (!item) return [];
    const reordered = { ...item, start_time_ms: startTimeMs };
    startTimeMs +=
      Math.max(0, Math.floor(item.duration * 1000) - item.trim_start_ms - item.trim_end_ms) + gapMs;
    return [reordered];
  });
}
