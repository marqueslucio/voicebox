import { useMutation } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';
import type { STTModel } from '@/lib/api/types';
import type { LanguageCode } from '@/lib/constants/languages';

export function useTranscription() {
  return useMutation({
    mutationFn: ({
      file,
      language,
      model,
    }: {
      file: File;
      language?: LanguageCode;
      model?: STTModel;
    }) => apiClient.transcribeAudio(file, language, model),
  });
}
