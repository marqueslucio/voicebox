import type { Platform } from '@/platform/types';
import { tauriLifecycle } from './lifecycle';
import { tauriMetadata } from './metadata';

export const tauriPlatform: Platform = {
  lifecycle: tauriLifecycle,
  metadata: tauriMetadata,
};
