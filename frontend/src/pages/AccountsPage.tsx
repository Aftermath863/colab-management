import React, { useState } from 'react';
import { Users, Plus, RefreshCw, Trash2, AlertCircle } from 'lucide-react';
import { Account } from '../types';
import { api } from '../services/api';
import { useI18n } from '../i18n';

interface Props {
  accounts: Account[];
  onRefresh: () => Promise<void>;
  onLinkAccount: () => void;
}

export const AccountsPage: React.FC<Props> = ({ accounts, onRefresh, onLinkAccount }) => {
  const { t, formatActionError, formatAccountStatus } = useI18n();
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const handleSync = async (accountId: string) => {
    setSyncingId(accountId);
    try {
      await api.syncAccount(accountId);
      await onRefresh();
    } catch (err: any) {
      alert(formatActionError('accounts.syncFailed', err?.message));
    } finally {
      setSyncingId(null);
    }
  };

  const handleDelete = async (accountId: string, email: string) => {
    if (!confirm(t('accounts.confirmDelete', { email }))) {
      return;
    }
    setDeletingId(accountId);
    try {
      await api.deleteAccount(accountId);
      await onRefresh();
    } catch (err: any) {
      alert(formatActionError('accounts.deleteFailed', err?.message));
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
        </div>

        <button
          type="button"
          onClick={onLinkAccount}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition flex items-center gap-2 shadow-lg shadow-blue-600/20"
        >
          <Plus className="w-4 h-4" /> {t('accounts.linkGoogleAccount')}
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {accounts.map((acc) => {
          const isLow = acc.ccu_balance <= acc.warning_ccu;
          const isCritical = acc.ccu_balance <= acc.hard_stop_ccu;

          return (
            <div
              key={acc.id}
              className="bg-slate-800/80 border border-slate-750 rounded-xl p-5 space-y-4 hover:border-slate-650 transition"
            >
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-semibold text-white text-base truncate max-w-[200px]" title={acc.email}>
                    {acc.display_name || acc.email.split('@')[0]}
                  </h3>
                  <p className="text-xs text-slate-400 truncate max-w-[220px]" title={acc.email}>
                    {acc.email}
                  </p>
                </div>

                <span
                  className={`px-2 py-0.5 text-xs font-semibold rounded ${
                    acc.status === 'ACTIVE'
                      ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                      : 'bg-amber-950 text-amber-400 border border-amber-800'
                  }`}
                >
                  {formatAccountStatus(acc.status)}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-3 pt-2 border-t border-slate-750">
                <div>
                  <div className="text-xs text-slate-400">{t('accounts.ccuBalance')}</div>
                  <div
                    className={`text-xl font-bold ${
                      isCritical ? 'text-red-400' : isLow ? 'text-amber-400' : 'text-white'
                    }`}
                  >
                    {acc.ccu_balance.toFixed(2)}
                  </div>
                </div>

                <div>
                  <div className="text-xs text-slate-400">{t('accounts.burnRate')}</div>
                  <div className="text-xl font-bold text-slate-200">
                    {t('accounts.burnRateUnit', { rate: acc.burn_rate_hourly.toFixed(2) })}
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between text-xs text-slate-400 pt-1">
                <span>
                  {t('accounts.activeAssignments')} <strong className="text-slate-200">{acc.active_assignments}</strong>
                </span>
                <span>
                  {t('accounts.priority')} <strong className="text-slate-200">{acc.priority}</strong>
                </span>
              </div>

              {isCritical && (
                <div className="text-xs text-red-400 bg-red-950/40 p-2 rounded flex items-center gap-1.5 border border-red-800/50">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                  {t('accounts.hardStopReached', { ccu: acc.hard_stop_ccu })}
                </div>
              )}

              <div className="flex items-center justify-between pt-3 border-t border-slate-750">
                <span className="text-[11px] text-slate-500">
                  {acc.last_synced_at
                    ? t('accounts.syncedAt', { date: acc.last_synced_at.split(' ')[0] })
                    : t('accounts.neverSynced')}
                </span>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleSync(acc.id)}
                    disabled={syncingId === acc.id}
                    className="p-1.5 hover:bg-slate-700 rounded text-slate-300 transition"
                    title={t('accounts.syncTooltip')}
                  >
                    <RefreshCw className={`w-4 h-4 ${syncingId === acc.id ? 'animate-spin' : ''}`} />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(acc.id, acc.email)}
                    disabled={deletingId === acc.id}
                    className="p-1.5 hover:bg-red-950/50 rounded text-red-400 transition"
                    title={t('accounts.deleteTooltip')}
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
        <div className="bg-slate-800/40 border border-slate-800 rounded-xl p-12 text-center text-slate-500">
          <Users className="w-12 h-12 mx-auto text-slate-600 mb-3" />
          <p className="text-base font-medium text-slate-400">{t('accounts.noAccountsTitle')}</p>
          <p className="text-xs text-slate-500 mt-1">{t('accounts.noAccountsDesc')}</p>
        </div>
      )}
    </div>
  );
};
