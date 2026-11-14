import React, { useState, useEffect } from 'react';
import { Copy, Check, Terminal, ExternalLink, X, ShieldCheck, Laptop, Loader2 } from 'lucide-react';
import { api } from '../services/api';
import { SSHConfig } from '../types';
import { useI18n } from '../i18n';
import {
  copyTextToClipboard,
  isMacDesktop,
  openIDE,
  openTerminalSSH,
  triggerHaptic,
} from '../desktop/macBridge';
import { useToast } from '../desktop/ToastContext';

interface SSHHelperModalProps {
  sessionId: string;
  sessionName: string;
  isOpen: boolean;
  onClose: () => void;
}

export const SSHHelperModal: React.FC<SSHHelperModalProps> = ({
  sessionId,
  sessionName,
  isOpen,
  onClose,
}) => {
  const { t, te, locale } = useI18n();
  const isZh = locale === 'zh';
  const toast = useToast();
  const isMac = isMacDesktop();

  const [config, setConfig] = useState<SSHConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedCmd, setCopiedCmd] = useState(false);
  const [copiedCfg, setCopiedCfg] = useState(false);

  useEffect(() => {
    if (isOpen) {
      loadSSHConfig();
    }
  }, [isOpen, sessionId]);

  useEffect(() => {
    if (!isOpen || typeof window === 'undefined') return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [isOpen, onClose]);

  const loadSSHConfig = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getSSHConfig(sessionId);
      setConfig(data);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch SSH config');
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = async (text: string, isCmd: boolean) => {
    const ok = await copyTextToClipboard(text);
    if (!ok) return;
    if (isCmd) {
      setCopiedCmd(true);
      toast.pushToast({
        variant: 'success',
        title: isZh ? '已复制 SSH 直连命令' : 'Copied SSH command',
      });
      setTimeout(() => setCopiedCmd(false), 2000);
    } else {
      setCopiedCfg(true);
      toast.pushToast({
        variant: 'success',
        title: isZh ? '已复制 SSH 配置片段' : 'Copied ~/.ssh/config snippet',
      });
      setTimeout(() => setCopiedCfg(false), 2000);
    }
  };

  if (!isOpen) return null;

  const hostAlias = `colab-${sessionName || sessionId}`;

  return (
    <div
      className="fixed inset-0 z-50 mac-modal-backdrop flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="mac-modal-panel mac-glass-heavy border border-white/[0.1] rounded-2xl max-w-2xl w-full shadow-glass-lg overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.08] bg-white/[0.02]">
          <div className="flex items-center gap-2.5">
            <span className="w-8 h-8 rounded-xl bg-indigo-500/15 border border-indigo-400/30 flex items-center justify-center text-indigo-400">
              <Terminal className="w-4 h-4" />
            </span>
            <h2 className="text-base sm:text-lg font-semibold text-white">{t('sshModal.title')}</h2>
          </div>
          <div className="flex items-center gap-2">
            <kbd className="mac-kbd hidden sm:inline-flex">Esc</kbd>
            <button
              type="button"
              onClick={onClose}
              title={t('common.close')}
              className="text-slate-400 hover:text-white transition-colors p-1.5 rounded-lg hover:bg-white/[0.08]"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6 overflow-y-auto max-h-[80vh]">
          {loading ? (
            <div className="py-12 flex flex-col items-center justify-center gap-3 text-slate-400 text-sm">
              <Loader2 className="w-6 h-6 animate-spin text-indigo-400" />
              <span>{t('sshModal.loading')}</span>
            </div>
          ) : error ? (
            <div className="p-4 bg-rose-950/40 border border-rose-500/30 rounded-xl text-rose-300 text-sm">
              {te(error)}
            </div>
          ) : config ? (
            <>
              {/* Multiplexer Banner */}
              <div className="flex items-start gap-3 p-3.5 bg-indigo-500/10 border border-indigo-400/25 rounded-xl text-xs text-indigo-200/90">
                <ShieldCheck className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" />
                <div>
                  <strong className="font-semibold text-indigo-200">{t('sshModal.multiplexerActiveTitle')}</strong>{' '}
                  {t('sshModal.multiplexerActiveDesc', { port: config.port ?? t('common.na') })}
                </div>
              </div>

              {/* Quick Terminal Command */}
              <div>
                <label className="block text-xs font-medium text-slate-400 uppercase tracking-wider mb-2">
                  {t('sshModal.directCommandLabel')}
                </label>
                <div className="relative group">
                  <pre className="mac-input rounded-xl p-3.5 pr-28 text-sm font-mono text-emerald-400 overflow-x-auto select-all tabular-nums">
                    {config.connection_command}
                  </pre>
                  <button
                    type="button"
                    onClick={() => copyToClipboard(config.connection_command, true)}
                    className="absolute right-2 top-2 mac-btn-secondary px-2.5 py-1.5 rounded-lg gap-1.5 text-xs"
                  >
                    {copiedCmd ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copiedCmd ? `${t('common.copied')} ✓` : t('common.copy')}</span>
                  </button>
                </div>
              </div>

              {/* VS Code / Cursor ~/.ssh/config Snippet */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-xs font-medium text-slate-400 uppercase tracking-wider">
                    {t('sshModal.vscodeSnippetLabel')}
                  </label>
                  <button
                    type="button"
                    onClick={() => copyToClipboard(config.vscode_config, false)}
                    className="text-xs text-indigo-300 hover:text-indigo-200 flex items-center gap-1.5 transition-colors"
                  >
                    {copiedCfg ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copiedCfg ? `${t('sshModal.copiedSnippet')} ✓` : t('sshModal.copySnippet')}</span>
                  </button>
                </div>
                <pre className="mac-input rounded-xl p-3.5 text-xs font-mono text-slate-300 overflow-x-auto select-all">
                  {config.vscode_config}
                </pre>
              </div>

              {/* IDE Deep Links & Quick Connect */}
              <div>
                <label className="block text-xs font-medium text-slate-400 uppercase tracking-wider mb-2.5">
                  {t('sshModal.ideLaunchLabel')}
                </label>
                <div className="flex flex-wrap gap-2.5">
                  {isMac && config.port && (
                    <button
                      type="button"
                      onClick={() => {
                        triggerHaptic('light');
                        openTerminalSSH(config.port!, sessionName, config.connection_command);
                        toast.pushToast({
                          variant: 'info',
                          title: isZh ? '正在打开 Terminal.app' : 'Opening Terminal.app',
                          description: `${sessionName} (localhost:${config.port})`,
                        });
                      }}
                      className="mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium gap-2"
                    >
                      <Terminal className="w-3.5 h-3.5 text-amber-400" />
                      <span>Terminal.app</span>
                      <ExternalLink className="w-3 h-3 opacity-70" />
                    </button>
                  )}

                  <a
                    href={`vscode://vscode-remote/ssh-remote+${hostAlias}/content`}
                    onClick={(e) => {
                      if (isMac && config.port) {
                        e.preventDefault();
                        triggerHaptic('light');
                        openIDE('vscode', config.port, '/content');
                        toast.pushToast({
                          variant: 'info',
                          title: isZh ? '正在启动 VS Code Remote-SSH' : 'Launching VS Code Remote-SSH',
                          description: `localhost:${config.port}/content`,
                        });
                      }
                    }}
                    className="mac-btn-primary px-4 py-2 rounded-xl text-xs font-semibold gap-2"
                  >
                    <Laptop className="w-3.5 h-3.5" />
                    <span>{t('sshModal.openInVscode')}</span>
                    <ExternalLink className="w-3 h-3 opacity-75" />
                  </a>

                  <a
                    href={`cursor://vscode-remote/ssh-remote+${hostAlias}/content`}
                    onClick={(e) => {
                      if (isMac && config.port) {
                        e.preventDefault();
                        triggerHaptic('light');
                        openIDE('cursor', config.port, '/content');
                        toast.pushToast({
                          variant: 'info',
                          title: isZh ? '正在启动 Cursor Remote-SSH' : 'Launching Cursor Remote-SSH',
                          description: `localhost:${config.port}/content`,
                        });
                      }
                    }}
                    className="mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium gap-2"
                  >
                    <Laptop className="w-3.5 h-3.5 text-purple-400" />
                    <span>{t('sshModal.openInCursor')}</span>
                    <ExternalLink className="w-3 h-3 opacity-75" />
                  </a>
                </div>
                <p className="text-[11px] text-slate-400/80 mt-2.5">
                  {t('sshModal.sshConfigTipPrefix')}
                  <code className="text-slate-300 font-mono">~/.ssh/config</code>
                  {t('sshModal.sshConfigTipSuffix')}
                </p>
              </div>
            </>
          ) : null}
        </div>

        {/* Footer */}
        <div className="px-6 py-3.5 bg-black/25 border-t border-white/[0.07] flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium"
          >
            {t('common.close')}
          </button>
        </div>
      </div>
    </div>
  );
};
