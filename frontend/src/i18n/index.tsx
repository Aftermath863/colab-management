import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import { en, TranslationDictionary } from './en';
import { zh } from './zh';
import { Locale, translateErrorMessage } from './errorMap';

export type { Locale, TranslationDictionary };
export { en, zh, translateErrorMessage };

export const STORAGE_KEY = 'colab_lang';

let activeRuntimeLocale: Locale | null = null;

type DotNestedKeys<T, Prefix extends string = ''> = {
  [K in keyof T & string]: T[K] extends string
    ? `${Prefix}${K}`
    : DotNestedKeys<T[K], `${Prefix}${K}.`>;
}[keyof T & string];

export type TranslationKey = DotNestedKeys<typeof en>;

export type InterpolationParams = Record<string, string | number | undefined | null>;

export type LocalizedText =
  | { key: TranslationKey; params?: InterpolationParams }
  | { raw: string };

const DICTIONARIES: Record<Locale, TranslationDictionary> = {
  en,
  zh,
};

export function normalizeLocale(raw?: string | null): Locale | null {
  if (!raw || typeof raw !== 'string') return null;
  const trimmed = raw.trim().toLowerCase();
  if (!trimmed) return null;
  if (trimmed === 'zh' || trimmed.startsWith('zh-') || trimmed.startsWith('zh_')) {
    return 'zh';
  }
  if (trimmed === 'en' || trimmed.startsWith('en-') || trimmed.startsWith('en_')) {
    return 'en';
  }
  return null;
}

export function detectInitialLocale(
  storageValue?: string | null,
  browserLanguages?: readonly string[] | string | null
): Locale {
  const fromStorage = normalizeLocale(storageValue);
  if (fromStorage) return fromStorage;

  if (Array.isArray(browserLanguages)) {
    for (const candidate of browserLanguages) {
      const normalized = normalizeLocale(candidate);
      if (normalized) return normalized;
    }
  } else if (typeof browserLanguages === 'string') {
    const normalized = normalizeLocale(browserLanguages);
    if (normalized) return normalized;
  }

  return 'en';
}

export function safeReadStorage(key: string): string | null {
  try {
    if (typeof window !== 'undefined' && typeof window.localStorage?.getItem === 'function') {
      return window.localStorage.getItem(key);
    }
  } catch {
    // Ignore storage access errors (e.g. private browsing / sandboxed iframe)
  }
  return null;
}

export function safeWriteStorage(key: string, value: string): void {
  try {
    if (typeof window !== 'undefined' && typeof window.localStorage?.setItem === 'function') {
      window.localStorage.setItem(key, value);
    }
  } catch {
    // Ignore storage write errors
  }
}

export function safeRemoveStorage(key: string): void {
  try {
    if (typeof window !== 'undefined' && typeof window.localStorage?.removeItem === 'function') {
      window.localStorage.removeItem(key);
    }
  } catch {
    // Ignore storage remove errors
  }
}

export function getStoredOrDetectedLocale(): Locale {
  const stored = safeReadStorage(STORAGE_KEY);
  const browserLangs =
    typeof navigator !== 'undefined'
      ? navigator.languages && navigator.languages.length > 0
        ? navigator.languages
        : navigator.language
      : null;
  return detectInitialLocale(stored, browserLangs);
}

export function getActiveLocale(): Locale {
  return activeRuntimeLocale ?? getStoredOrDetectedLocale();
}

export function resolvePath(obj: Record<string, unknown>, path: string): string | undefined {
  if (!path || typeof path !== 'string') return undefined;
  const parts = path.split('.');
  let current: unknown = obj;
  for (const part of parts) {
    if (
      current === null ||
      typeof current !== 'object' ||
      !Object.prototype.hasOwnProperty.call(current, part)
    ) {
      return undefined;
    }
    current = (current as Record<string, unknown>)[part];
  }
  return typeof current === 'string' ? current : undefined;
}

export function interpolate(template: string, params?: InterpolationParams): string {
  if (!params) return template;
  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, token: string) => {
    if (!Object.prototype.hasOwnProperty.call(params, token)) {
      return match;
    }
    const val = params[token];
    return val === undefined || val === null ? '' : String(val);
  });
}

