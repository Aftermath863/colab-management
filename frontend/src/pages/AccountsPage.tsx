import React, { useState, useEffect } from 'react';
import {
  Users,
  Plus,
  RefreshCw,
  Trash2,
  AlertCircle,
  Zap,
  Flame,
  ShieldAlert,
  X,
} from 'lucide-react';
import { Account } from '../types';
import { api } from '../services/api';
import { useI18n } from '../i18n';
import { formatSyncTimestamp } from '../utils/time';
import { MODAL_BACKDROP_CLASSES, ModalPortal } from '../components/ModalPortal';
import {
  triggerHaptic,
  notifyToast,
  updateCardSpotlight,
} from '../utils/macDesktop';

interface Props {
  accounts: Account[];
  onRefresh: () => Promise<void>;
  onLinkAccount: () => void;
  onSyncAll?: () => Promise<void>;
  isSyncingAll?: boolean;
  autoRefreshSeconds?: number;
}

export const AccountsPage: React.FC<Props> = ({
  accounts,
  onRefresh,
  onLinkAccount,
  onSyncAll,
  isSyncingAll = false,
  autoRefreshSeconds = 60,
}) => {
  const { t, locale, formatActionError, formatAccountStatus } = useI18n();
  const isZh = locale === 'zh';
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [localSyncingAll, setLocalSyncingAll] = useState<boolean>(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Account | null>(null);
  const syncingAll = isSyncingAll || localSyncingAll;

  useEffect(() => {
    if (!deleteTarget || typeof window === 'undefined') return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDeleteTarget(null);
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [deleteTarget]);

  const handleSyncAll = async () => {
    if (syncingAll) return;
    triggerHaptic('light');
    if (onSyncAll) {
      try {
        await onSyncAll();
      } catch (err: any) {
        triggerHaptic('medium');
        notifyToast(formatActionError('accounts.syncFailed', err?.message), undefined, 'error');
      }
      return;
    }
    setLocalSyncingAll(true);
    try {
      await api.syncAllAccounts();
      await onRefresh();
      triggerHaptic('success');
      notifyToast(
        isZh ? '账号池配额已同步' : 'Account Pool Synced',
        isZh ? '所有账号的 CCU 配额已更新' : 'Updated CCU metrics for all accounts',
        'success'
      );
    } catch (err: any) {
      triggerHaptic('medium');
      notifyToast(formatActionError('accounts.syncFailed', err?.message), undefined, 'error');
    } finally {
      setLocalSyncingAll(false);
    }
  };

  const handleSync = async (accountId: string, email: string) => {
    setSyncingId(accountId);
    triggerHaptic('light');
    try {
      await api.syncAccount(accountId);
      await onRefresh();
      triggerHaptic('success');
      notifyToast(isZh ? '账号配额已同步' : 'Account Synced', email, 'success');
    } catch (err: any) {
      triggerHaptic('medium');
      notifyToast(formatActionError('accounts.syncFailed', err?.message), undefined, 'error');
    } finally {
      setSyncingId(null);
    }
  };

  const confirmDeleteAccount = async () => {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setDeleteTarget(null);
    setDeletingId(target.id);
    triggerHaptic('medium');
    try {
      await api.deleteAccount(target.id);
      await onRefresh();
      triggerHaptic('success');
      notifyToast(isZh ? '账号已解绑' : 'Account Unlinked', target.email, 'success');
    } catch (err: any) {
      triggerHaptic('medium');
      notifyToast(formatActionError('accounts.deleteFailed', err?.message), undefined, 'error');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="p-4 sm:p-8 max-w-7xl mx-auto space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">{t('accounts.title')}</h2>
          <p className="text-sm text-slate-400 mt-1">{t('accounts.subtitle')}</p>
          <div className="mt-2 inline-flex items-center gap-2 px-2.5 py-0.5 rounded-full bg-white/[0.04] border border-white/[0.08] text-xs text-slate-400">
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                syncingAll ? 'bg-sky-400 animate-ping' : 'bg-emerald-400'
              }`}
            />
            <span>{t('accounts.autoSyncBadge', { seconds: autoRefreshSeconds })}</span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            data-testid="accounts-sync-all"
            onClick={() => void handleSyncAll()}
            disabled={syncingAll || accounts.length === 0}
            className="mac-btn-secondary px-3.5 py-2 rounded-xl text-sm font-medium flex items-center gap-2 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${syncingAll ? 'animate-spin text-sky-400' : ''}`} />
            <span>{syncingAll ? t('accounts.syncingAll') : t('accounts.syncAll')}</span>
          </button>
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              onLinkAccount();
            }}
            className="mac-btn-primary px-4 py-2 rounded-xl text-sm font-medium flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            <span>{t('accounts.linkGoogleAccount')}</span>
          </button>
        </div>
      </div>

      {/* Compute Wallet Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {accounts.map((acc) => {
          const isLow = acc.ccu_balance <= acc.warning_ccu;
          const isCritical = acc.ccu_balance <= acc.hard_stop_ccu;
          const quotaMax = Math.max(100, acc.warning_ccu * 5, acc.ccu_balance);
          const quotaPct =
            acc.ccu_balance <= 0
              ? 0
              : Math.min(100, Math.max(4, Math.round((acc.ccu_balance / quotaMax) * 100)));
          const barClass = isCritical
            ? 'from-rose-500 to-red-400'
            : isLow
            ? 'from-amber-500 to-yellow-400'
            : 'from-emerald-500 to-teal-400';

          const statusPillClass =
            acc.status === 'ACTIVE'
              ? 'bg-emerald-500/15 text-emerald-300 border-emerald-400/30'
              : acc.status === 'COOLDOWN'
              ? 'bg-sky-500/15 text-sky-300 border-sky-400/30'
              : 'bg-amber-500/15 text-amber-300 border-amber-400/30';

          const initial = (acc.display_name || acc.email || 'G')[0]?.toUpperCase() || 'G';

          return (
            <div
              key={acc.id}
              onMouseMove={updateCardSpotlight}
              className="mac-card mac-card-hover p-5 flex flex-col justify-between space-y-4"
            >
              {/* Wallet Header */}
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-3 min-w-0">
                  <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500/25 via-indigo-500/20 to-cyan-500/20 border border-white/[0.12] flex items-center justify-center text-sm font-bold text-blue-200 shrink-0">
                    {initial}
                  </div>
                  <div className="min-w-0">
                    <h3
                      className="font-semibold text-white text-base truncate"
                      title={acc.email}
                    >
                      {acc.display_name || acc.email.split('@')[0]}
                    </h3>
                    <p className="text-xs text-slate-400 truncate font-mono" title={acc.email}>
                      {acc.email}
                    </p>
                  </div>
                </div>

                <span
                  className={`px-2.5 py-0.5 text-[11px] font-semibold rounded-full border shrink-0 ${statusPillClass}`}
                >
                  {formatAccountStatus(acc.status)}
                </span>
              </div>

              {/* CCU Quota Bar & Burn Rate */}
              <div className="p-3.5 rounded-xl bg-white/[0.03] border border-white/[0.06] space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <div className="text-[11px] text-slate-400 flex items-center gap-1">
                      <Zap className="w-3 h-3 text-amber-400" />
                      <span>{t('accounts.ccuBalance')}</span>
                    </div>
                    <div
                      className={`text-xl font-bold tabular-nums mt-0.5 ${
                        isCritical ? 'text-rose-400' : isLow ? 'text-amber-300' : 'text-white'
                      }`}
                    >
                      {acc.ccu_balance.toFixed(2)}
                    </div>
                  </div>

                  <div>
                    <div className="text-[11px] text-slate-400 flex items-center gap-1">
                      <Flame className="w-3 h-3 text-rose-400" />
                      <span>{t('accounts.burnRate')}</span>
                    </div>
                    <div className="text-xl font-bold text-slate-200 tabular-nums mt-0.5">
                      {t('accounts.burnRateUnit', { rate: acc.burn_rate_hourly.toFixed(2) })}
                    </div>
                  </div>
                </div>

                <div>
                  <div className="h-1.5 w-full rounded-full bg-white/[0.06] overflow-hidden">
                    <div
                      className={`h-full rounded-full bg-gradient-to-r ${barClass} transition-all duration-500`}
                      style={{ width: `${quotaPct}%` }}
                    />
                  </div>
                </div>
              </div>

              {/* Assignment & Priority Badges */}
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="px-2.5 py-1 rounded-lg bg-white/[0.04] border border-white/[0.07] text-slate-300 tabular-nums">
                  {t('accounts.activeAssignments')}{' '}
                  <strong className="text-white">{acc.active_assignments}</strong>
                </span>
                <span className="px-2.5 py-1 rounded-lg bg-indigo-500/10 border border-indigo-400/25 text-indigo-200 tabular-nums">
                  {t('accounts.priority')} <strong className="text-white">{acc.priority}</strong>
                </span>
              </div>

              {isCritical && (
                <div className="text-xs text-rose-300 bg-rose-500/10 px-3 py-2 rounded-xl flex items-center gap-2 border border-rose-400/30">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0 text-rose-400" />
                  <span>{t('accounts.hardStopReached', { ccu: acc.hard_stop_ccu })}</span>
                </div>
              )}

              {/* Footer */}
              <div className="flex items-center justify-between pt-3 border-t border-white/[0.08]">
                <span className="text-[11px] text-slate-400 tabular-nums">
                  {acc.last_synced_at
                    ? t('accounts.syncedAt', { date: formatSyncTimestamp(acc.last_synced_at) })
                    : t('accounts.neverSynced')}
                </span>

                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => void handleSync(acc.id, acc.email)}
                    disabled={syncingId === acc.id}
                    className="p-1.5 hover:bg-white/[0.08] rounded-lg text-slate-300 hover:text-white transition disabled:opacity-50"
                    title={t('accounts.syncTooltip')}
                    aria-label={t('accounts.syncTooltip')}
                  >
                    <RefreshCw
                      className={`w-4 h-4 ${syncingId === acc.id ? 'animate-spin text-sky-400' : ''}`}
                    />
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      triggerHaptic('light');
                      setDeleteTarget(acc);
                    }}
                    disabled={deletingId === acc.id}
                    className="p-1.5 hover:bg-rose-500/15 rounded-lg text-rose-400 hover:text-rose-300 transition disabled:opacity-50"
                    title={t('accounts.deleteTooltip')}
                    aria-label={t('accounts.deleteTooltip')}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {accounts.length === 0 && (
        <div className="mac-card p-12 text-center space-y-4">
          <div className="w-14 h-14 mx-auto rounded-2xl bg-white/[0.04] border border-white/[0.08] flex items-center justify-center text-slate-400">
            <Users className="w-7 h-7 text-blue-400/80" />
          </div>
          <div>
            <p className="text-base font-semibold text-white">{t('accounts.noAccountsTitle')}</p>
            <p className="text-xs text-slate-400 mt-1 max-w-md mx-auto">
              {t('accounts.noAccountsDesc')}
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              onLinkAccount();
            }}
            className="mac-btn-primary px-4 py-2 rounded-xl text-xs font-medium inline-flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            <span>{t('accounts.linkGoogleAccount')}</span>
          </button>
        </div>
      )}

      {/* Delete Confirmation Glass Modal */}
      {deleteTarget && (
        <ModalPortal>
          <div
            className={MODAL_BACKDROP_CLASSES}
            onClick={() => setDeleteTarget(null)}
          >
            <div
              className="mac-modal-panel mac-card w-full max-w-md p-6 space-y-5 shadow-2xl overflow-y-auto"
              onClick={(e) => e.stopPropagation()}
            >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-rose-500/15 border border-rose-400/30 text-rose-300">
                  <ShieldAlert className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-semibold text-white">
                    {t('accounts.deleteTooltip')}
                  </h3>
                  <p className="text-xs text-slate-400 font-mono mt-0.5">{deleteTarget.email}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                aria-label={t('common.close')}
                className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <p className="text-sm text-slate-300 leading-relaxed">
              {t('accounts.confirmDelete', { email: deleteTarget.email })}
            </p>

            <div className="flex justify-end gap-2.5 pt-2 border-t border-white/[0.08]">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium"
              >
                {t('common.cancel')}
              </button>
              <button
                type="button"
                onClick={() => void confirmDeleteAccount()}
                className="px-4 py-2 rounded-xl text-xs font-semibold bg-rose-600 hover:bg-rose-500 text-white transition shadow-lg shadow-rose-600/20"
              >
                {t('common.delete')}
              </button>
            </div>
          </div>
        </div>
        </ModalPortal>
      )}
    </div>
  );
};
