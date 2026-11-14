import React, { useState } from 'react';
import { Terminal, Lock, ArrowRight, Eye, EyeOff, Cpu, ShieldCheck } from 'lucide-react';
import { api } from '../services/api';
import { useI18n } from '../i18n';
import { LanguageSwitcher } from '../components/LanguageSwitcher';
import {
  isMacDesktop,
  getMacArch,
  getMacVersion,
  triggerHaptic,
  notifyToast,
  handleMacDragMouseDown,
  handleMacDragDoubleClick,
  updateCardSpotlight,
} from '../utils/macDesktop';

interface Props {
  onLoginSuccess: () => void;
}

export const LoginPage: React.FC<Props> = ({ onLoginSuccess }) => {
  const { t, te, locale } = useI18n();
  const isZh = locale === 'zh';
  const macDesktop = isMacDesktop();
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password) return;
    setLoading(true);
    setError(null);
    triggerHaptic('light');
    try {
      await api.login(password);
      triggerHaptic('success');
      notifyToast(
        isZh ? '控制台已解锁' : 'Console Unlocked',
        isZh ? '欢迎回到 Colab 算力指挥中心' : 'Welcome back to Colab Fleet Control',
        'success'
      );
      onLoginSuccess();
    } catch (err: any) {
      triggerHaptic('medium');
      setError(err.message || 'Authentication failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mac-ambient-bg min-h-screen flex flex-col justify-center items-center p-4 relative overflow-hidden">
      {/* Ambient Radial Aura Orbs */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden" aria-hidden="true">
        <div className="absolute -top-32 -left-24 w-[32rem] h-[32rem] rounded-full bg-indigo-500/15 blur-[120px]" />
        <div className="absolute -bottom-36 -right-24 w-[30rem] h-[30rem] rounded-full bg-cyan-500/10 blur-[120px]" />
        <div className="absolute top-1/3 left-1/2 -translate-x-1/2 w-[24rem] h-[24rem] rounded-full bg-amber-500/[0.07] blur-[110px]" />
      </div>

      {/* Draggable Top Chrome */}
      <div
        data-mac-drag-region="true"
        onMouseDown={handleMacDragMouseDown}
        onDoubleClick={handleMacDragDoubleClick}
        className={`fixed top-0 inset-x-0 h-14 z-20 flex items-center justify-between pr-4 sm:pr-6 select-none ${
          macDesktop ? 'pl-24' : 'pl-4 sm:pl-6'
        }`}
      >
        <div className="flex items-center gap-2">
          {macDesktop && (
            <span className="mac-glass inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-medium text-slate-300 tracking-wide">
              <Cpu className="w-3.5 h-3.5 text-sky-400" />
              <span>Apple Silicon · {getMacArch()}</span>
              <span className="text-slate-500 tabular-nums">v{getMacVersion()}</span>
            </span>
          )}
        </div>
        <LanguageSwitcher />
      </div>

      {/* Obsidian Vault Centerpiece Card */}
      <div
        onMouseMove={updateCardSpotlight}
        className="mac-card mac-card-hover mac-page-enter w-full max-w-md p-8 sm:p-9 relative z-10 shadow-2xl"
      >
        <div className="flex flex-col items-center mb-8 text-center">
          <div className="relative mb-5">
            <div
              className="absolute -inset-2 rounded-3xl bg-gradient-to-tr from-blue-500/35 via-indigo-500/25 to-amber-400/25 blur-xl"
              aria-hidden="true"
            />
            <div className="relative w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-400/50 via-indigo-500/40 to-cyan-400/40 p-[1px] shadow-[0_12px_32px_-8px_rgba(59,130,246,0.55)]">
              <div className="w-full h-full rounded-[15px] bg-slate-950/85 backdrop-blur-xl flex items-center justify-center text-blue-300">
                <Terminal className="w-8 h-8" />
              </div>
            </div>
          </div>

          <h1 className="text-2xl font-bold text-white tracking-tight">{t('login.title')}</h1>
          <p className="text-sm text-slate-400 mt-1.5">{t('login.subtitle')}</p>

          {macDesktop && (
            <div className="mt-3 inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-400/25 text-[11px] font-medium text-emerald-300">
              <ShieldCheck className="w-3.5 h-3.5" />
              <span>
                {isZh
                  ? `macOS ${getMacArch()} 桌面安全守护模式`
                  : `macOS ${getMacArch()} Desktop Vault`}
              </span>
            </div>
          )}
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
              {t('login.passwordLabel')}
            </label>
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={t('login.passwordPlaceholder')}
                className="mac-input w-full pl-11 pr-11 py-3 rounded-xl text-white placeholder-slate-500 text-sm"
                required
              />
              <Lock className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
              <button
                type="button"
                onClick={() => setShowPassword((prev) => !prev)}
                aria-label={showPassword ? t('templates.hide') : t('templates.show')}
                className="absolute right-3 top-1/2 -translate-y-1/2 p-1 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-white/[0.06] transition"
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          {error && (
            <div className="text-xs text-rose-300 bg-rose-500/10 px-3.5 py-2.5 rounded-xl border border-rose-500/30">
              {te(error)}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="mac-btn-primary w-full py-3 rounded-xl font-medium flex items-center justify-center gap-2 disabled:opacity-50"
          >
            <span>{loading ? t('login.authenticating') : t('login.signIn')}</span>
            <ArrowRight className="w-4 h-4" />
          </button>
        </form>

        <div className="mt-8 pt-5 border-t border-white/[0.08] text-center">
          <p className="text-xs text-slate-400">
            {t('login.defaultCredsPrefix')}
            <code className="px-1.5 py-0.5 rounded bg-white/[0.06] border border-white/[0.08] font-mono text-slate-200">
              ADMIN_PASSWORD
            </code>
            {t('login.defaultCredsSuffix')}
          </p>
        </div>
      </div>
    </div>
  );
};
