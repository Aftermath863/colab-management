import React, { useEffect, useState } from 'react';
import { ExternalLink, Copy, Check, Key, Loader2, X } from 'lucide-react';
import { useI18n } from '../i18n';
import { copyTextToClipboard, isMacDesktop, openExternalUrl, triggerHaptic } from '../desktop/macBridge';
import { useToast } from '../desktop/ToastContext';
import { MODAL_BACKDROP_CLASSES, ModalPortal } from './ModalPortal';

interface Props {
  authUrl: string;
  onComplete: (code: string) => Promise<void>;
  onClose: () => void;
}

export const OAuthModal: React.FC<Props> = ({ authUrl, onComplete, onClose }) => {
  const { t, te, locale } = useI18n();
  const isZh = locale === 'zh';
  const toast = useToast();

  const [code, setCode] = useState('');
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !loading) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [loading, onClose]);

  const handleCopy = async () => {
    const ok = await copyTextToClipboard(authUrl);
    if (ok) {
      setCopied(true);
      toast.pushToast({
        variant: 'success',
        title: isZh ? '已复制 Google OAuth 链接' : 'Copied OAuth authorization URL',
      });
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim()) return;
    setLoading(true);
    setError(null);
    triggerHaptic('light');
    try {
      await onComplete(code.trim());
      toast.pushToast({
        variant: 'success',
        title: isZh ? 'Google 账号已成功绑定' : 'Google account linked successfully',
      });
      onClose();
    } catch (err: any) {
      triggerHaptic('medium');
      setError(err.message || 'Failed to complete OAuth authorization');
    } finally {
      setLoading(false);
    }
  };

  return (
    <ModalPortal>
      <div
        className={MODAL_BACKDROP_CLASSES}
        onClick={() => {
          if (!loading) onClose();
        }}
        role="dialog"
        aria-modal="true"
      >
        <div
          className="w-full max-w-xl mac-modal-panel mac-glass-heavy rounded-2xl border border-white/[0.1] p-6 shadow-glass-lg overflow-y-auto"
          onClick={(e) => e.stopPropagation()}
        >
        <div className="flex items-start justify-between gap-4 mb-2">
          <h3 className="text-lg sm:text-xl font-bold flex items-center gap-2.5 text-white">
            <span className="w-8 h-8 rounded-xl bg-blue-500/15 border border-blue-400/30 flex items-center justify-center text-blue-400 shrink-0">
              <Key className="w-4 h-4" />
            </span>
            <span>{t('oauth.title')}</span>
          </h3>
          <div className="flex items-center gap-2 shrink-0">
            <kbd className="mac-kbd hidden sm:inline-flex">Esc</kbd>
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              title={t('common.close')}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
        <p className="text-sm text-slate-300/90 mb-4">{t('oauth.intro')}</p>

        {/* Step 1 */}
        <div className="mb-4 rounded-xl bg-white/[0.03] p-4 border border-white/[0.08]">
          <div className="text-xs font-semibold text-blue-400 uppercase tracking-wider mb-1">
            {t('oauth.step1Title')}
          </div>
          <p className="text-xs text-slate-400 mb-3">{t('oauth.step1Desc')}</p>
          <div className="flex flex-wrap sm:flex-nowrap gap-2">
            <a
              href={authUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => {
                if (isMacDesktop()) {
                  e.preventDefault();
                  triggerHaptic('light');
                  openExternalUrl(authUrl);
                }
              }}
              className="flex-1 mac-btn-primary px-3.5 py-2.5 rounded-xl text-xs font-semibold gap-2 truncate"
            >
              <ExternalLink className="w-4 h-4 shrink-0" />
              <span className="truncate">{t('oauth.openAuthPage')}</span>
            </a>
            <button
              type="button"
              onClick={handleCopy}
              className="mac-btn-secondary px-3.5 py-2.5 rounded-xl text-xs font-medium gap-1.5"
            >
              {copied ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
              <span>{copied ? `${t('common.copied')} ✓` : t('oauth.copyUrl')}</span>
            </button>
          </div>
        </div>

        {/* Step 2 */}
        <form onSubmit={handleSubmit}>
          <div className="mb-4 rounded-xl bg-white/[0.03] p-4 border border-white/[0.08]">
            <div className="text-xs font-semibold text-blue-400 uppercase tracking-wider mb-1">
              {t('oauth.step2Title')}
            </div>
            <p className="text-xs text-slate-400 mb-2.5">
              {t('oauth.step2DescPrefix')}
              <code className="px-1.5 py-0.5 rounded bg-white/[0.06] text-blue-300 font-mono">4/0A...</code>
              {t('oauth.step2DescSuffix')}
            </p>
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder={t('oauth.codePlaceholder')}
              className="w-full px-3 py-2.5 mac-input rounded-xl text-sm font-mono"
              required
            />
          </div>

          {error && (
            <div className="mb-4 text-xs text-rose-300 bg-rose-950/50 p-3 rounded-xl border border-rose-500/35">
              {te(error)}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-2 border-t border-white/[0.07]">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium disabled:opacity-50"
            >
              {t('common.cancel')}
            </button>
            <button
              type="submit"
              disabled={loading || !code.trim()}
              className="px-5 py-2 rounded-xl text-xs font-semibold text-white bg-gradient-to-b from-emerald-500 to-emerald-600 hover:brightness-110 active:scale-[0.97] border border-emerald-300/30 shadow-[0_6px_20px_-6px_rgba(16,185,129,0.55),inset_0_1px_0_rgba(255,255,255,0.22)] transition-all disabled:opacity-50 flex items-center gap-2"
            >
              {loading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {loading ? t('oauth.verifying') : t('oauth.completeLinking')}
            </button>
          </div>
        </form>
      </div>
    </div>
    </ModalPortal>
  );
};
