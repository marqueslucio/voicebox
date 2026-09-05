import { expect, mock, test } from 'bun:test';
import { QueryObserver } from '@tanstack/react-query';

mock.module('virtual:changelog', () => ({ default: [] }));

const { notifyServerReady } = await import('./App');
const { queryClient } = await import('@/lib/queryClient');

test('refetches failed startup queries when the backend becomes ready', async () => {
  const queryKey = ['test', 'server-ready'];
  let backendReady = false;
  let calls = 0;
  const observer = new QueryObserver(queryClient, {
    queryKey,
    queryFn: async () => {
      calls += 1;
      if (!backendReady) throw new Error('server not ready');
      return 'ready';
    },
    retry: false,
    staleTime: Infinity,
  });

  const initialFailure = new Promise<void>((resolve) => {
    const unsubscribe = observer.subscribe((result) => {
      if (result.isError) {
        unsubscribe();
        resolve();
      }
    });
  });
  await initialFailure;

  backendReady = true;
  let appReady = false;
  const refetched = new Promise<void>((resolve) => {
    const unsubscribe = observer.subscribe((result) => {
      if (result.data === 'ready') {
        unsubscribe();
        resolve();
      }
    });
  });

  notifyServerReady(() => {
    appReady = true;
  });
  await refetched;

  expect(appReady).toBe(true);
  expect(calls).toBe(2);
  queryClient.removeQueries({ queryKey });
});
