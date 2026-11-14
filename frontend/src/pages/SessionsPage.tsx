import React, { useState } from 'react';
import { Play, Square, ExternalLink, Terminal, Copy, Check, Cpu } from 'lucide-react';
import { Session } from '../types';
import { StopSessionModal } from '../components/StopSessionModal';
import { SnapshotModal } from '../components/SnapshotModal';
import { useI18n } from '../i18n';
import {
  isMacDesktop,
  triggerHaptic,
  notifyToast,
  openExternalUrl,
  openTerminalSSH,
  copyToClipboard,
  getAcceleratorBadgeClass,
  getSessionStatusStyles,
} from '../utils/macDesktop';

interface Props {
  sessions: Session[];
  onOpenSession: (session: Session) => void;
  onNewSession: () => void;
  onStopSession: (sessionId: string) => Promise<void>;
  onRefresh: () => Promise<void>;
}

export const SessionsPage: React.FC<Props> = ({
  sessions,
  onOpenSession,
  onNewSession,
  onStopSession,
  onRefresh,
}) => {
  const { t, locale, formatActionError, formatSessionStatus, formatShape } = useI18n();
  const isZh = locale === 'zh';
  const macDesktop = isMacDesktop();

  const [filter, setFilter] = useState<'all' | 'active' | 'stopped'>('all');
  const [stoppingId, setStoppingId] = useState<string | null>(null);
  const [stopTarget, setStopTarget] = useState<Session | null>(null);
  const [snapshotSaveTarget, setSnapshotSaveTarget] = useState<Session | null>(null);
  const [copiedSshId, setCopiedSshId] = useState<string | null>(null);

  const isSessionActive = (s: Session) => s.status !== 'STOPPED' && s.status !== 'FAILED';

  const filteredSessions = sessions.filter((s) => {
    if (filter === 'active') return isSessionActive(s);
    if (filter === 'stopped') return !isSessionActive(s);
    return true;
  });

  const handleStopDirect = async (sessionId: string, sessionName?: string) => {
    setStoppingId(sessionId);
    triggerHaptic('medium');
    try {
      await onStopSession(sessionId);
      await onRefresh();
      triggerHaptic('success');
      notifyToast(
        isZh ? '实例已停止' : 'Runtime Stopped',
        sessionName
          ? isZh
            ? `计算实例 ${sessionName} 已释放`
            : `Instance ${sessionName} terminated`
          : undefined,
        'success'
      );
    } catch (err: any) {
      triggerHaptic('medium');
      notifyToast(formatActionError('sessions.stopFailed', err?.message), undefined, 'error');
    } finally {
      setStoppingId(null);
    }
  };

  const handleCopySsh = async (sess: Session) => {
    if (!sess.ssh_local_port) return;
    const cmd = `ssh -p ${sess.ssh_local_port} root@localhost`;
    const ok = await copyToClipboard(cmd);
    if (ok) {
      triggerHaptic('success');
      setCopiedSshId(sess.id);
      notifyToast(isZh ? '已复制 SSH 命令' : 'SSH Command Copied', cmd, 'success');
      setTimeout(() => {
        setCopiedSshId((prev) => (prev === sess.id ? null : prev));
      }, 2000);
    }
  };

  const tabLabels = {
    all: t('sessions.filterAll'),
    active: t('sessions.filterActive'),
    stopped: t('sessions.filterStopped'),
  };

  return (
    <div className="p-4 sm:p-8 max-w-7xl mx-auto space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">{t('sessions.title')}</h2>
          <p className="text-sm text-slate-400 mt-1">{t('sessions.subtitle')}</p>
        </div>

        <button
          type="button"
          onClick={() => {
            triggerHaptic('medium');
            onNewSession();
          }}
          className="mac-btn-primary px-4 py-2 rounded-xl text-sm font-medium flex items-center gap-2"
        >
          <Play className="w-4 h-4 fill-current" />
          <span>{t('sessions.launchRuntime')}</span>
          {macDesktop && <kbd className="mac-kbd text-[10px]">⌘N</kbd>}
        </button>
      </div>

      {/* Segmented Pill Filter */}
      <div className="flex items-center justify-between gap-4">
        <div className="mac-pill-group inline-flex items-center gap-1 p-1 rounded-xl">
          {(['all', 'active', 'stopped'] as const).map((tab) => {
            const count = sessions.filter((s) => {
              if (tab === 'active') return isSessionActive(s);
              if (tab === 'stopped') return !isSessionActive(s);
              return true;
            }).length;
            const active = filter === tab;
            return (
              <button
                type="button"
                key={tab}
                onClick={() => {
                  triggerHaptic('alignment');
                  setFilter(tab);
                }}
                className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition flex items-center gap-1.5 ${
                  active
                    ? 'mac-pill-active text-white'
                    : 'text-slate-400 hover:text-slate-100 hover:bg-white/[0.04]'
                }`}
              >
                <span>{tabLabels[tab]}</span>
                <span
                  className={`px-1.5 py-0.2 rounded-full text-[11px] tabular-nums ${
                    active ? 'bg-white/15 text-white' : 'bg-white/[0.06] text-slate-400'
                  }`}
                >
                  {count}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Sessions Glass Table */}
      <div className="mac-card overflow-hidden">
        {filteredSessions.length === 0 ? (
          <div className="p-12 text-center space-y-3">
            <div className="w-12 h-12 mx-auto rounded-2xl bg-white/[0.04] border border-white/[0.08] flex items-center justify-center text-slate-400">
              <Cpu className="w-5 h-5" />
            </div>
            <p className="text-sm text-slate-400">{t('sessions.noMatchingSessions')}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-300">
              <thead className="bg-white/[0.02] text-[11px] uppercase tracking-wider font-semibold text-slate-400 border-b border-white/[0.08]">
                <tr>
                  <th className="px-6 py-3.5">{t('sessions.colSession')}</th>
                  <th className="px-6 py-3.5">{t('sessions.colAccelerator')}</th>
                  <th className="px-6 py-3.5">{t('sessions.colAccount')}</th>
                  <th className="px-6 py-3.5">{t('sessions.colStatus')}</th>
                  <th className="px-6 py-3.5">{t('sessions.colSshPort')}</th>
                  <th className="px-6 py-3.5">{t('sessions.colCreated')}</th>
                  <th className="px-6 py-3.5 text-right">{t('sessions.colActions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {filteredSessions.map((sess) => {
                  const isActive = isSessionActive(sess);
                  const statusStyle = getSessionStatusStyles(sess.status);
                  const isCopied = copiedSshId === sess.id;

                  return (
                    <tr key={sess.id} className="hover:bg-white/[0.03] transition-colors">
                      <td className="px-6 py-4 font-medium text-white">
                        <div className="flex items-center gap-2.5">
                          <span
                            className={`w-2 h-2 rounded-full shrink-0 ${statusStyle.dotClass} ${
                              isActive && statusStyle.isPulsing ? 'animate-pulse' : ''
                            }`}
                          />
                          <span className="truncate max-w-[180px]">{sess.name}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`inline-flex items-center px-2.5 py-0.5 text-xs font-mono rounded-lg border ${getAcceleratorBadgeClass(
                            sess.accelerator
                          )}`}
                        >
                          {sess.accelerator} ({formatShape(sess.shape)})
                        </span>
                      </td>
                      <td className="px-6 py-4 text-xs font-mono text-slate-400">
                        {sess.account_email || sess.account_id}
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`inline-flex items-center px-2.5 py-0.5 text-xs font-semibold rounded-full border ${statusStyle.badgeClass}`}
                        >
                          {formatSessionStatus(sess.status)}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-xs font-mono">
                        {sess.ssh_local_port ? (
                          <button
                            type="button"
                            onClick={() => void handleCopySsh(sess)}
                            title={
                              isZh
                                ? `点击复制: ssh -p ${sess.ssh_local_port} root@localhost`
                                : `Click to copy: ssh -p ${sess.ssh_local_port} root@localhost`
                            }
                            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border transition tabular-nums ${
                              isCopied
                                ? 'bg-emerald-500/15 border-emerald-400/40 text-emerald-300'
                                : 'bg-white/[0.04] hover:bg-white/[0.08] border-white/[0.09] text-slate-200'
                            }`}
                          >
                            <span>
                              {isCopied
                                ? isZh
                                  ? '已复制 ✓'
                                  : 'Copied ✓'
                                : `localhost:${sess.ssh_local_port}`}
                            </span>
                            {isCopied ? (
                              <Check className="w-3 h-3 text-emerald-400" />
                            ) : (
                              <Copy className="w-3 h-3 text-slate-400" />
                            )}
                          </button>
                        ) : (
                          <span className="text-slate-500">—</span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-xs text-slate-400 tabular-nums">
                        {sess.created_at?.split(' ')[0] || t('sessions.recently')}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="inline-flex items-center justify-end gap-2">
                          {macDesktop && isActive && sess.ssh_local_port && (
                            <button
                              type="button"
                              onClick={() => openTerminalSSH(sess.ssh_local_port!, sess.name)}
                              title={isZh ? '在 Terminal.app 中打开 SSH' : 'Open in Terminal.app'}
                              className="mac-btn-secondary px-2.5 py-1.5 rounded-lg text-xs font-medium inline-flex items-center gap-1.5"
                            >
                              <Terminal className="w-3.5 h-3.5 text-emerald-400" />
                              <span>Terminal.app</span>
                            </button>
                          )}

                          {sess.colab_web_url && (
                            <a
                              href={sess.colab_web_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => {
                                if (macDesktop && sess.colab_web_url) {
                                  e.preventDefault();
                                  triggerHaptic('light');
                                  openExternalUrl(sess.colab_web_url);
                                }
                              }}
                              className="inline-flex items-center gap-1 px-2.5 py-1.5 bg-amber-500/15 hover:bg-amber-500/25 border border-amber-400/30 rounded-lg text-xs font-medium text-amber-200 transition"
                            >
                              <ExternalLink className="w-3 h-3" /> {t('sessions.openColab')}
                            </a>
                          )}

                          {isActive && (
                            <>
                              <button
                                type="button"
                                onClick={() => {
                                  triggerHaptic('light');
                                  onOpenSession(sess);
                                }}
                                className="mac-btn-primary px-3 py-1.5 rounded-lg text-xs font-medium inline-flex items-center gap-1.5"
                              >
                                <Terminal className="w-3 h-3" /> {t('sessions.workbench')}
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  triggerHaptic('light');
                                  setStopTarget(sess);
                                }}
                                disabled={stoppingId === sess.id}
                                title={t('sessions.stopTooltip')}
                                aria-label={t('sessions.stopTooltip')}
                                className="px-2.5 py-1.5 bg-rose-500/15 hover:bg-rose-500/25 border border-rose-400/30 rounded-lg text-xs text-rose-300 transition disabled:opacity-50"
                              >
                                <Square className="w-3 h-3 fill-current inline" />
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {stopTarget && (
        <StopSessionModal
          session={stopTarget}
          onSaveAndStop={() => {
            const target = stopTarget;
            setStopTarget(null);
            setSnapshotSaveTarget(target);
          }}
          onStopDirectly={async () => {
            const target = stopTarget;
            setStopTarget(null);
            await handleStopDirect(target.id, target.name);
          }}
          onClose={() => setStopTarget(null)}
        />
      )}

      {snapshotSaveTarget && (
        <SnapshotModal
          session={snapshotSaveTarget}
          initialTab="save"
          initialStopAfterSave={true}
          onCompleted={async (task) => {
            if (task.status === 'COMPLETED') {
              await onRefresh();
            }
          }}
          onClose={() => setSnapshotSaveTarget(null)}
        />
      )}
    </div>
  );
};
