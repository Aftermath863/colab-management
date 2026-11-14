import React, { useEffect, useState } from 'react';
import { Cpu, Loader2, X } from 'lucide-react';
import { Account, Snapshot, Template } from '../types';
import { api } from '../services/api';
import { useI18n, TranslationKey } from '../i18n';
import {
  ACCELERATOR_IDS,
  AcceleratorId,
  MachineShapeType,
  isHighMemOnlyAccelerator,
  resolveEffectiveShape,
} from '../utils/accelerators';
import { triggerHaptic } from '../desktop/macBridge';
import { useToast } from '../desktop/ToastContext';
import { MODAL_BACKDROP_CLASSES, ModalPortal } from './ModalPortal';

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
  initialSnapshotId?: string;
  onLaunch: (params: {
    name: string;
    accelerator: string;
    shape: string;
    lifecycle_policy: string;
    account_id?: string;
    template_id?: string;
    snapshot_id?: string;
  }) => Promise<void>;
  onClose: () => void;
}

export const NewSessionModal: React.FC<Props> = ({
  accounts,
  initialSnapshotId,
  onLaunch,
  onClose,
}) => {
  const { t, te, locale, formatLifecycle } = useI18n();
  const isZh = locale === 'zh';
  const toast = useToast();

  const [name, setName] = useState(`session-${Math.random().toString(36).substring(2, 7)}`);
  const [accelerator, setAccelerator] = useState('T4');
  const [preferredShape, setPreferredShape] = useState<MachineShapeType>('standard');
  const [lifecyclePolicy, setLifecyclePolicy] = useState('interactive');
  const [accountId, setAccountId] = useState('');
  const [initMode, setInitMode] = useState<'none' | 'snapshot' | 'template'>(
    initialSnapshotId ? 'snapshot' : 'none',
  );
  const [snapshotId, setSnapshotId] = useState(initialSnapshotId || '');
  const [templateId, setTemplateId] = useState('');
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.getSnapshots().catch(() => []),
      api.getTemplates().catch(() => []),
    ]).then(([snaps, tpls]) => {
      setSnapshots(snaps.filter((s) => s.status === 'READY'));
      setTemplates(tpls);
    });
  }, []);

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

  const selectedSnapshot =
    initMode === 'snapshot' && snapshotId
      ? snapshots.find((s) => s.id === snapshotId)
      : undefined;

  useEffect(() => {
    if (selectedSnapshot) {
      setAccountId(selectedSnapshot.account_id);
    }
  }, [selectedSnapshot]);

  const effectiveAccountId = selectedSnapshot ? selectedSnapshot.account_id : accountId;

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
    triggerHaptic('light');
    try {
      await onLaunch({
        name: name.trim(),
        accelerator,
        shape: effectiveShape,
        lifecycle_policy: lifecyclePolicy,
        account_id: effectiveAccountId || undefined,
        template_id: initMode === 'template' && templateId ? templateId : undefined,
        snapshot_id: initMode === 'snapshot' && snapshotId ? snapshotId : undefined,
      });
      toast.pushToast({
        variant: 'success',
        title: isZh ? `实例 "${name.trim()}" 已分配` : `Runtime "${name.trim()}" provisioned`,
        description: `${accelerator} (${effectiveShape})`,
      });
      onClose();
    } catch (err: any) {
      triggerHaptic('medium');
      setError(err.message || 'Failed to provision session');
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
          className="w-full max-w-lg mac-modal-panel mac-glass-heavy rounded-2xl border border-white/[0.1] p-6 shadow-glass-lg overflow-y-auto"
          onClick={(e) => e.stopPropagation()}
        >
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-lg font-bold flex items-center gap-2.5 text-white">
            <span className="w-8 h-8 rounded-xl bg-blue-500/15 border border-blue-400/30 flex items-center justify-center text-blue-400">
              <Cpu className="w-4 h-4" />
            </span>
            {t('newSession.title')}
          </h3>
          <div className="flex items-center gap-2">
            <kbd className="mac-kbd hidden sm:inline-flex">Esc</kbd>
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition-colors disabled:opacity-40"
              title={t('common.close')}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              {t('newSession.sessionName')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 mac-input rounded-xl text-sm"
              required
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              {t('newSession.hardwareAccelerator')}
            </label>
            <select
              value={accelerator}
              onChange={(e) => {
                triggerHaptic('light');
                setAccelerator(e.target.value);
              }}
              className="w-full px-3 py-2 mac-input rounded-xl text-sm"
            >
              {accelerators.map((acc) => (
                <option key={acc.id} value={acc.id} className="bg-slate-900 text-white">
                  {acc.id} — {acc.name}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                {t('newSession.machineShape')}
              </label>
              <select
                value={effectiveShape}
                onChange={(e) => setPreferredShape(e.target.value as MachineShapeType)}
                disabled={highMemOnly}
                className="w-full px-3 py-2 mac-input rounded-xl text-sm disabled:opacity-60 disabled:cursor-not-allowed"
              >
                <option value="standard" className="bg-slate-900 text-white">
                  {t('newSession.shapeStandard')}
                </option>
                <option value="hm" className="bg-slate-900 text-white">
                  {t('newSession.shapeHighRam')}
                </option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                {t('newSession.lifecyclePolicy')}
              </label>
              <select
                value={lifecyclePolicy}
                onChange={(e) => setLifecyclePolicy(e.target.value)}
                className="w-full px-3 py-2 mac-input rounded-xl text-sm"
              >
                <option value="interactive" className="bg-slate-900 text-white">
                  {formatLifecycle('interactive')}
                </option>
                <option value="keep_alive" className="bg-slate-900 text-white">
                  {formatLifecycle('keep_alive')}
                </option>
                <option value="ephemeral" className="bg-slate-900 text-white">
                  {formatLifecycle('ephemeral')}
                </option>
              </select>
            </div>
          </div>

          {highMemOnly && (
            <p className="-mt-1 text-[11px] text-amber-300/90 bg-amber-500/10 border border-amber-400/20 rounded-lg px-2.5 py-1.5">
              {t('newSession.highRamOnlyHint')}
            </p>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              {t('snapshots.initModeLabel')}
            </label>
            <div className="mac-pill-group w-full grid grid-cols-3 gap-1">
              {(['none', 'snapshot', 'template'] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => {
                    triggerHaptic('light');
                    setInitMode(mode);
                  }}
                  className={`px-2.5 py-2 rounded-lg text-xs font-medium transition-all ${
                    initMode === mode
                      ? 'mac-pill-active text-white'
                      : 'text-slate-400 hover:text-slate-200 border border-transparent'
                  }`}
                >
                  {mode === 'none'
                    ? t('snapshots.initModeNone')
                    : mode === 'snapshot'
                      ? t('snapshots.initModeSnapshot')
                      : t('snapshots.initModeTemplate')}
                </button>
              ))}
            </div>
          </div>

          {initMode === 'snapshot' && (
            <div>
              <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                {t('snapshots.snapshotSelectLabel')}
              </label>
              <select
                value={snapshotId}
                onChange={(e) => setSnapshotId(e.target.value)}
                required
                className="w-full px-3 py-2 mac-input rounded-xl text-sm"
              >
                <option value="" className="bg-slate-900 text-white">
                  {t('snapshots.snapshotSelectPlaceholder')}
                </option>
                {snapshots.map((sn) => (
                  <option key={sn.id} value={sn.id} className="bg-slate-900 text-white">
                    {sn.name} ({sn.account_email || sn.account_id})
                  </option>
                ))}
              </select>
            </div>
          )}

          {initMode === 'template' && (
            <div>
              <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
                {t('jobs.envTemplateLabel')}
              </label>
              <select
                value={templateId}
                onChange={(e) => setTemplateId(e.target.value)}
                className="w-full px-3 py-2 mac-input rounded-xl text-sm"
              >
                <option value="" className="bg-slate-900 text-white">
                  {t('jobs.envTemplateNone')}
                </option>
                {templates.map((tpl) => (
                  <option key={tpl.id} value={tpl.id} className="bg-slate-900 text-white">
                    {tpl.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1.5">
              {t('newSession.accountSelector')}
            </label>
            <select
              value={effectiveAccountId}
              onChange={(e) => setAccountId(e.target.value)}
              disabled={Boolean(selectedSnapshot)}
              className="w-full px-3 py-2 mac-input rounded-xl text-sm disabled:opacity-60 disabled:cursor-not-allowed"
            >
              <option value="" className="bg-slate-900 text-white">
                {t('newSession.autoSelectAccount')}
              </option>
              {accounts.map((acc) => (
                <option key={acc.id} value={acc.id} className="bg-slate-900 text-white">
                  {t('newSession.accountOption', {
                    email: acc.email,
                    ccu: acc.ccu_balance.toFixed(1),
                    active: acc.active_assignments,
                  })}
                </option>
              ))}
            </select>
            {selectedSnapshot && (
              <p className="mt-1.5 text-[11px] text-amber-300/90">
                {t('snapshots.snapshotAccountLockedHint', {
                  email: selectedSnapshot.account_email || selectedSnapshot.account_id,
                })}
              </p>
            )}
          </div>

          {error && (
            <div className="text-xs text-rose-300 bg-rose-950/50 p-3 rounded-xl border border-rose-500/35">
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
              disabled={loading}
              className="mac-btn-primary px-5 py-2 rounded-xl text-xs font-semibold gap-2 disabled:opacity-50"
            >
              {loading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {loading ? t('newSession.provisioning') : t('newSession.launchRuntime')}
            </button>
          </div>
        </form>
      </div>
    </div>
    </ModalPortal>
  );
};
