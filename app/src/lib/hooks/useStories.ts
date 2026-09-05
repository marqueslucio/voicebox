import { type QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';
import type {
  StoryCreate,
  StoryDetailResponse,
  StoryItemBatchUpdate,
  StoryItemCreate,
  StoryItemMove,
  StoryItemReorder,
  StoryItemSplit,
  StoryItemTrim,
  StoryItemVersionUpdate,
  StoryItemVolumeUpdate,
} from '@/lib/api/types';
import { normalizeStoryDelivery, reorderStoryItems } from '@/lib/story';
import { usePlatform } from '@/platform/PlatformContext';

// ponytail: one global write lane prevents lost edits; use per-story scopes if
// concurrent multi-story editing ever becomes a real workflow.
const STORY_WRITE_SCOPE = { id: 'story-writes' };

type StoryMutationContext = { previous?: StoryDetailResponse };

async function snapshotStory(queryClient: QueryClient, storyId: string) {
  await queryClient.cancelQueries({ queryKey: ['stories', storyId], exact: true });
  return queryClient.getQueryData<StoryDetailResponse>(['stories', storyId]);
}

function updateStoryItem(
  queryClient: QueryClient,
  storyId: string,
  itemId: string,
  update: Partial<StoryDetailResponse['items'][number]>,
) {
  queryClient.setQueryData<StoryDetailResponse>(['stories', storyId], (story) =>
    story
      ? {
          ...story,
          items: story.items.map((item) => (item.id === itemId ? { ...item, ...update } : item)),
        }
      : story,
  );
}

function restoreStory(queryClient: QueryClient, storyId: string, context?: StoryMutationContext) {
  if (context?.previous) {
    queryClient.setQueryData(['stories', storyId], context.previous);
  }
}

async function refreshStory(queryClient: QueryClient, storyId: string) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['stories'], exact: true }),
    queryClient.invalidateQueries({ queryKey: ['stories', storyId], exact: true }),
  ]);
}

export function useStories() {
  return useQuery({
    queryKey: ['stories'],
    queryFn: () => apiClient.listStories(),
  });
}

export function useStory(storyId: string | null) {
  return useQuery({
    queryKey: ['stories', storyId],
    queryFn: () => apiClient.getStory(storyId!),
    enabled: !!storyId,
  });
}

export function useCreateStory() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: (data: StoryCreate) => apiClient.createStory(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });
}

export function useUpdateStory() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: ({ storyId, data }: { storyId: string; data: StoryCreate }) =>
      apiClient.updateStory(storyId, data),
    onSuccess: (updated, { storyId }) => {
      queryClient.setQueryData<StoryDetailResponse>(['stories', storyId], (story) =>
        story
          ? {
              ...story,
              name: updated.name,
              description: updated.description,
              delivery_settings: updated.delivery_settings,
              updated_at: updated.updated_at,
            }
          : story,
      );
      return refreshStory(queryClient, storyId);
    },
  });
}

export function useDeleteStory() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: (storyId: string) => apiClient.deleteStory(storyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['stories'] });
    },
  });
}

export function useAddStoryItem() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: ({ storyId, data }: { storyId: string; data: StoryItemCreate }) =>
      apiClient.addStoryItem(storyId, data),
    onSuccess: (item, { storyId }) => {
      queryClient.setQueryData<StoryDetailResponse>(['stories', storyId], (story) =>
        story ? { ...story, items: [...story.items, item] } : story,
      );
    },
    onSettled: (_, __, variables) => {
      return refreshStory(queryClient, variables.storyId);
    },
  });
}

export function useRemoveStoryItem() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: ({ storyId, itemId }: { storyId: string; itemId: string }) =>
      apiClient.removeStoryItem(storyId, itemId),
    onMutate: async ({ storyId, itemId }) => {
      const previous = await snapshotStory(queryClient, storyId);
      queryClient.setQueryData<StoryDetailResponse>(['stories', storyId], (story) =>
        story ? { ...story, items: story.items.filter((item) => item.id !== itemId) } : story,
      );
      return { previous };
    },
    onError: (_, variables, context) => restoreStory(queryClient, variables.storyId, context),
    onSettled: (_, __, variables) => {
      return refreshStory(queryClient, variables.storyId);
    },
  });
}

export function useUpdateStoryItemTimes() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: ({ storyId, data }: { storyId: string; data: StoryItemBatchUpdate }) =>
      apiClient.updateStoryItemTimes(storyId, data),
    onSettled: (_, __, variables) => {
      return refreshStory(queryClient, variables.storyId);
    },
  });
}

export function useReorderStoryItems() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: ({ storyId, data }: { storyId: string; data: StoryItemReorder }) =>
      apiClient.reorderStoryItems(storyId, data),
    onMutate: async ({ storyId, data }) => {
      const previous = await snapshotStory(queryClient, storyId);
      if (previous) {
        const gapMs = normalizeStoryDelivery(previous.delivery_settings).gap_ms;
        queryClient.setQueryData<StoryDetailResponse>(['stories', storyId], {
          ...previous,
          items: reorderStoryItems(previous.items, data.item_ids, gapMs),
        });
      }
      return { previous };
    },
    onError: (_, variables, context) => restoreStory(queryClient, variables.storyId, context),
    onSuccess: (items, { storyId }) => {
      queryClient.setQueryData<StoryDetailResponse>(['stories', storyId], (story) =>
        story ? { ...story, items } : story,
      );
    },
    onSettled: (_, __, variables) => {
      return refreshStory(queryClient, variables.storyId);
    },
  });
}

