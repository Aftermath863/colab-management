import React, { useState } from 'react';
import { ExternalLink, Copy, Check, Key } from 'lucide-react';
import { useI18n } from '../i18n';

interface Props {
  authUrl: string;
  onComplete: (code: string) => Promise<void>;
  onClose: () => void;
}

export const OAuthModal: React.FC<Props> = ({ authUrl, onComplete, onClose }) => {
  const { t, te } = useI18n();
  const [code, setCode] = useState('');
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCopy = async () => {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(authUrl);
    } else {
      const ta = document.createElement('textarea');
      ta.value = authUrl;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim()) return;
    setLoading(true);
    setError(null);
    try {
      await onComplete(code.trim());
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to complete OAuth authorization');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-xl rounded-xl bg-slate-800 border border-slate-700 p-6 shadow-2xl">
        <h3 className="text-xl font-bold flex items-center gap-2 text-white mb-2">
          <Key className="w-5 h-5 text-blue-400" />
          {t('oauth.title')}
        </h3>
        <p className="text-sm text-slate-300 mb-4">{t('oauth.intro')}</p>

        {/* Step 1 */}
        <div className="mb-4 rounded-lg bg-slate-900/60 p-4 border border-slate-700/60">
          <div className="text-xs font-semibold text-blue-400 uppercase tracking-wider mb-1">
            {t('oauth.step1Title')}
          </div>
          <p className="text-xs text-slate-400 mb-2">{t('oauth.step1Desc')}</p>
          <div className="flex gap-2">
            <a
              href={authUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded text-xs font-medium text-white transition truncate"
            >
              <ExternalLink className="w-4 h-4 shrink-0" />
              {t('oauth.openAuthPage')}
            </a>
            <button
              type="button"
              onClick={handleCopy}
              className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded text-xs text-slate-200 flex items-center gap-1"
            >
              {copied ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
              {copied ? t('common.copied') : t('oauth.copyUrl')}
            </button>
          </div>
        </div>

        {/* Step 2 */}
        <form onSubmit={handleSubmit}>
          <div className="mb-4 rounded-lg bg-slate-900/60 p-4 border border-slate-700/60">
            <div className="text-xs font-semibold text-blue-400 uppercase tracking-wider mb-1">
              {t('oauth.step2Title')}
            </div>
            <p className="text-xs text-slate-400 mb-2">
              {t('oauth.step2DescPrefix')}
              <code>4/0A...</code>
              {t('oauth.step2DescSuffix')}
            </p>
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder={t('oauth.codePlaceholder')}
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-white font-mono focus:border-blue-500 focus:outline-none"
              required
            />
          </div>

          {error && (
            <div className="mb-4 text-xs text-red-400 bg-red-950/40 p-2.5 rounded border border-red-800">
              {te(error)}
            </div>
          )}

          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded text-sm text-slate-300"
            >
              {t('common.cancel')}
            </button>
            <button
              type="submit"
              disabled={loading || !code.trim()}
              className="px-5 py-2 bg-green-600 hover:bg-green-500 disabled:opacity-50 rounded text-sm font-medium text-white transition flex items-center gap-2"
            >
              {loading ? t('oauth.verifying') : t('oauth.completeLinking')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
