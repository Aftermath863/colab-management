import React, { useState } from 'react';
import { Cpu, X } from 'lucide-react';
import { Account } from '../types';
import { useI18n, TranslationKey } from '../i18n';
import {
  ACCELERATOR_IDS,
  AcceleratorId,
  MachineShapeType,
  isHighMemOnlyAccelerator,
  resolveEffectiveShape,
} from '../utils/accelerators';

const SESSION_ACCELERATOR_KEY_MAP: Record<AcceleratorId, TranslationKey> = {
  CPU: 'newSession.accCPU',
  T4: 'newSession.accT4',
  G4: 'newSession.accG4',
  L4: 'newSession.accL4',
  A100: 'newSession.accA100',
  H100: 'newSession.accH100',
  V5E1: 'newSession.accV5E1',
  V6E1: 'newSession.accV6E1',
};

interface Props {
  accounts: Account[];
  onLaunch: (params: {
    name: string;
    accelerator: string;
    shape: string;
    lifecycle_policy: string;
    account_id?: string;
  }) => Promise<void>;
  onClose: () => void;
}

export const NewSessionModal: React.FC<Props> = ({ accounts, onLaunch, onClose }) => {
  const { t, te, formatLifecycle } = useI18n();
  const [name, setName] = useState(`session-${Math.random().toString(36).substring(2, 7)}`);
  const [accelerator, setAccelerator] = useState('T4');
  const [preferredShape, setPreferredShape] = useState<MachineShapeType>('standard');
  const [lifecyclePolicy, setLifecyclePolicy] = useState('interactive');
  const [accountId, setAccountId] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const highMemOnly = isHighMemOnlyAccelerator(accelerator);
  const effectiveShape = resolveEffectiveShape(accelerator, preferredShape);

  const accelerators = ACCELERATOR_IDS.map((id) => ({
    id,
    name: t(SESSION_ACCELERATOR_KEY_MAP[id]),
  }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    setError(null);
    try {
      await onLaunch({
        name: name.trim(),
        accelerator,
        shape: effectiveShape,
        lifecycle_policy: lifecyclePolicy,
        account_id: accountId || undefined,
      });
      onClose();
    } catch (err: any) {
      setError(err.message || 'Failed to provision session');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-xl bg-slate-800 border border-slate-700 p-6 shadow-2xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold flex items-center gap-2 text-white">
            <Cpu className="w-5 h-5 text-blue-400" />
            {t('newSession.title')}
          </h3>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-white" title={t('common.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1">
              {t('newSession.sessionName')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-white focus:border-blue-500 focus:outline-none"
              required
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1">
              {t('newSession.hardwareAccelerator')}
            </label>
            <select
              value={accelerator}
              onChange={(e) => setAccelerator(e.target.value)}
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-white focus:border-blue-500 focus:outline-none"
            >
              {accelerators.map((acc) => (
                <option key={acc.id} value={acc.id}>
                  {acc.id} — {acc.name}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1">
                {t('newSession.machineShape')}
              </label>
              <select
                value={effectiveShape}
                onChange={(e) => setPreferredShape(e.target.value as MachineShapeType)}
                disabled={highMemOnly}
                className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-white focus:border-blue-500 focus:outline-none disabled:opacity-60 disabled:cursor-not-allowed"
              >
                <option value="standard">{t('newSession.shapeStandard')}</option>
                <option value="hm">{t('newSession.shapeHighRam')}</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1">
                {t('newSession.lifecyclePolicy')}
              </label>
              <select
                value={lifecyclePolicy}
                onChange={(e) => setLifecyclePolicy(e.target.value)}
                className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-white focus:border-blue-500 focus:outline-none"
              >
                <option value="interactive">{formatLifecycle('interactive')}</option>
                <option value="keep_alive">{formatLifecycle('keep_alive')}</option>
                <option value="ephemeral">{formatLifecycle('ephemeral')}</option>
              </select>
            </div>
          </div>

          {highMemOnly && (
            <p className="-mt-2 text-[11px] text-amber-400/90">
              {t('newSession.highRamOnlyHint')}
            </p>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1">
              {t('newSession.accountSelector')}
            </label>
            <select
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-white focus:border-blue-500 focus:outline-none"
            >
              <option value="">{t('newSession.autoSelectAccount')}</option>
              {accounts.map((acc) => (
                <option key={acc.id} value={acc.id}>
                  {t('newSession.accountOption', {
                    email: acc.email,
                    ccu: acc.ccu_balance.toFixed(1),
                    active: acc.active_assignments,
                  })}
                </option>
              ))}
            </select>
          </div>

          {error && (
            <div className="text-xs text-red-400 bg-red-950/40 p-2.5 rounded border border-red-800">
              {te(error)}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded text-sm text-slate-300"
            >
              {t('common.cancel')}
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded text-sm font-medium text-white transition flex items-center gap-2"
            >
              {loading ? t('newSession.provisioning') : t('newSession.launchRuntime')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
