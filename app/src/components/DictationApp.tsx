import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { Check, Copy, Mic, Settings2, Square } from 'lucide-react';
import { useEffect, useState } from 'react';
import logo from '@/assets/voicebox-logo.png';
import { AccessibilityNotice } from '@/components/AccessibilityGate/AccessibilityGate';
import { InputMonitoringNotice } from '@/components/InputMonitoringGate/InputMonitoringGate';
import { ChordPicker } from '@/components/ChordPicker/ChordPicker';
import { TitleBarDragRegion } from '@/components/TitleBarDragRegion';
import { apiClient } from '@/lib/api/client';
import type { STTModel } from '@/lib/api/types';
import { isSTTLanguageSupported } from '@/lib/constants/stt';
import { useCaptureRecordingSession } from '@/lib/hooks/useCaptureRecordingSession';
import { useDictationReadiness } from '@/lib/hooks/useDictationReadiness';
import { useCaptureSettings } from '@/lib/hooks/useSettings';
import { defaultChordKeys, displayLabelForKey } from '@/lib/utils/keyCodes';

const button =
  'rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50';

export function DictationApp() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState<'history' | 'settings'>('history');
  const [historyPage, setHistoryPage] = useState(0);
  const [copied, setCopied] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chord, setChord] = useState<'push' | 'toggle' | null>(null);
  const [deleteModel, setDeleteModel] = useState<string | null>(null);
  const { settings, update } = useCaptureSettings();
  const readiness = useDictationReadiness();
  const captures = useQuery({
    queryKey: ['captures', historyPage],
    queryFn: () => apiClient.listCaptures(50, historyPage * 50),
  });
  const models = useQuery({
    queryKey: ['model-status'],
    queryFn: () => apiClient.getModelStatus(),
    refetchInterval: 3000,
  });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['model-status'] });
    queryClient.invalidateQueries({ queryKey: ['capture-readiness'] });
  };
  const modelAction = useMutation({
    mutationFn: async ({
      name,
      action,
    }: {
      name: string;
      action: 'download' | 'delete' | 'unload';
    }) => {
      if (action === 'download') return apiClient.triggerModelDownload(name);
      if (action === 'unload') return apiClient.unloadModel(name);
      return apiClient.deleteModel(name);
    },
    onSuccess: () => {
      setDeleteModel(null);
      refresh();
    },
    onError: (e) => setError(String(e)),
  });
  const copy = async (text: string, id: string) => {
    try {
      await invoke('paste_final_text', { text, focus: null, allowAutoPaste: false });
      setCopied(id);
      setError(null);
    } catch (e) {
      setError(`Copy failed: ${String(e)}`);
    }
  };
  const session = useCaptureRecordingSession({
    onCaptureCreated: () => setHistoryPage(0),
    onFinalText: (text, capture) => {
      void copy(text, capture.id);
    },
  });
  useEffect(() => {
    const unlisten = listen('capture:created', () => {
      setHistoryPage(0);
      queryClient.invalidateQueries({ queryKey: ['captures'] });
    });
    return () => {
      void unlisten.then((fn) => fn());
    };
  }, [queryClient]);
  const pushKeys = settings?.chord_push_to_talk_keys ?? defaultChordKeys('push');
  const toggleKeys = settings?.chord_toggle_to_talk_keys ?? defaultChordKeys('toggle');
  const sttModel = settings?.stt_model ?? 'turbo';
  const language = settings?.language ?? 'auto';

  return (
    <div className="h-screen flex flex-col bg-background text-foreground overflow-hidden pt-8">
      <TitleBarDragRegion />
      <header className="flex items-center justify-between px-6 py-4 border-b border-border">
        <div className="flex items-center gap-3">
          <img src={logo} alt="" className="h-10 w-10 object-contain" />
          <div>
            <h1 className="font-semibold text-lg leading-tight">Voicebox</h1>
            <p className="text-xs text-muted-foreground">Private, local dictation</p>
          </div>
        </div>
        <nav className="flex gap-1 rounded-lg bg-muted p-1" aria-label="Navigation">
          <button
            className={`${button} border-0 ${page === 'history' ? 'bg-background shadow-sm' : ''}`}
            onClick={() => setPage('history')}
          >
            Dictation
          </button>
          <button
            className={`${button} border-0 ${page === 'settings' ? 'bg-background shadow-sm' : ''}`}
            onClick={() => setPage('settings')}
          >
            <Settings2 className="w-4 h-4" />
            <span className="sr-only">Settings</span>
          </button>
        </nav>
      </header>
      {error && (
        <div
          role="alert"
          className="mx-6 mt-4 rounded-lg bg-destructive/10 p-3 text-sm text-destructive"
        >
          {error}
          <button className="float-right" onClick={() => setError(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}
      <main className="flex-1 overflow-y-auto px-6 py-6">
        {page === 'history' ? (
          <>
            <section className="rounded-xl border border-border bg-muted/30 p-5 flex items-center justify-between gap-4">
              <div>
                <h2 className="font-semibold">
                  {session.pillState === 'starting'
                    ? 'Starting microphone…'
                    : session.isRecording
                      ? 'Listening…'
                      : session.isBusy
                        ? 'Transcribing…'
                        : 'Ready to dictate'}
                </h2>
                <p className="text-sm text-muted-foreground mt-1">
                  {session.errorMessage ||
                    (readiness.stt?.ready
                      ? 'Your words stay on this Mac.'
                      : 'Download a transcription model in Settings to begin.')}
                </p>
              </div>
              <button
                className="rounded-full bg-primary text-primary-foreground p-4 disabled:opacity-50"
                aria-label={session.isRecording ? 'Stop recording' : 'Start recording'}
                disabled={
                  !session.isRecording &&
                  (session.isBusy || session.pillState === 'starting' || !readiness.stt?.ready)
                }
                onClick={session.toggleRecording}
              >
                {session.isRecording ? <Square className="h-5 w-5" /> : <Mic className="h-5 w-5" />}
              </button>
            </section>
            <div className="flex items-baseline justify-between mt-8 mb-3">
              <h2 className="font-semibold">History</h2>
              <span className="text-xs text-muted-foreground">
                {captures.data?.total ?? 0} captures
              </span>
            </div>
            {captures.isError && (
              <p role="alert" className="text-sm text-destructive">
                Could not load history.
              </p>
            )}
            {captures.isLoading && (
              <p className="text-sm text-muted-foreground">Loading history…</p>
            )}
            {captures.data?.items.length === 0 && (
              <p className="text-sm text-muted-foreground py-6">
                Your dictations will appear here.
              </p>
            )}
            <div className="divide-y divide-border">
              {captures.data?.items.map((capture) => {
                const text = capture.transcript_refined || capture.transcript_raw;
                return (
                  <article key={capture.id} className="py-4 flex gap-4 items-start">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm whitespace-pre-wrap break-words">
                        {text || 'No transcript'}
                      </p>
                      <p className="text-xs text-muted-foreground mt-2">
                        {new Date(capture.created_at).toLocaleString()}
                      </p>
                    </div>
                    <button
                      className={button}
                      disabled={!text}
                      onClick={() => void copy(text, capture.id)}
                      aria-label="Copy transcript"
                    >
                      {copied === capture.id ? (
                        <Check className="w-4 h-4" />
                      ) : (
                        <Copy className="w-4 h-4" />
                      )}
                    </button>
                  </article>
                );
              })}
            </div>
            {(captures.data?.total ?? 0) > 50 && (
              <div className="flex items-center justify-between pt-5">
                <button
                  className={button}
                  disabled={historyPage === 0}
                  onClick={() => setHistoryPage((n) => n - 1)}
                >
                  Newer
                </button>
                <span className="text-xs text-muted-foreground">
                  Page {historyPage + 1} of {Math.ceil((captures.data?.total ?? 0) / 50)}
                </span>
                <button
                  className={button}
                  disabled={(historyPage + 1) * 50 >= (captures.data?.total ?? 0)}
                  onClick={() => setHistoryPage((n) => n + 1)}
                >
                  Older
                </button>
              </div>
            )}
          </>
        ) : (
          <div className="space-y-8">
            <section>
              <h2 className="font-semibold text-lg">Transcription</h2>
              <p className="text-sm text-muted-foreground mt-1">
                Choose a local model and language.
              </p>
              <label className="block text-sm mt-4">
                Model
                <select
                  className="mt-1 w-full rounded-lg border border-border bg-background p-2"
                  value={sttModel}
                  onChange={(e) => {
                    const model = e.target.value as STTModel;
                    update({
                      stt_model: model,
                      language: isSTTLanguageSupported(model, language) ? language : 'auto',
                    });
                  }}
                >
                  <option value="parakeet-v3">Parakeet v3 (Q8)</option>
                  <option value="base">Whisper Base</option>
                  <option value="small">Whisper Small</option>
                  <option value="medium">Whisper Medium</option>
                  <option value="large">Whisper Large</option>
                  <option value="turbo">Whisper Turbo</option>
                </select>
              </label>
              <label className="block text-sm mt-4">
                Language
                <select
                  className="mt-1 w-full rounded-lg border border-border bg-background p-2"
                  value={language}
                  onChange={(e) => update({ language: e.target.value })}
                >
                  <option value="auto">Automatic</option>
                  <option value="de">German</option>
                  <option value="en">English</option>
                  <option value="es">Spanish</option>
                  <option value="fr">French</option>
                  {!['auto', 'de', 'en', 'es', 'fr', 'ja', 'zh', 'hi'].includes(language) && (
                    <option value={language}>{language}</option>
                  )}
                  {isSTTLanguageSupported(sttModel, 'ja') && <option value="ja">Japanese</option>}
                  {isSTTLanguageSupported(sttModel, 'zh') && <option value="zh">Chinese</option>}
                  {isSTTLanguageSupported(sttModel, 'hi') && <option value="hi">Hindi</option>}
                </select>
              </label>
              <div className="mt-5 divide-y divide-border">
                {models.data?.models.map((model) => (
                  <div
                    key={model.model_name}
                    className="py-3 flex items-center justify-between gap-3"
                  >
                    <div>
                      <p className="text-sm font-medium">{model.display_name}</p>
                      <p className="text-xs text-muted-foreground">
                        {model.downloading
                          ? 'Downloading…'
                          : model.loaded
                            ? 'Loaded'
                            : model.downloaded
                              ? 'Downloaded'
                              : `${model.size_mb ?? '?'} MB`}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      {model.loaded && (
                        <button
                          className={button}
                          disabled={modelAction.isPending}
                          onClick={() =>
                            modelAction.mutate({ name: model.model_name, action: 'unload' })
                          }
                        >
                          Unload
                        </button>
                      )}
                      {model.downloaded ? (
                        deleteModel === model.model_name ? (
                          <>
                            <button className={button} onClick={() => setDeleteModel(null)}>
                              Cancel
                            </button>
                            <button
                              className={button}
                              disabled={modelAction.isPending}
                              onClick={() =>
                                modelAction.mutate({ name: model.model_name, action: 'delete' })
                              }
                            >
                              Confirm delete
                            </button>
                          </>
                        ) : (
                          <button
                            className={button}
                            disabled={model.loaded || modelAction.isPending}
                            onClick={() => setDeleteModel(model.model_name)}
                          >
                            Delete
                          </button>
                        )
                      ) : (
                        <button
                          className={button}
                          disabled={model.downloading || modelAction.isPending}
                          onClick={() =>
                            modelAction.mutate({ name: model.model_name, action: 'download' })
                          }
                        >
                          Download
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              {models.isError && (
                <p role="alert" className="text-sm text-destructive">
                  Could not load models.
                </p>
              )}
            </section>
            <section>
              <h2 className="font-semibold text-lg">Shortcuts & permissions</h2>
              <label className="flex items-center justify-between py-4 text-sm">
                Global shortcuts
                <input
                  type="checkbox"
                  checked={settings?.hotkey_enabled ?? false}
                  onChange={(e) => update({ hotkey_enabled: e.target.checked })}
                />
              </label>
              <InputMonitoringNotice enabled={settings?.hotkey_enabled ?? false} />
              {(['push', 'toggle'] as const).map((kind) => (
                <div className="flex items-center justify-between py-3 text-sm" key={kind}>
                  <span>{kind === 'push' ? 'Hold to dictate' : 'Toggle dictation'}</span>
                  <button className={button} onClick={() => setChord(kind)}>
                    {(kind === 'push' ? pushKeys : toggleKeys).map(displayLabelForKey).join(' + ')}
                  </button>
                </div>
              ))}
              <ChordPicker
                open={chord !== null}
                title={chord === 'push' ? 'Hold to dictate' : 'Toggle dictation'}
                description="Press your preferred shortcut."
                initialKeys={chord === 'push' ? pushKeys : toggleKeys}
                onCancel={() => setChord(null)}
                onSave={(keys) => {
                  if (chord === 'push') update({ chord_push_to_talk_keys: keys });
                  else update({ chord_toggle_to_talk_keys: keys });
                  setChord(null);
                }}
              />
              <label className="flex items-center justify-between py-4 text-sm">
                Paste into the focused app
                <input
                  type="checkbox"
                  checked={settings?.allow_auto_paste ?? true}
                  onChange={(e) => update({ allow_auto_paste: e.target.checked })}
                />
              </label>
              <AccessibilityNotice />
              <p className="text-xs text-muted-foreground mt-4">
                Microphone access is managed in macOS System Settings → Privacy &amp; Security →
                Microphone.
              </p>
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