export function useMoveStoryItem() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: ({
      storyId,
      itemId,
      data,
    }: {
      storyId: string;
      itemId: string;
      data: StoryItemMove;
    }) => apiClient.moveStoryItem(storyId, itemId, data),
    onMutate: async ({ storyId, itemId, data }) => {
      const previous = await snapshotStory(queryClient, storyId);
      updateStoryItem(queryClient, storyId, itemId, data);
      return { previous };
    },
    onError: (_, variables, context) => restoreStory(queryClient, variables.storyId, context),
    onSuccess: (item, { storyId, itemId }) => updateStoryItem(queryClient, storyId, itemId, item),
    onSettled: (_, __, variables) => {
      return refreshStory(queryClient, variables.storyId);
    },
  });
}

export function useTrimStoryItem() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: ({
      storyId,
      itemId,
      data,
    }: {
      storyId: string;
      itemId: string;
      data: StoryItemTrim;
    }) => apiClient.trimStoryItem(storyId, itemId, data),
    onMutate: async ({ storyId, itemId, data }) => {
      const previous = await snapshotStory(queryClient, storyId);
      updateStoryItem(queryClient, storyId, itemId, data);
      return { previous };
    },
    onError: (_, variables, context) => restoreStory(queryClient, variables.storyId, context),
    onSuccess: (item, { storyId, itemId }) => updateStoryItem(queryClient, storyId, itemId, item),
    onSettled: (_, __, variables) => {
      return refreshStory(queryClient, variables.storyId);
    },
  });
}

export function useUpdateStoryItemVolume() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: ({
      storyId,
      itemId,
      data,
    }: {
      storyId: string;
      itemId: string;
      data: StoryItemVolumeUpdate;
    }) => apiClient.updateStoryItemVolume(storyId, itemId, data),
    onMutate: async ({ storyId, itemId, data }) => {
      const previous = await snapshotStory(queryClient, storyId);
      updateStoryItem(queryClient, storyId, itemId, data);
      return { previous };
    },
    onError: (_, variables, context) => restoreStory(queryClient, variables.storyId, context),
    onSuccess: (item, { storyId, itemId }) => updateStoryItem(queryClient, storyId, itemId, item),
    onSettled: (_, __, variables) => {
      return refreshStory(queryClient, variables.storyId);
    },
  });
}

export function useSplitStoryItem() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: ({
      storyId,
      itemId,
      data,
    }: {
      storyId: string;
      itemId: string;
      data: StoryItemSplit;
    }) => apiClient.splitStoryItem(storyId, itemId, data),
    onSuccess: (splitItems, { storyId, itemId }) => {
      queryClient.setQueryData<StoryDetailResponse>(['stories', storyId], (story) => {
        if (!story) return story;
        const index = story.items.findIndex((item) => item.id === itemId);
        if (index === -1) return story;
        const items = [...story.items];
        items.splice(index, 1, ...splitItems);
        return { ...story, items };
      });
    },
    onSettled: (_, __, variables) => {
      return refreshStory(queryClient, variables.storyId);
    },
  });
}

export function useDuplicateStoryItem() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: ({ storyId, itemId }: { storyId: string; itemId: string }) =>
      apiClient.duplicateStoryItem(storyId, itemId),
    onSuccess: (item, { storyId }) => {
      queryClient.setQueryData<StoryDetailResponse>(['stories', storyId], (story) =>
        story ? { ...story, items: [...story.items, item] } : story,
      );
    },
    onSettled: (_, __, variables) => {
      return refreshStory(queryClient, variables.storyId);
    },
  });
}

export function useSetStoryItemVersion() {
  const queryClient = useQueryClient();

  return useMutation({
    scope: STORY_WRITE_SCOPE,
    mutationFn: ({
      storyId,
      itemId,
      data,
    }: {
      storyId: string;
      itemId: string;
      data: StoryItemVersionUpdate;
    }) => apiClient.setStoryItemVersion(storyId, itemId, data),
    onMutate: async ({ storyId, itemId, data }) => {
      const previous = await snapshotStory(queryClient, storyId);
      updateStoryItem(queryClient, storyId, itemId, data);
      return { previous };
    },
    onError: (_, variables, context) => restoreStory(queryClient, variables.storyId, context),
    onSuccess: (item, { storyId, itemId }) => updateStoryItem(queryClient, storyId, itemId, item),
    onSettled: (_, __, variables) => {
      return refreshStory(queryClient, variables.storyId);
    },
  });
}

export function useExportStoryAudio() {
  const platform = usePlatform();

  return useMutation({
    mutationFn: async ({ storyId, storyName }: { storyId: string; storyName: string }) => {
      const blob = await apiClient.exportStoryAudio(storyId);

      // Create safe filename
      const safeName = storyName
        .substring(0, 50)
        .replace(/[^a-z0-9]/gi, '-')
        .toLowerCase();
      const filename = `${safeName || 'story'}.wav`;

      await platform.filesystem.saveFile(filename, blob, [
        {
          name: 'Audio File',
          extensions: ['wav'],
        },
      ]);

      return blob;
    },
  });
}
