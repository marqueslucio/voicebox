import { useServerStore } from '@/stores/serverStore';
import type {
  CaptureCreateResponse,
  CaptureListResponse,
  CaptureReadinessResponse,
  CaptureSettings,
  CaptureSettingsUpdate,
  CaptureSource,
  HealthResponse,
  ModelStatusListResponse,
} from './types';

function detail(value: unknown, fallback: string): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') return JSON.stringify(value);
  return fallback;
}

class ApiClient {
  private getBaseUrl() {
    return useServerStore.getState().serverUrl;
  }
  private async request<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`${this.getBaseUrl()}${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...options?.headers },
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(detail(error.detail, `HTTP ${response.status}`));
    }
    return response.json();
  }
  getHealth() {
    return this.request<HealthResponse>('/health');
  }
  listCaptures(limit = 50, offset = 0) {
    return this.request<CaptureListResponse>(`/captures?limit=${limit}&offset=${offset}`);
  }
  async createCapture(
    file: File,
    options: { source: CaptureSource },
  ): Promise<CaptureCreateResponse> {
    const body = new FormData();
    body.append('file', file);
    body.append('source', options.source);
    const response = await fetch(`${this.getBaseUrl()}/captures`, { method: 'POST', body });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(detail(error.detail, `HTTP ${response.status}`));
    }
    return response.json();
  }
  getCaptureSettings() {
    return this.request<CaptureSettings>('/settings/captures');
  }
  getCaptureReadiness() {
    return this.request<CaptureReadinessResponse>('/capture/readiness');
  }
  updateCaptureSettings(patch: CaptureSettingsUpdate) {
    return this.request<CaptureSettings>('/settings/captures', {
      method: 'PUT',
      body: JSON.stringify(patch),
    });
  }
  getModelStatus() {
    return this.request<ModelStatusListResponse>('/models/status');
  }
  triggerModelDownload(name: string) {
    return this.request('/models/download', {
      method: 'POST',
      body: JSON.stringify({ model_name: name }),
    });
  }
  deleteModel(name: string) {
    return this.request(`/models/${name}`, { method: 'DELETE' });
  }
  unloadModel(name: string) {
    return this.request(`/models/${name}/unload`, { method: 'POST' });
  }
}
export const apiClient = new ApiClient();
