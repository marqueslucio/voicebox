import { expect, test } from 'bun:test';

test('legacy keep-running settings cannot orphan the dictation server after quit', async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: () => JSON.stringify({
        state: {
          serverUrl: 'http://127.0.0.1:17493',
          mode: 'remote',
          keepServerRunningOnClose: true,
          customModelsDir: '/custom/models',
        },
        version: 0,
      }),
      setItem: () => {},
      removeItem: () => {},
    },
  });
  try {
    const { useServerStore } = await import('./serverStore');
    await useServerStore.persist.rehydrate();
    const state = useServerStore.getState();
    expect(state.keepServerRunningOnClose).toBe(false);
    expect(state.mode).toBe('local');
    expect(state.serverUrl).toBe('http://127.0.0.1:17495');
    expect(state.customModelsDir).toBe('/custom/models');
  } finally {
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous);
    else Reflect.deleteProperty(globalThis, 'localStorage');
  }
});
