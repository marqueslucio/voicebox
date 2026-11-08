export interface ServerLogEntry {
  stream: 'stdout' | 'stderr';
  line: string;
}

export interface PlatformLifecycle {
  startServer(remote?: boolean, modelsDir?: string | null): Promise<string>;
  stopServer(): Promise<void>;
  restartServer(modelsDir?: string | null): Promise<string>;
  setKeepServerRunning(keep: boolean): Promise<void>;
  setBackendOverride(backend?: string | null): Promise<void>;
  setupWindowCloseHandler(): Promise<void>;
  subscribeToServerLogs(callback: (entry: ServerLogEntry) => void): () => void;
  onServerReady?: () => void;
}

export interface PlatformMetadata {
  getVersion(): Promise<string>;
  isTauri: boolean;
}

export interface Platform {
  lifecycle: PlatformLifecycle;
  metadata: PlatformMetadata;
}
