import React, { useState } from 'react';
import {
  Cpu,
  Users,
  Zap,
  ExternalLink,
  Play,
  AlertTriangle,
  RefreshCw,
  Copy,
  Check,
  Terminal,
  Flame,
  Clock,
  Sparkles,
} from 'lucide-react';
import { Account, Session } from '../types';
import { api } from '../services/api';
import { useI18n } from '../i18n';
import { getLatestSyncTimestamp } from '../utils/time';
import {
  isMacDesktop,
  triggerHaptic,
  notifyToast,
  openExternalUrl,
  openTerminalSSH,
  copyToClipboard,
  updateCardSpotlight,
  getAcceleratorBadgeClass,
  getSessionStatusStyles,
} from '../utils/macDesktop';

interface Props {
  accounts: Account[];
  sessions: Session[];
  onOpenSession: (session: Session) => void;
  onNewSession: () => void;
  onLinkAccount: () => void;
  onSyncAccounts?: () => Promise<void>;
  isSyncing?: boolean;
  autoRefreshSeconds?: number;
}

export const OverviewPage: React.FC<Props> = ({
  accounts,
  sessions,
  onOpenSession,
  onNewSession,
  onLinkAccount,
  onSyncAccounts,
  isSyncing = false,
  autoRefreshSeconds = 60,
}) => {
  const { t, locale, formatActionError, formatSessionStatus, formatShape } = useI18n();
  const isZh = locale === 'zh';
  const macDesktop = isMacDesktop();

  const [localSyncing, setLocalSyncing] = useState(false);
  const [copiedSshId, setCopiedSshId] = useState<string | null>(null);
  const syncing = isSyncing || localSyncing;

  const totalCcu = accounts.reduce((acc, a) => acc + a.ccu_balance, 0);
  const totalBurnRate = accounts.reduce((acc, a) => acc + a.burn_rate_hourly, 0);
  const totalWarningCcu = accounts.reduce((acc, a) => acc + (a.warning_ccu || 0), 0);
  const totalHardStopCcu = accounts.reduce((acc, a) => acc + (a.hard_stop_ccu || 0), 0);
  const readyAccountsCount = accounts.filter((a) => a.status === 'ACTIVE').length;

  const activeSessions = sessions.filter(
    (s) => s.status !== 'STOPPED' && s.status !== 'FAILED' && s.status !== 'LOST'
  );
  const orphanSessions = sessions.filter((s) => s.status === 'ORPHAN');
  const latestSync = getLatestSyncTimestamp(accounts);

  // Pool quota progress calculation
  const poolTargetCcu = Math.max(100, accounts.length * 100, totalCcu);
  const ccuProgressPct =
    accounts.length === 0 || totalCcu <= 0
      ? 0
      : Math.min(100, Math.max(4, Math.round((totalCcu / poolTargetCcu) * 100)));
  const isPoolCritical = accounts.length > 0 && totalCcu <= totalHardStopCcu;
  const isPoolWarning = !isPoolCritical && accounts.length > 0 && totalCcu <= totalWarningCcu;
  const ccuBarGradient = isPoolCritical
    ? 'from-rose-500 to-red-400'
    : isPoolWarning
    ? 'from-amber-500 to-yellow-400'
    : 'from-emerald-500 to-cyan-400';

  // Burn-rate runway calculation
  const runwayHours = totalBurnRate > 0 ? totalCcu / totalBurnRate : null;
  const runwayLabel =
    runwayHours !== null
      ? runwayHours >= 24
        ? `${(runwayHours / 24).toFixed(1)}d`
        : `${runwayHours.toFixed(1)}h`
      : null;

  const handleManualSync = async () => {
    if (syncing) return;
    triggerHaptic('light');
    if (onSyncAccounts) {
      try {
        await onSyncAccounts();
      } catch (err: any) {
        triggerHaptic('medium');
        notifyToast(
          formatActionError('overview.syncQuotaFailed', err?.message),
          undefined,
          'error'
        );
      }
      return;
    }
    setLocalSyncing(true);
    try {
      await api.syncAllAccounts();
      triggerHaptic('success');
      notifyToast(
        isZh ? '算力配额已同步' : 'Fleet Quotas Synced',
        isZh ? '所有 Google 账号 CCU 余额已刷新' : 'All linked account CCU balances refreshed',
        'success'
      );
    } catch (err: any) {
      triggerHaptic('medium');
      notifyToast(formatActionError('overview.syncQuotaFailed', err?.message), undefined, 'error');
    } finally {
      setLocalSyncing(false);
    }
  };

  const handleCopySsh = async (sess: Session) => {
    if (!sess.ssh_local_port) return;
    const cmd = `ssh -p ${sess.ssh_local_port} root@localhost`;
    const ok = await copyToClipboard(cmd);
    if (ok) {
      triggerHaptic('success');
      setCopiedSshId(sess.id);
      notifyToast(
        isZh ? '已复制 SSH 命令' : 'SSH Command Copied',
        cmd,
        'success'
      );
      setTimeout(() => {
        setCopiedSshId((prev) => (prev === sess.id ? null : prev));
      }, 2000);
    }
  };

  const renderMetricSyncButton = () => (
    <button
      type="button"
      onClick={() => void handleManualSync()}
      disabled={syncing}
      title={t('overview.refreshQuota')}
      aria-label={t('overview.refreshQuota')}
      className="p-1.5 rounded-lg hover:bg-white/[0.08] text-slate-400 hover:text-slate-100 transition disabled:opacity-50"
    >
      <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
    </button>
  );

  return (
    <div className="mac-page-enter p-4 sm:p-8 max-w-7xl mx-auto space-y-6">
      {/* Top Banner & Quick Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">{t('overview.title')}</h2>
          <p className="text-sm text-slate-400 mt-1">{t('overview.subtitle')}</p>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400 mt-2">
            <span className="inline-flex items-center gap-1.5">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  syncing ? 'bg-sky-400 animate-ping' : 'bg-emerald-400'
                }`}
              />
              {latestSync
                ? t('overview.lastSyncedAt', { time: latestSync })
                : t('overview.neverSynced')}
            </span>
            <span aria-hidden="true" className="text-slate-600">
              ·
            </span>
            <span>{t('overview.autoRefreshHint', { seconds: autoRefreshSeconds })}</span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            data-testid="overview-refresh-quota"
            onClick={() => void handleManualSync()}
            disabled={syncing}
            className="mac-btn-secondary px-3.5 py-2 rounded-xl text-sm font-medium flex items-center gap-2 disabled:opacity-60"
          >
            <RefreshCw className={`w-4 h-4 ${syncing ? 'animate-spin text-sky-400' : ''}`} />
            <span>{syncing ? t('overview.refreshingQuota') : t('overview.refreshQuota')}</span>
          </button>
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              onLinkAccount();
            }}
            className="mac-btn-secondary px-3.5 py-2 rounded-xl text-sm font-medium flex items-center gap-2"
          >
            <Users className="w-4 h-4 text-sky-400" />
            <span>{t('overview.linkAccount')}</span>
          </button>
          <button
            type="button"
            onClick={() => {
              triggerHaptic('medium');
              onNewSession();
            }}
            className="mac-btn-primary px-4 py-2 rounded-xl text-sm font-medium flex items-center gap-2"
          >
            <Play className="w-4 h-4 fill-current" />
            <span>{t('overview.launchRuntime')}</span>
            {macDesktop && <kbd className="mac-kbd text-[10px]">⌘N</kbd>}
          </button>
        </div>
      </div>

      {/* Bento Telemetry Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Card 1: Pool CCU Balance + Quota Progress Bar */}
        <div
          onMouseMove={updateCardSpotlight}
          className="mac-card mac-card-hover p-5 flex flex-col justify-between space-y-3"
        >
          <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider">
            <span>{t('overview.poolCcuBalance')}</span>
            <div className="flex items-center gap-1">
              {renderMetricSyncButton()}
              <span className="p-1.5 rounded-lg bg-amber-500/10 border border-amber-400/20">
                <Zap className="w-4 h-4 text-amber-400" />
              </span>
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-white tabular-nums tracking-tight">
              {totalCcu.toFixed(2)}{' '}
              <span className="text-sm font-semibold text-amber-300/90">CCU</span>
            </div>
            <div className="mt-2.5 h-1.5 w-full rounded-full bg-white/[0.06] overflow-hidden">
              <div
                className={`h-full rounded-full bg-gradient-to-r ${ccuBarGradient} transition-all duration-500`}
                style={{ width: `${ccuProgressPct}%` }}
              />
            </div>
          </div>
          <p className="text-xs text-slate-400">
            {t('overview.acrossAccounts', { count: accounts.length })}
          </p>
        </div>

        {/* Card 2: Hourly Burn Rate + Runway Indicator */}
        <div
          onMouseMove={updateCardSpotlight}
          className="mac-card mac-card-hover p-5 flex flex-col justify-between space-y-3"
        >
          <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider">
            <span>{t('overview.hourlyBurnRate')}</span>
            <div className="flex items-center gap-1">
              {renderMetricSyncButton()}
              <span className="p-1.5 rounded-lg bg-rose-500/10 border border-rose-400/20">
                <Flame className="w-4 h-4 text-rose-400" />
              </span>
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-white tabular-nums tracking-tight">
              {totalBurnRate.toFixed(2)}{' '}
              <span className="text-sm font-semibold text-slate-400">CCU/h</span>
            </div>
            <div className="mt-2 flex items-center gap-1.5">
              <span
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border tabular-nums ${
                  runwayLabel
                    ? 'bg-amber-500/10 text-amber-300 border-amber-400/25'
                    : 'bg-white/[0.05] text-slate-400 border-white/10'
                }`}
              >
                <Clock className="w-3 h-3" />
                {runwayLabel
                  ? isZh
                    ? `预计续航 ~${runwayLabel}`
                    : `Est. runway ~${runwayLabel}`
                  : isZh
                  ? '当前无消耗'
                  : 'Zero burn'}
              </span>
            </div>
          </div>
          <p className="text-xs text-slate-400">{t('overview.estimatedConsumption')}</p>
        </div>

        {/* Card 3: Active Runtimes */}
        <div
          onMouseMove={updateCardSpotlight}
          className="mac-card mac-card-hover p-5 flex flex-col justify-between space-y-3"
        >
          <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider">
            <span>{t('overview.activeRuntimes')}</span>
            <span className="p-1.5 rounded-lg bg-emerald-500/10 border border-emerald-400/20">
              <Cpu className="w-4 h-4 text-emerald-400" />
            </span>
          </div>
          <div>
            <div className="text-2xl font-bold text-white tabular-nums tracking-tight flex items-center gap-2.5">
              <span>{activeSessions.length}</span>
              {activeSessions.length > 0 && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-400/25">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  {isZh ? '在线' : 'Live'}
                </span>
              )}
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {activeSessions.slice(0, 3).map((s) => (
                <span
                  key={s.id}
                  className={`px-1.5 py-0.5 rounded text-[10px] font-mono border ${getAcceleratorBadgeClass(
                    s.accelerator
                  )}`}
                >
                  {s.accelerator}
                </span>
              ))}
            </div>
          </div>
          <p className="text-xs text-slate-400">{t('overview.runningColabVms')}</p>
        </div>

        {/* Card 4: Accounts Ready */}
        <div
          onMouseMove={updateCardSpotlight}
          className="mac-card mac-card-hover p-5 flex flex-col justify-between space-y-3"
        >
          <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider">
            <span>{t('overview.accountsReady')}</span>
            <span className="p-1.5 rounded-lg bg-blue-500/10 border border-blue-400/20">
              <Users className="w-4 h-4 text-blue-400" />
            </span>
          </div>
          <div>
            <div className="text-2xl font-bold text-white tabular-nums tracking-tight">
              {readyAccountsCount}{' '}
              <span className="text-base font-medium text-slate-400">/ {accounts.length}</span>
            </div>
            <div className="mt-2.5 flex items-center gap-1">
              {accounts.slice(0, 8).map((acc) => (
                <span
                  key={acc.id}
                  title={acc.email}
                  className={`h-1.5 flex-1 rounded-full ${
                    acc.status === 'ACTIVE'
                      ? 'bg-emerald-400/80'
                      : acc.status === 'COOLDOWN'
                      ? 'bg-sky-400/80'
                      : 'bg-amber-400/80'
                  }`}
                />
              ))}
              {accounts.length === 0 && (
                <span className="h-1.5 w-full rounded-full bg-white/[0.06]" />
              )}
            </div>
          </div>
          <p className="text-xs text-slate-400">{t('overview.readyForAssignment')}</p>
        </div>
      </div>

      {/* Glowing Orphan Alert Banner */}
      {orphanSessions.length > 0 && (
        <div className="mac-card rounded-2xl p-4 sm:p-5 border border-amber-400/35 bg-amber-950/25 shadow-[0_0_32px_-10px_rgba(245,158,11,0.45)] flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-start sm:items-center gap-3.5">
            <div className="p-2.5 rounded-xl bg-amber-500/15 border border-amber-400/30 text-amber-300 shrink-0">
              <AlertTriangle className="w-5 h-5 animate-pulse" />
            </div>
            <div>
              <div className="text-sm font-semibold text-amber-200">
                {t('overview.orphanDiscoveredTitle', { count: orphanSessions.length })}
              </div>
              <div className="text-xs text-amber-200/80 mt-0.5 leading-relaxed">
                {t('overview.orphanDiscoveredDesc')}
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              onOpenSession(orphanSessions[0]);
            }}
            className="mac-btn-secondary px-3.5 py-2 rounded-xl text-xs font-medium text-amber-200 border-amber-400/30 shrink-0 self-start sm:self-auto"
          >
            {t('overview.workbench')} →
          </button>
        </div>
      )}

      {/* Active Runtimes Glass Table */}
      <div className="mac-card overflow-hidden">
        <div className="px-6 py-4 border-b border-white/[0.08] flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <h3 className="font-semibold text-white text-base">{t('overview.activeRuntimes')}</h3>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-white/[0.06] border border-white/[0.08] text-slate-300 tabular-nums">
              {t('overview.instancesCount', { count: activeSessions.length })}
            </span>
          </div>
        </div>

        {activeSessions.length === 0 ? (
          <div className="p-12 text-center space-y-4">
            <div className="w-14 h-14 mx-auto rounded-2xl bg-white/[0.04] border border-white/[0.08] flex items-center justify-center text-slate-400 shadow-inner">
              <Sparkles className="w-6 h-6 text-blue-400/80" />
            </div>
            <p className="text-sm text-slate-400 max-w-md mx-auto">
              {t('overview.noActiveInstances')}
            </p>
            <button
              type="button"
              onClick={() => {
                triggerHaptic('medium');
                onNewSession();
              }}
              className="mac-btn-primary px-4 py-2 rounded-xl text-xs font-medium inline-flex items-center gap-2"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>{t('overview.launchRuntime')}</span>
            </button>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-300">
              <thead className="bg-white/[0.02] text-[11px] uppercase tracking-wider font-semibold text-slate-400 border-b border-white/[0.08]">
                <tr>
                  <th className="px-6 py-3.5">{t('overview.colSessionName')}</th>
                  <th className="px-6 py-3.5">{t('overview.colAccelerator')}</th>
                  <th className="px-6 py-3.5">{t('overview.colAccount')}</th>
                  <th className="px-6 py-3.5">{t('overview.colStatus')}</th>
                  <th className="px-6 py-3.5">{t('overview.colSshPort')}</th>
                  <th className="px-6 py-3.5 text-right">{t('overview.colActions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {activeSessions.map((sess) => {
                  const statusStyle = getSessionStatusStyles(sess.status);
                  const isCopied = copiedSshId === sess.id;
                  return (
                    <tr key={sess.id} className="hover:bg-white/[0.03] transition-colors">
                      <td className="px-6 py-4 font-medium text-white">
                        <div className="flex items-center gap-2.5">
                          <span
                            className={`w-2 h-2 rounded-full shrink-0 ${statusStyle.dotClass} ${
                              statusStyle.isPulsing ? 'animate-pulse' : ''
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
                          <span className="text-slate-500">{t('common.na')}</span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="inline-flex items-center justify-end gap-2">
                          {macDesktop && sess.ssh_local_port && (
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
                              <ExternalLink className="w-3 h-3" /> {t('overview.openColab')}
                            </a>
                          )}
                          <button
                            type="button"
                            onClick={() => {
                              triggerHaptic('light');
                              onOpenSession(sess);
                            }}
                            className="mac-btn-primary px-3 py-1.5 rounded-lg text-xs font-medium"
                          >
                            {t('overview.workbench')}
                          </button>
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
    </div>
  );
};
