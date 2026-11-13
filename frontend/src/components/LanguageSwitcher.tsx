import React from 'react';
import { Globe } from 'lucide-react';
import { useI18n } from '../i18n';

interface Props {
  className?: string;
}

export const LanguageSwitcher: React.FC<Props> = ({ className = '' }) => {
  const { locale, setLocale, t } = useI18n();

  return (
    <div
      role="group"
      aria-label={t('common.languageSwitch')}
      title={t('common.languageSwitch')}
      data-testid="language-switcher"
      className={`inline-flex items-center rounded-lg bg-slate-900/90 border border-slate-700/80 p-0.5 text-xs select-none shrink-0 ${className}`}
    >
      <Globe className="w-3.5 h-3.5 text-slate-400 ml-1.5 mr-1 shrink-0" aria-hidden="true" />
      <button
        type="button"
        data-testid="lang-zh"
        aria-pressed={locale === 'zh'}
        onClick={() => setLocale('zh')}
        className={`px-2 py-1 rounded-md font-medium transition whitespace-nowrap ${
          locale === 'zh'
            ? 'bg-blue-600 text-white shadow-sm'
            : 'text-slate-400 hover:text-slate-200'
        }`}
      >
        {t('common.langZh')}
      </button>
      <button
        type="button"
        data-testid="lang-en"
        aria-pressed={locale === 'en'}
        onClick={() => setLocale('en')}
        className={`px-2 py-1 rounded-md font-medium transition whitespace-nowrap ${
          locale === 'en'
            ? 'bg-blue-600 text-white shadow-sm'
            : 'text-slate-400 hover:text-slate-200'
        }`}
      >
        {t('common.langEn')}
      </button>
    </div>
  );
};
