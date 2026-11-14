import React from 'react';
import { Globe } from 'lucide-react';
import { useI18n } from '../i18n';
import { triggerHaptic } from '../desktop/macBridge';

interface Props {
  className?: string;
}

export const LanguageSwitcher: React.FC<Props> = ({ className = '' }) => {
  const { locale, setLocale, t } = useI18n();

  const handleSelect = (next: 'zh' | 'en') => {
    if (next !== locale) {
      triggerHaptic('light');
      setLocale(next);
    }
  };

  return (
    <div
      role="group"
      aria-label={t('common.languageSwitch')}
      title={t('common.languageSwitch')}
      data-testid="language-switcher"
      className={`mac-pill-group text-xs select-none shrink-0 gap-0.5 ${className}`}
    >
      <Globe className="w-3.5 h-3.5 text-slate-400 ml-1.5 mr-1 shrink-0" aria-hidden="true" />
      <button
        type="button"
        data-testid="lang-zh"
        aria-pressed={locale === 'zh'}
        onClick={() => handleSelect('zh')}
        className={`px-2.5 py-1 rounded-lg font-medium transition-all whitespace-nowrap ${
          locale === 'zh'
            ? 'mac-pill-active text-white'
            : 'text-slate-400 hover:text-slate-200 border border-transparent'
        }`}
      >
        {t('common.langZh')}
      </button>
      <button
        type="button"
        data-testid="lang-en"
        aria-pressed={locale === 'en'}
        onClick={() => handleSelect('en')}
        className={`px-2.5 py-1 rounded-lg font-medium transition-all whitespace-nowrap ${
          locale === 'en'
            ? 'mac-pill-active text-white'
            : 'text-slate-400 hover:text-slate-200 border border-transparent'
        }`}
      >
        {t('common.langEn')}
      </button>
    </div>
  );
};