export function translate(locale: Locale, key: TranslationKey, params?: InterpolationParams): string {
  const activeDict = DICTIONARIES[locale] || DICTIONARIES.en;
  const raw =
    resolvePath(activeDict as unknown as Record<string, unknown>, key) ??
    resolvePath(DICTIONARIES.en as unknown as Record<string, unknown>, key) ??
    key;
  return interpolate(raw, params);
}

const EVENT_KEY_MAP: Record<string, TranslationKey> = {
  'session.started': 'notifications.events.sessionStarted',
  'session.stopped': 'notifications.events.sessionStopped',
  'quota.warning': 'notifications.events.quotaWarning',
  'quota.hard_stop': 'notifications.events.quotaHardStop',
  'job.started': 'notifications.events.jobStarted',
  'job.finished': 'notifications.events.jobFinished',
  'job.failed': 'notifications.events.jobFailed',
  'orphan.discovered': 'notifications.events.orphanDiscovered',
};

const GENERIC_ACTION_FALLBACKS = new Set([
  'Failed to start OAuth',
  'Failed to sync account',
  'Failed to delete account',
  'Failed to stop session',
  'Failed to delete path',
  'Failed to save file',
  'Failed to cancel job',
  'Failed to delete channel',
]);

export interface I18nContextValue {
  locale: Locale;
  setLocale: (next: Locale) => void;
  toggleLocale: () => void;
  t: (key: TranslationKey, params?: InterpolationParams) => string;
  te: (errorMessage: string | null | undefined) => string;
  tm: (msg: LocalizedText | null | undefined) => string;
  formatActionError: (prefixKey: TranslationKey, rawError: string | null | undefined) => string;
  formatAccountStatus: (status: string) => string;
  formatSessionStatus: (status: string) => string;
  formatShape: (shape: string) => string;
  formatLifecycle: (policy: string) => string;
  formatJobStatus: (status: string) => string;
  formatJobFilter: (filterValue: string) => string;
  formatJobType: (jobType: string) => string;
  formatNotificationProvider: (provider: string) => string;
  formatStorageType: (storageType: string) => string;
  formatKernelState: (state?: string | null) => string;
  formatEventLabel: (eventId: string) => string;
}

