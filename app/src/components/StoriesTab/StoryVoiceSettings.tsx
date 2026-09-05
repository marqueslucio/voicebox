import { Loader2, Settings2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ENGINE_OPTIONS,
  getEngineModelSelectValue,
  isProfileCompatibleWithEngine,
} from '@/components/Generation/EngineModelSelector';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { useToast } from '@/components/ui/use-toast';
import type {
  GenerationRequest,
  StoryDeliverySettings,
  StoryDetailResponse,
} from '@/lib/api/types';
import { getLanguageOptionsForEngine, type LanguageCode } from '@/lib/constants/languages';
import { useProfiles } from '@/lib/hooks/useProfiles';
import { useUpdateStory } from '@/lib/hooks/useStories';
import { normalizeStoryDelivery } from '@/lib/story';
import { cn } from '@/lib/utils/cn';

interface StoryVoiceSettingsProps {
  story: StoryDetailResponse;
}

export function StoryVoiceSettings({ story }: StoryVoiceSettingsProps) {
  const { t } = useTranslation();
  const { toast } = useToast();
  const { data: profiles } = useProfiles();
  const updateStory = useUpdateStory();
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState(() => normalizeStoryDelivery(story.delivery_settings));
  const [error, setError] = useState<string | null>(null);

  // story.id is intentional: switching between two unsaved/default stories
  // must reset local draft state even when both settings values are undefined.
  // biome-ignore lint/correctness/useExhaustiveDependencies: see above
  useEffect(() => {
    setSettings(normalizeStoryDelivery(story.delivery_settings));
    setError(null);
  }, [story.id, story.delivery_settings]);

  const compatibleProfiles = profiles?.filter((profile) =>
    isProfileCompatibleWithEngine(profile, settings.engine),
  );
  const languages = getLanguageOptionsForEngine(settings.engine);
  const engineModelValue = getEngineModelSelectValue(settings.engine, settings.model_size);
  const supportsExpression =
    settings.engine === 'qwen_custom_voice' && settings.model_size === '1.7B';

  const setEngineModel = (value: string) => {
    const [engine, requestedModel] = value.split(':') as [
      NonNullable<GenerationRequest['engine']>,
      StoryDeliverySettings['model_size'] | undefined,
    ];
    const modelSize = requestedModel ?? (engine === 'tada' ? '1B' : '1.7B');
    const nextLanguages = getLanguageOptionsForEngine(engine);
    const currentProfile = profiles?.find((profile) => profile.id === settings.profile_id);

    setSettings((current) => ({
      ...current,
      engine,
      model_size: modelSize,
      language: nextLanguages.some((language) => language.value === current.language)
        ? current.language
        : (nextLanguages[0]?.value ?? 'en'),
      profile_id:
        currentProfile && isProfileCompatibleWithEngine(currentProfile, engine)
          ? current.profile_id
          : null,
      energy: engine === 'qwen_custom_voice' && modelSize === '1.7B' ? current.energy : 0.5,
      warmth: engine === 'qwen_custom_voice' && modelSize === '1.7B' ? current.warmth : 0.5,
      expression: engine === 'qwen_custom_voice' && modelSize === '1.7B' ? current.expression : 0.5,
    }));
  };

  const save = async () => {
    if (settings.enabled && !settings.profile_id) {
      setError(t('storyContent.voice.profileRequired'));
      return;
    }

    setError(null);
    try {
      await updateStory.mutateAsync({
        storyId: story.id,
        data: {
          name: story.name,
          description: story.description,
          delivery_settings: normalizeStoryDelivery(settings),
        },
      });
      toast({ title: t('storyContent.voice.saved') });
      setOpen(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : t('storyContent.voice.saveFailed'));
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn(story.delivery_settings?.enabled && 'border-accent text-accent')}
          aria-label={t('storyContent.voice.open')}
        >
          <Settings2 data-icon="inline-start" />
          {t('storyContent.voice.button')}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-[min(28rem,calc(100vw-2rem))] max-h-[75vh] overflow-y-auto"
      >
        <div className="flex flex-col gap-4">
          <div>
            <h3 className="font-semibold">{t('storyContent.voice.title')}</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {t('storyContent.voice.description')}
            </p>
          </div>

          <div className="flex items-start gap-3">
            <Checkbox
              id="story-voice-enabled"
              checked={settings.enabled}
              onCheckedChange={(enabled) => setSettings((current) => ({ ...current, enabled }))}
            />
            <div className="flex min-w-0 flex-col gap-1">
              <Label htmlFor="story-voice-enabled">{t('storyContent.voice.enabled')}</Label>
              <p className="text-xs text-muted-foreground">{t('storyContent.voice.enabledHint')}</p>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label>{t('storyContent.voice.profile')}</Label>
              <Select
                value={settings.profile_id ?? ''}
                onValueChange={(profile_id) =>
                  setSettings((current) => ({ ...current, profile_id }))
                }
              >
                <SelectTrigger aria-label={t('storyContent.voice.profile')}>
                  <SelectValue placeholder={t('storyContent.voice.selectProfile')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {compatibleProfiles?.map((profile) => (
                      <SelectItem key={profile.id} value={profile.id}>
                        {profile.name}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>{t('storyContent.voice.engine')}</Label>
              <Select value={engineModelValue} onValueChange={setEngineModel}>
                <SelectTrigger aria-label={t('storyContent.voice.engine')}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {ENGINE_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <Label>{t('storyContent.voice.language')}</Label>
              <Select
                value={settings.language}
                onValueChange={(language) =>
                  setSettings((current) => ({ ...current, language: language as LanguageCode }))
                }
              >
                <SelectTrigger aria-label={t('storyContent.voice.language')}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {languages.map((language) => (
                      <SelectItem key={language.value} value={language.value}>
                        {language.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>
          </div>

          <DeliverySlider
            id="story-speed"
            label={t('storyContent.voice.speed')}
            value={settings.speed}
            min={0.75}
            max={1.25}
            step={0.01}
            display={`${settings.speed.toFixed(2)}×`}
            hint={t('storyContent.voice.speedHint')}
            onChange={(speed) => setSettings((current) => ({ ...current, speed }))}
          />
          <DeliverySlider
            id="story-gap"
            label={t('storyContent.voice.gap')}
            value={settings.gap_ms}
            min={0}
            max={1500}
            step={25}
            display={`${settings.gap_ms} ms`}
            hint={t('storyContent.voice.gapHint')}
            onChange={(gap_ms) => setSettings((current) => ({ ...current, gap_ms }))}
          />

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="story-seed">{t('storyContent.voice.seed')}</Label>
            <Input
              id="story-seed"
              type="number"
              min={0}
              step={1}
              value={settings.seed ?? ''}
              placeholder={t('storyContent.voice.seedPlaceholder')}
              onChange={(event) =>
                setSettings((current) => ({
                  ...current,
                  seed:
                    event.target.value === ''
                      ? null
                      : Math.max(0, Math.floor(event.target.valueAsNumber)),
                }))
              }
            />
            <p className="text-xs text-muted-foreground">{t('storyContent.voice.seedHint')}</p>
          </div>

          {supportsExpression ? (
            <div className="flex flex-col gap-4">
              {(['energy', 'warmth', 'expression'] as const).map((field) => (
                <DeliverySlider
                  key={field}
                  id={`story-${field}`}
                  label={t(`storyContent.voice.${field}`)}
                  value={settings[field]}
                  min={0}
                  max={1}
                  step={0.05}
                  display={settings[field].toFixed(2)}
                  onChange={(value) => setSettings((current) => ({ ...current, [field]: value }))}
                />
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t('storyContent.voice.expressionUnavailable')}
            </p>
          )}

          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button type="button" onClick={save} disabled={updateStory.isPending}>
              {updateStory.isPending && (
                <Loader2 data-icon="inline-start" className="animate-spin" />
              )}
              {updateStory.isPending ? t('storyContent.voice.saving') : t('common.save')}
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function DeliverySlider({
  id,
  label,
  value,
  display,
  hint,
  ...props
}: {
  id: string;
  label: string;
  value: number;
  display: string;
  hint?: string;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <Label htmlFor={id}>{label}</Label>
        <output htmlFor={id} className="text-xs tabular-nums text-muted-foreground">
          {display}
        </output>
      </div>
      <Slider
        id={id}
        value={[value]}
        min={props.min}
        max={props.max}
        step={props.step}
        onValueChange={([next]) => props.onChange(next)}
        aria-label={`${label}: ${display}`}
      />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
