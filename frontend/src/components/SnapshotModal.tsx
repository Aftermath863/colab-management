import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Camera,
  CheckCircle2,
  DownloadCloud,
  ExternalLink,
  HardDrive,
  Loader2,
  RefreshCw,
  ShieldAlert,
  UploadCloud,
  X,
} from 'lucide-react';
import { api } from '../services/api';
import { Session, Snapshot, SnapshotTask } from '../types';
import { useI18n } from '../i18n';
import { openExternalUrl, triggerHaptic } from '../desktop/macBridge';
import { useToast } from '../desktop/ToastContext';
import { MODAL_BACKDROP_CLASSES, ModalPortal } from './ModalPortal';

interface SnapshotModalProps {
  session: Session;
  initialTab?: 'save' | 'restore';
  initialStopAfterSave?: boolean;
  initialRestoreSnapshotId?: string;
  autoStartRestore?: boolean;
  onClose: () => void;
  onCompleted?: (task: SnapshotTask) => void;
}

function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let idx = 0;
  let val = bytes;
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx += 1;
  }
  return `${val >= 10 || idx === 0 ? val.toFixed(0) : val.toFixed(1)} ${units[idx]}`;
}

export const SnapshotModal: React.FC<SnapshotModalProps> = ({
  session,
  initialTab = 'save',
  initialStopAfterSave = false,
  initialRestoreSnapshotId = '',
  autoStartRestore = false,
  onClose,
  onCompleted,
}) => {
  const { t } = useI18n();
  const toast = useToast();

  const [activeTab, setActiveTab] = useState<'save' | 'restore'>(initialTab);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [loadingList, setLoadingList] = useState(true);

  // Save form state
  const [saveMode, setSaveMode] = useState<'new' | 'overwrite'>('new');
  const [overwriteId, setOverwriteId] = useState('');
  const [name, setName] = useState(`${session.name}-env`);
  const [description, setDescription] = useState('');
  const [includeBinary, setIncludeBinary] = useState(false);
  const [stopAfterSave, setStopAfterSave] = useState(initialStopAfterSave);

  // Restore form state
  const [restoreSnapshotId, setRestoreSnapshotId] = useState(initialRestoreSnapshotId);

  // Task, auth popup & error state
  const [task, setTask] = useState<SnapshotTask | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authRedirectUrl, setAuthRedirectUrl] = useState<string | null>(null);
  const [waitingAuth, setWaitingAuth] = useState(false);
  const [pendingAction, setPendingAction] = useState<'save' | 'restore' | null>(null);

  const popupRef = useRef<Window | null>(null);
  const authPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const taskPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoTriggeredRef = useRef(false);
  const completionNotifiedRef = useRef<string | null>(null);

  const readySnapshots = snapshots.filter((s) => s.status === 'READY');

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [submitting, onClose]);

  const loadSnapshots = useCallback(async () => {
    setLoadingList(true);
    try {
      const data = await api.getSnapshots(session.account_id);
      setSnapshots(data);
      const ready = data.filter((s) => s.status === 'READY');
      if (ready.length > 0 && !restoreSnapshotId) {
        setRestoreSnapshotId(ready[0].id);
      }
      if (ready.length > 0 && !overwriteId) {
        setOverwriteId(ready[0].id);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to load snapshots');
    } finally {
      setLoadingList(false);
    }
  }, [session.account_id, restoreSnapshotId, overwriteId]);

  const pollTaskOnce = useCallback(async () => {
    try {
      const current = await api.getSessionSnapshotTask(session.id);
      if (current && current.status !== 'IDLE') {
        setTask(current);
        if (current.status === 'COMPLETED') {
          if (taskPollRef.current) {
            clearInterval(taskPollRef.current);
            taskPollRef.current = null;
          }
          await loadSnapshots();
          const doneKey = `${current.task_id || ''}:${current.status}`;
          if (completionNotifiedRef.current !== doneKey) {
            completionNotifiedRef.current = doneKey;
            toast.pushToast({
              variant: 'success',
              title: t('snapshots.taskCompleted'),
              description: current.message || session.name,
            });
            onCompleted?.(current);
          }
        } else if (current.status === 'FAILED') {
          if (taskPollRef.current) {
            clearInterval(taskPollRef.current);
            taskPollRef.current = null;
          }
          toast.pushToast({
            variant: 'error',
            title: t('snapshots.taskFailed'),
            description: current.error_message || current.message,
          });
        }
      }
    } catch {
      // Ignore transient poll glitches
    }
  }, [session.id, session.name, loadSnapshots, onCompleted, t, toast]);

  const startTaskPolling = useCallback(() => {
    if (taskPollRef.current) clearInterval(taskPollRef.current);
    taskPollRef.current = setInterval(pollTaskOnce, 1500);
  }, [pollTaskOnce]);

  useEffect(() => {
    loadSnapshots();
    pollTaskOnce();
    return () => {
      if (authPollRef.current) clearInterval(authPollRef.current);
      if (taskPollRef.current) clearInterval(taskPollRef.current);
    };
  }, [loadSnapshots, pollTaskOnce]);

  const handleSave = useCallback(async () => {
    setError(null);
    setAuthRedirectUrl(null);
    setSubmitting(true);
    triggerHaptic('light');
    try {
      const targetOverwrite = saveMode === 'overwrite' ? overwriteId : undefined;
      const chosenSnap = targetOverwrite ? readySnapshots.find((s) => s.id === targetOverwrite) : undefined;
      const res = await api.saveSessionSnapshot(session.id, {
        name: saveMode === 'overwrite' && chosenSnap ? chosenSnap.name : (name.trim() || `${session.name}-env`),
        description: saveMode === 'overwrite' && chosenSnap ? (description.trim() || chosenSnap.description) : description.trim(),
        capture_mode: includeBinary ? 'full_binary' : 'hybrid',
        overwrite_snapshot_id: targetOverwrite,
        stop_after_save: stopAfterSave,
      });

      if (res.action_status === 'AUTH_REQUIRED' && res.redirect_url) {
        setPendingAction('save');
        setAuthRedirectUrl(res.redirect_url);
        return;
      }
      if (res.task) {
        setTask(res.task);
        startTaskPolling();
      }
    } catch (err: any) {
      triggerHaptic('medium');
      setError(err.message || 'Snapshot save failed');
    } finally {
      setSubmitting(false);
    }
  }, [
    session.id,
    session.name,
    saveMode,
    overwriteId,
    readySnapshots,
    name,
    description,
    includeBinary,
    stopAfterSave,
    startTaskPolling,
  ]);

  const handleRestore = useCallback(
    async (explicitId?: string) => {
      const targetId = explicitId || restoreSnapshotId;
      if (!targetId) return;
      setError(null);
      setAuthRedirectUrl(null);
      setSubmitting(true);
      triggerHaptic('light');
      try {
        const res = await api.restoreSessionSnapshot(session.id, targetId);
        if (res.action_status === 'AUTH_REQUIRED' && res.redirect_url) {
          setPendingAction('restore');
          setAuthRedirectUrl(res.redirect_url);
          return;
        }
        if (res.task) {
          setTask(res.task);
          startTaskPolling();
        }
      } catch (err: any) {
        triggerHaptic('medium');
        setError(err.message || 'Snapshot restore failed');
      } finally {
        setSubmitting(false);
      }
    },
    [session.id, restoreSnapshotId, startTaskPolling],
  );

  useEffect(() => {
    if (autoStartRestore && initialRestoreSnapshotId && !autoTriggeredRef.current) {
      autoTriggeredRef.current = true;
      setActiveTab('restore');
      setRestoreSnapshotId(initialRestoreSnapshotId);
      handleRestore(initialRestoreSnapshotId);
    }
  }, [autoStartRestore, initialRestoreSnapshotId, handleRestore]);

  const handleOpenAuthPopup = () => {
    if (!authRedirectUrl || typeof window === 'undefined') return;
    setWaitingAuth(true);
    const popup = window.open(authRedirectUrl, 'colab-gdrive-snapshot-auth', 'width=600,height=720');
    if (!popup) {
      openExternalUrl(authRedirectUrl);
      setWaitingAuth(false);
      return;
    }
    popupRef.current = popup;
    if (authPollRef.current) clearInterval(authPollRef.current);
    authPollRef.current = setInterval(async () => {
      if (popupRef.current && popupRef.current.closed) {
        if (authPollRef.current) {
          clearInterval(authPollRef.current);
          authPollRef.current = null;
        }
        setWaitingAuth(false);
        setAuthRedirectUrl(null);
        const actionToResume = pendingAction;
        setPendingAction(null);
        if (actionToResume === 'restore') {
          await handleRestore();
        } else if (actionToResume === 'save') {
          await handleSave();
        }
      }
    }, 900);
  };

  const isRunning = task?.status === 'RUNNING';

  return (
    <ModalPortal>
      <div
        className={MODAL_BACKDROP_CLASSES}
        onClick={() => {
          if (!submitting) onClose();
        }}
        role="dialog"
        aria-modal="true"
      >
        <div
          className="w-full max-w-2xl mac-modal-panel mac-glass-heavy border border-white/[0.1] rounded-2xl shadow-glass-lg overflow-hidden flex flex-col"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="px-6 py-4 bg-white/[0.02] border-b border-white/[0.08] flex items-center justify-between shrink-0">
          <div className="flex items-center space-x-3">
            <div className="p-2 rounded-xl bg-indigo-500/15 border border-indigo-400/30 text-indigo-400">
              <Camera className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-white">{t('snapshots.modalTitle')}</h3>
              <p className="text-xs text-slate-400">
                {session.name} ({session.account_email || session.account_id})
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <kbd className="mac-kbd hidden sm:inline-flex">Esc</kbd>
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition-colors"
              title={t('common.close')}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Segmented Tabs */}
        <div className="px-6 py-3 border-b border-white/[0.07] bg-black/20 shrink-0">
          <div className="mac-pill-group gap-1">
            <button
              type="button"
              onClick={() => {
                triggerHaptic('light');
                setActiveTab('save');
              }}
              className={`px-4 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition-all ${
                activeTab === 'save'
                  ? 'mac-pill-active text-white'
                  : 'text-slate-400 hover:text-slate-200 border border-transparent'
              }`}
            >
              <UploadCloud className="w-3.5 h-3.5" />
              <span>{t('snapshots.saveTab')}</span>
            </button>
            <button
              type="button"
              onClick={() => {
                triggerHaptic('light');
                setActiveTab('restore');
              }}
              className={`px-4 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition-all ${
                activeTab === 'restore'
                  ? 'mac-pill-active text-white'
                  : 'text-slate-400 hover:text-slate-200 border border-transparent'
              }`}
            >
              <DownloadCloud className="w-3.5 h-3.5" />
              <span>{t('snapshots.restoreTab')}</span>
            </button>
          </div>
        </div>

        <div className="p-6 space-y-4 min-h-0 flex-1 overflow-y-auto">
          {/* Google Drive Authorization Banner */}
          {authRedirectUrl && (
            <div className="p-4 rounded-xl bg-amber-950/35 border border-amber-400/35 space-y-2.5">
              <div className="flex items-start space-x-2.5">
                <ShieldAlert className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                <div>
                  <h4 className="text-sm font-semibold text-amber-200">{t('snapshots.authRequiredTitle')}</h4>
                  <p className="text-xs text-amber-200/80 mt-0.5">{t('snapshots.authRequiredDesc')}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={handleOpenAuthPopup}
                disabled={waitingAuth}
                className="px-3.5 py-2 rounded-xl bg-amber-600 hover:bg-amber-500 active:scale-[0.97] text-white text-xs font-semibold flex items-center space-x-2 transition-all disabled:opacity-60"
              >
                {waitingAuth ? <Loader2 className="w-4 h-4 animate-spin" /> : <ExternalLink className="w-4 h-4" />}
                <span>{waitingAuth ? t('snapshots.waitingAuthPopup') : t('snapshots.openAuthPopup')}</span>
              </button>
            </div>
          )}

          {/* Error Banner */}
          {error && (
            <div className="p-3 rounded-xl bg-rose-950/50 border border-rose-500/35 text-xs text-rose-200">
              {error}
            </div>
          )}

          {/* Live Task Progress */}
          {task && task.status !== 'IDLE' && (
            <div
              className={`p-3.5 rounded-xl border text-xs space-y-2 ${
                task.status === 'COMPLETED'
                  ? 'bg-emerald-950/30 border-emerald-400/35 text-emerald-200'
                  : task.status === 'FAILED'
                    ? 'bg-rose-950/30 border-rose-400/35 text-rose-200'
                    : 'bg-indigo-950/30 border-indigo-400/35 text-indigo-200'
              }`}
            >
              <div className="flex items-center justify-between font-semibold">
                <span className="flex items-center space-x-2">
                  {task.status === 'RUNNING' && <Loader2 className="w-4 h-4 animate-spin text-indigo-400" />}
                  {task.status === 'COMPLETED' && <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
                  <span>
                    {task.status === 'RUNNING'
                      ? t('snapshots.taskRunning')
                      : task.status === 'COMPLETED'
                        ? t('snapshots.taskCompleted')
                        : t('snapshots.taskFailed')}
                  </span>
                </span>
                <span className="font-mono tabular-nums">{task.progress_pct ?? 0}%</span>
              </div>
              <div className="w-full h-1.5 rounded-full bg-white/[0.08] overflow-hidden">
                <div
                  className={`h-full transition-all duration-300 ${
                    task.status === 'FAILED'
                      ? 'bg-rose-500'
                      : task.status === 'COMPLETED'
                        ? 'bg-emerald-500'
                        : 'bg-indigo-500'
                  }`}
                  style={{ width: `${Math.min(100, Math.max(5, task.progress_pct ?? 10))}%` }}
                />
              </div>
              {task.message && <p className="text-[11px] opacity-90">{task.message}</p>}
            </div>
          )}

          {activeTab === 'save' ? (
            <div className="space-y-4">
              {/* New vs Overwrite Toggle */}
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">
                  {t('snapshots.saveTargetLabel')}
                </label>
                <div className="mac-pill-group w-full grid grid-cols-2 gap-1">
                  <button
                    type="button"
                    onClick={() => {
                      triggerHaptic('light');
                      setSaveMode('new');
                    }}
                    className={`px-3 py-2 rounded-lg text-xs font-semibold transition-all ${
                      saveMode === 'new'
                        ? 'mac-pill-active text-white'
                        : 'text-slate-400 hover:text-slate-200 border border-transparent'
                    }`}
                  >
                    {t('snapshots.saveAsNew')}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      triggerHaptic('light');
                      setSaveMode('overwrite');
                    }}
                    disabled={readySnapshots.length === 0}
                    className={`px-3 py-2 rounded-lg text-xs font-semibold transition-all disabled:opacity-40 ${
                      saveMode === 'overwrite'
                        ? 'mac-pill-active text-white'
                        : 'text-slate-400 hover:text-slate-200 border border-transparent'
                    }`}
                  >
                    {t('snapshots.overwriteExisting')}
                  </button>
                </div>
              </div>

              {saveMode === 'overwrite' ? (
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1.5">
                    {t('snapshots.selectOverwriteLabel')}
                  </label>
                  {readySnapshots.length === 0 ? (
                    <p className="text-xs text-slate-500">{t('snapshots.noOverwritableSnapshots')}</p>
                  ) : (
                    <select
                      value={overwriteId}
                      onChange={(e) => setOverwriteId(e.target.value)}
                      className="w-full px-3 py-2 rounded-xl mac-input text-sm"
                    >
                      {readySnapshots.map((s) => (
                        <option key={s.id} value={s.id} className="bg-slate-900 text-white">
                          {s.name} ({formatBytes(s.workspace_size_bytes)})
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              ) : (
                <>
                  <div>
                    <label className="block text-xs font-medium text-slate-300 mb-1.5">
                      {t('snapshots.nameLabel')}
                    </label>
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder={t('snapshots.namePlaceholder')}
                      className="w-full px-3 py-2 rounded-xl mac-input text-sm placeholder-slate-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-300 mb-1.5">
                      {t('snapshots.descLabel')}
                    </label>
                    <input
                      type="text"
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder={t('snapshots.descPlaceholder')}
                      className="w-full px-3 py-2 rounded-xl mac-input text-sm placeholder-slate-500"
                    />
                  </div>
                </>
              )}

              {/* Full Binary Option */}
              <div className="p-3.5 rounded-xl bg-white/[0.03] border border-white/[0.08] space-y-2">
                <label className="flex items-start space-x-2.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={includeBinary}
                    onChange={(e) => setIncludeBinary(e.target.checked)}
                    className="mt-0.5 rounded bg-slate-900 border-white/20 text-indigo-500 focus:ring-indigo-500"
                  />
                  <div>
                    <div className="text-xs font-semibold text-slate-200">{t('snapshots.fullBinaryCheckbox')}</div>
                    <p className="text-[11px] text-slate-400 mt-0.5">
                      {includeBinary ? t('snapshots.fullBinaryHint') : t('snapshots.hybridHint')}
                    </p>
                  </div>
                </label>
              </div>

              {/* Stop After Save Option */}
              <label className="flex items-center space-x-2.5 cursor-pointer px-1">
                <input
                  type="checkbox"
                  checked={stopAfterSave}
                  onChange={(e) => setStopAfterSave(e.target.checked)}
                  className="rounded bg-slate-900 border-white/20 text-indigo-500 focus:ring-indigo-500"
                />
                <span className="text-xs text-slate-300">{t('snapshots.stopAfterSaveCheckbox')}</span>
              </label>

              <div className="pt-3 flex items-center justify-end space-x-3 border-t border-white/[0.07]">
                <button
                  type="button"
                  onClick={onClose}
                  className="mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium"
                >
                  {t('common.close')}
                </button>
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={submitting || isRunning || (saveMode === 'overwrite' && !overwriteId)}
                  className="mac-btn-primary px-4 py-2 rounded-xl text-xs font-semibold gap-2 disabled:opacity-50"
                >
                  {(submitting || isRunning) && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <span>{submitting || isRunning ? t('snapshots.savingButton') : t('snapshots.startSaveButton')}</span>
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-slate-300">{t('snapshots.selectRestoreLabel')}</label>
                <button
                  type="button"
                  onClick={loadSnapshots}
                  className="text-xs text-indigo-300 hover:text-indigo-200 flex items-center space-x-1"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingList ? 'animate-spin' : ''}`} />
                  <span>{t('common.refresh')}</span>
                </button>
              </div>

              {readySnapshots.length === 0 ? (
                <div className="p-4 rounded-xl bg-white/[0.03] border border-white/[0.08] text-xs text-slate-400">
                  {t('snapshots.noRestoreSnapshots')}
                </div>
              ) : (
                <select
                  value={restoreSnapshotId}
                  onChange={(e) => setRestoreSnapshotId(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl mac-input text-sm"
                >
                  {readySnapshots.map((s) => (
                    <option key={s.id} value={s.id} className="bg-slate-900 text-white">
                      {s.name} · {s.capture_mode === 'full_binary' ? t('snapshots.modeFullBinary') : t('snapshots.modeHybrid')} ·{' '}
                      {formatBytes(s.workspace_size_bytes)}
                    </option>
                  ))}
                </select>
              )}

              <div className="p-3 rounded-xl bg-white/[0.03] border border-white/[0.08] flex items-start space-x-2 text-xs text-slate-400">
                <HardDrive className="w-4 h-4 text-slate-400 shrink-0 mt-0.5" />
                <span>{t('snapshots.restoreWarning')}</span>
              </div>

              <div className="pt-3 flex items-center justify-end space-x-3 border-t border-white/[0.07]">
                <button
                  type="button"
                  onClick={onClose}
                  className="mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium"
                >
                  {t('common.close')}
                </button>
                <button
                  type="button"
                  onClick={() => handleRestore()}
                  disabled={submitting || isRunning || !restoreSnapshotId}
                  className="px-4 py-2 rounded-xl bg-gradient-to-b from-emerald-500 to-emerald-600 hover:brightness-110 active:scale-[0.97] border border-emerald-300/30 shadow-[0_6px_20px_-6px_rgba(16,185,129,0.55),inset_0_1px_0_rgba(255,255,255,0.22)] text-white text-xs font-semibold flex items-center space-x-2 transition-all disabled:opacity-50"
                >
                  {(submitting || isRunning) && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  <span>
                    {submitting || isRunning ? t('snapshots.restoringButton') : t('snapshots.startRestoreButton')}
                  </span>
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
    </ModalPortal>
  );
};