function buildHelpers(
  locale: Locale,
  setLocale: (next: Locale) => void
): I18nContextValue {
  const t = (key: TranslationKey, params?: InterpolationParams) => translate(locale, key, params);
  const te = (errorMessage: string | null | undefined) => translateErrorMessage(errorMessage, locale);
  const tm = (msg: LocalizedText | null | undefined): string => {
    if (!msg) return '';
    if ('key' in msg) {
      return t(msg.key, msg.params);
    }
    return te(msg.raw);
  };

  const formatActionError = (prefixKey: TranslationKey, rawError: string | null | undefined): string => {
    const basePrefix = t(prefixKey, { error: '' }).replace(/[:：\s]+$/, '');
    const raw = (rawError ?? '').trim();
    if (!raw) return basePrefix;

    const translated = te(raw);
    if (translated.toLowerCase().startsWith(basePrefix.toLowerCase())) {
      return translated;
    }
    if (GENERIC_ACTION_FALLBACKS.has(raw)) {
      if (raw === 'Failed to save file' && prefixKey === 'files.uploadFailed') {
        return basePrefix;
      }
      return translated;
    }
    return t(prefixKey, { error: translated });
  };

  const formatAccountStatus = (status: string): string => {
    const key = `status.account.${status}` as TranslationKey;
    const val = resolvePath(DICTIONARIES[locale] as unknown as Record<string, unknown>, key);
    return val ?? status;
  };

  const formatSessionStatus = (status: string): string => {
    const key = `status.session.${status}` as TranslationKey;
    const val = resolvePath(DICTIONARIES[locale] as unknown as Record<string, unknown>, key);
    return val ?? status;
  };

  const formatShape = (shape: string): string => {
    const key = `status.shape.${shape}` as TranslationKey;
    const val = resolvePath(DICTIONARIES[locale] as unknown as Record<string, unknown>, key);
    return val ?? shape;
  };

  const formatLifecycle = (policy: string): string => {
    const key = `status.lifecycle.${policy}` as TranslationKey;
    const val = resolvePath(DICTIONARIES[locale] as unknown as Record<string, unknown>, key);
    return val ?? policy;
  };

  const formatJobStatus = (status: string): string => {
    const key = `status.job.${status}` as TranslationKey;
    const val = resolvePath(DICTIONARIES[locale] as unknown as Record<string, unknown>, key);
    return val ?? status;
  };

  const formatJobFilter = (filterValue: string): string => {
    const normalized = filterValue || 'ALL';
    const key = `status.jobFilter.${normalized}` as TranslationKey;
    const val = resolvePath(DICTIONARIES[locale] as unknown as Record<string, unknown>, key);
    return val ?? filterValue;
  };

  const formatJobType = (jobType: string): string => {
    const lower = jobType.trim().toLowerCase();
    const key = `status.jobType.${lower}` as TranslationKey;
    const val = resolvePath(DICTIONARIES[locale] as unknown as Record<string, unknown>, key);
    return val ?? jobType;
  };

  const formatNotificationProvider = (provider: string): string => {
    const lower = provider.trim().toLowerCase();
    const key = `status.provider.${lower}` as TranslationKey;
    const val = resolvePath(DICTIONARIES[locale] as unknown as Record<string, unknown>, key);
    return val ?? provider;
  };

  const formatStorageType = (storageType: string): string => {
    const lower = storageType.trim().toLowerCase();
    const key = `status.storageType.${lower}` as TranslationKey;
    const val = resolvePath(DICTIONARIES[locale] as unknown as Record<string, unknown>, key);
    return val ?? storageType;
  };

  const formatKernelState = (state?: string | null): string => {
    if (!state) return t('status.kernel.idle');
    const lower = state.trim().toLowerCase();
    const key = `status.kernel.${lower}` as TranslationKey;
    const val = resolvePath(DICTIONARIES[locale] as unknown as Record<string, unknown>, key);
    return val ?? state;
  };

  const formatEventLabel = (eventId: string): string => {
    if (Object.prototype.hasOwnProperty.call(EVENT_KEY_MAP, eventId)) {
      return t(EVENT_KEY_MAP[eventId]);
    }
    return eventId;
  };

  return {
    locale,
    setLocale,
    toggleLocale: () => setLocale(locale === 'zh' ? 'en' : 'zh'),
    t,
    te,
    tm,
    formatActionError,
    formatAccountStatus,
    formatSessionStatus,
    formatShape,
    formatLifecycle,
    formatJobStatus,
    formatJobFilter,
    formatJobType,
    formatNotificationProvider,
    formatStorageType,
    formatKernelState,
    formatEventLabel,
  };
}

const defaultContextValue = buildHelpers('en', () => {});

const I18nContext = createContext<I18nContextValue>(defaultContextValue);

interface I18nProviderProps {
  children: React.ReactNode;
  initialLocale?: Locale;
}

export const I18nProvider: React.FC<I18nProviderProps> = ({ children, initialLocale }) => {
  const [locale, setLocaleState] = useState<Locale>(() => initialLocale ?? getStoredOrDetectedLocale());

  const setLocale = useCallback((next: Locale) => {
    const valid = normalizeLocale(next) ?? 'en';
    activeRuntimeLocale = valid;
    setLocaleState(valid);
    safeWriteStorage(STORAGE_KEY, valid);
  }, []);

  useEffect(() => {
    activeRuntimeLocale = locale;
    if (typeof document !== 'undefined') {
      document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en';
      document.title = translate(locale, 'common.appTitle');
    }
    return () => {
      activeRuntimeLocale = null;
    };
  }, [locale]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const handleStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === STORAGE_KEY) {
        if (event.newValue === null) {
          const fallback = getStoredOrDetectedLocale();
          activeRuntimeLocale = fallback;
          setLocaleState(fallback);
        } else {
          const next = normalizeLocale(event.newValue);
          if (next) {
            activeRuntimeLocale = next;
            setLocaleState(next);
          }
        }
      }
    };
    window.addEventListener('storage', handleStorage);
    return () => window.removeEventListener('storage', handleStorage);
  }, []);

  const value = useMemo(() => buildHelpers(locale, setLocale), [locale, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
};

export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}
