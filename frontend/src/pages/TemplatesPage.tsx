import React, { useState, useEffect } from 'react';
import {
  Layers,
  Key,
  Plus,
  Trash2,
  Package,
  FileCode,
  CheckCircle2,
  AlertCircle,
  X,
  Eye,
  EyeOff,
  Camera,
  RefreshCw,
  Play,
  ExternalLink,
  Lock,
  ShieldCheck,
} from 'lucide-react';
import { api } from '../services/api';
import { Template, Secret, StorageProfile, Snapshot, Session } from '../types';
import { LocalizedText, useI18n } from '../i18n';
import { MODAL_BACKDROP_CLASSES, ModalPortal } from '../components/ModalPortal';
import {
  isMacDesktop,
  triggerHaptic,
  notifyToast,
  openExternalUrl,
  updateCardSpotlight,
  getAcceleratorBadgeClass,
} from '../utils/macDesktop';

interface TemplatesPageProps {
  sessions?: Session[];
  onLaunchFromSnapshot?: (snapshot: Snapshot) => void;
  initialTab?: 'templates' | 'secrets' | 'snapshots';
}

function formatSnapSize(bytes: number): string {
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

type ConfirmDeleteState =
  | { kind: 'template'; id: string; name: string }
  | { kind: 'secret'; id: string; name: string }
  | { kind: 'snapshot'; id: string; name: string };

export const TemplatesPage: React.FC<TemplatesPageProps> = ({
  sessions: propSessions,
  onLaunchFromSnapshot,
  initialTab = 'templates',
}) => {
  const { t, tm, locale, formatStorageType } = useI18n();
  const isZh = locale === 'zh';
  const macDesktop = isMacDesktop();

  const [activeTab, setActiveTab] = useState<'templates' | 'secrets' | 'snapshots'>(initialTab);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [profiles, setProfiles] = useState<StorageProfile[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [availableSessions, setAvailableSessions] = useState<Session[]>(propSessions || []);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<{
    type: 'success' | 'error';
    msg: LocalizedText;
  } | null>(null);

  // Delete confirmation modal
  const [confirmDelete, setConfirmDelete] = useState<ConfirmDeleteState | null>(null);

  // Sync-from-Drive Modal state
  const [isSyncModalOpen, setIsSyncModalOpen] = useState(false);
  const [syncSessionId, setSyncSessionId] = useState('');
  const [syncingDrive, setSyncingDrive] = useState(false);
  const [syncAuthUrl, setSyncAuthUrl] = useState<string | null>(null);

  // Template Modal State
  const [isTemplateModalOpen, setIsTemplateModalOpen] = useState(false);
  const [tplName, setTplName] = useState('');
  const [tplPackages, setTplPackages] = useState('');
  const [tplSelectedSecrets, setTplSelectedSecrets] = useState<string[]>([]);
  const [tplSelectedProfiles, setTplSelectedProfiles] = useState<string[]>([]);
  const [tplStartupScript, setTplStartupScript] = useState('');

  // Secret Modal State
  const [isSecretModalOpen, setIsSecretModalOpen] = useState(false);
  const [secKeyName, setSecKeyName] = useState('');
  const [secValue, setSecValue] = useState('');
  const [secDescription, setSecDescription] = useState('');
  const [showSecretValue, setShowSecretValue] = useState(false);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [tplData, secData, profData, snapData, sessData] = await Promise.all([
        api.getTemplates(),
        api.getSecrets(),
        api.getStorageProfiles(),
        api.getSnapshots().catch(() => []),
        propSessions ? Promise.resolve(propSessions) : api.getSessions().catch(() => []),
      ]);
      setTemplates(tplData);
      setSecrets(secData);
      setProfiles(profData);
      setSnapshots(snapData);
      setAvailableSessions(sessData);
    } catch (err: any) {
      const msg: LocalizedText = err.message ? { raw: err.message } : { key: 'templates.loadFailed' };
      setFeedback({ type: 'error', msg });
      notifyToast(tm(msg), undefined, 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAll();
  }, []);

  // Close active modal on Esc
  useEffect(() => {
    if (
      (!isSyncModalOpen && !isTemplateModalOpen && !isSecretModalOpen && !confirmDelete) ||
      typeof window === 'undefined'
    ) {
      return;
    }
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (confirmDelete) setConfirmDelete(null);
      else if (isSyncModalOpen) setIsSyncModalOpen(false);
      else if (isTemplateModalOpen) setIsTemplateModalOpen(false);
      else if (isSecretModalOpen) setIsSecretModalOpen(false);
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [isSyncModalOpen, isTemplateModalOpen, isSecretModalOpen, confirmDelete]);

  const executeConfirmDelete = async () => {
    if (!confirmDelete) return;
    const target = confirmDelete;
    setConfirmDelete(null);
    triggerHaptic('medium');

    if (target.kind === 'snapshot') {
      try {
        await api.deleteSnapshot(target.id);
        triggerHaptic('success');
        notifyToast(
          isZh ? '快照记录已删除' : 'Snapshot Deleted',
          target.name,
          'success'
        );
        await loadAll();
      } catch (err: any) {
        const msg: LocalizedText = { raw: err.message || 'Failed to delete snapshot' };
        setFeedback({ type: 'error', msg });
        notifyToast(tm(msg), undefined, 'error');
      }
      return;
    }

    if (target.kind === 'template') {
      try {
        await api.deleteTemplate(target.id);
        const msg: LocalizedText = {
          key: 'templates.templateDeleted',
          params: { name: target.name },
        };
        setFeedback({ type: 'success', msg });
        triggerHaptic('success');
        notifyToast(tm(msg), undefined, 'success');
        void loadAll();
      } catch (err: any) {
        const msg: LocalizedText = err.message
          ? { raw: err.message }
          : { key: 'templates.templateDeleteFailed' };
        setFeedback({ type: 'error', msg });
        notifyToast(tm(msg), undefined, 'error');
      }
      return;
    }

    try {
      await api.deleteSecret(target.id);
      const msg: LocalizedText = {
        key: 'templates.secretDeleted',
        params: { name: target.name },
      };
      setFeedback({ type: 'success', msg });
      triggerHaptic('success');
      notifyToast(tm(msg), undefined, 'success');
      void loadAll();
    } catch (err: any) {
      const msg: LocalizedText = err.message
        ? { raw: err.message }
        : { key: 'templates.secretDeleteFailed' };
      setFeedback({ type: 'error', msg });
      notifyToast(tm(msg), undefined, 'error');
    }
  };

  const handleSyncFromDrive = async (targetSessId?: string) => {
    const sessId = targetSessId || syncSessionId;
    if (!sessId) return;
    setSyncingDrive(true);
    setSyncAuthUrl(null);
    triggerHaptic('light');
    try {
      const res = await api.syncSnapshotsFromDrive(sessId);
      if (res.action_status === 'AUTH_REQUIRED' && res.redirect_url) {
        setSyncAuthUrl(res.redirect_url);
        return;
      }
      const msg: LocalizedText = { key: 'snapshots.syncSuccess' };
      setFeedback({ type: 'success', msg });
      triggerHaptic('success');
      notifyToast(tm(msg), undefined, 'success');
      setIsSyncModalOpen(false);
      await loadAll();
    } catch (err: any) {
      const msg: LocalizedText = { raw: err.message || 'Sync failed' };
      setFeedback({ type: 'error', msg });
      triggerHaptic('medium');
      notifyToast(tm(msg), undefined, 'error');
    } finally {
      setSyncingDrive(false);
    }
  };

  const resetTemplateForm = () => {
    setTplName('');
    setTplPackages('');
    setTplSelectedSecrets([]);
    setTplSelectedProfiles([]);
    setTplStartupScript('');
  };

  const handleCreateTemplate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tplName.trim()) return;

    const pkgs = tplPackages
      .split(/[,\s\n]+/)
      .map((p) => p.trim())
      .filter(Boolean);

    const createdName = tplName.trim();
    triggerHaptic('light');
    try {
      await api.createTemplate({
        name: createdName,
        packages: pkgs,
        secret_ids: tplSelectedSecrets,
        storage_profile_ids: tplSelectedProfiles,
        startup_script: tplStartupScript,
      });
      const msg: LocalizedText = {
        key: 'templates.templateCreated',
        params: { name: createdName },
      };
      setFeedback({ type: 'success', msg });
      triggerHaptic('success');
      notifyToast(tm(msg), undefined, 'success');
      setIsTemplateModalOpen(false);
      resetTemplateForm();
      void loadAll();
    } catch (err: any) {
      const msg: LocalizedText = err.message
        ? { raw: err.message }
        : { key: 'templates.templateCreateFailed' };
      setFeedback({ type: 'error', msg });
      triggerHaptic('medium');
      notifyToast(tm(msg), undefined, 'error');
    }
  };

  const resetSecretForm = () => {
    setSecKeyName('');
    setSecValue('');
    setSecDescription('');
    setShowSecretValue(false);
  };

  const handleCreateSecret = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!secKeyName.trim() || !secValue.trim()) return;

    const normalizedName = secKeyName.trim().toUpperCase();
    triggerHaptic('light');
    try {
      await api.createSecret({
        key_name: normalizedName,
        value: secValue.trim(),
        description: secDescription.trim(),
      });
      const msg: LocalizedText = {
        key: 'templates.secretCreated',
        params: { name: normalizedName },
      };
      setFeedback({ type: 'success', msg });
      triggerHaptic('success');
      notifyToast(tm(msg), undefined, 'success');
      setIsSecretModalOpen(false);
      resetSecretForm();
      void loadAll();
    } catch (err: any) {
      const msg: LocalizedText = err.message
        ? { raw: err.message }
        : { key: 'templates.secretCreateFailed' };
      setFeedback({ type: 'error', msg });
      triggerHaptic('medium');
      notifyToast(tm(msg), undefined, 'error');
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-8 py-6 sm:py-8 space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2.5">
            <Layers className="w-7 h-7 text-indigo-400" />
            <span>{t('templates.title')}</span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">{t('templates.subtitle')}</p>
        </div>

        <div className="flex gap-2">
          {activeTab === 'templates' ? (
            <button
              type="button"
              onClick={() => {
                triggerHaptic('light');
                setIsTemplateModalOpen(true);
              }}
              className="mac-btn-primary px-4 py-2 rounded-xl text-sm font-medium flex items-center gap-2"
            >
              <Plus className="w-4 h-4" />
              <span>{t('templates.newTemplate')}</span>
            </button>
          ) : activeTab === 'secrets' ? (
            <button
              type="button"
              onClick={() => {
                triggerHaptic('light');
                setIsSecretModalOpen(true);
              }}
              className="px-4 py-2 bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-400/35 text-emerald-100 rounded-xl text-sm font-medium transition flex items-center gap-2 shadow-lg shadow-emerald-950/30"
            >
              <Key className="w-4 h-4 text-emerald-300" />
              <span>{t('templates.addSecret')}</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={() => {
                triggerHaptic('light');
                const readySessions = availableSessions.filter(
                  (s) => s.status === 'READY' || s.status === 'BUSY'
                );
                if (readySessions.length > 0 && !syncSessionId) {
                  setSyncSessionId(readySessions[0].id);
                }
                setSyncAuthUrl(null);
                setIsSyncModalOpen(true);
              }}
              className="mac-btn-primary px-4 py-2 rounded-xl text-sm font-medium flex items-center gap-2"
            >
              <RefreshCw className="w-4 h-4" />
              <span>{t('snapshots.syncDriveButton')}</span>
            </button>
          )}
        </div>
      </div>

      {/* Feedback Banner */}
      {feedback && (
        <div
          className={`mac-card p-4 rounded-xl text-sm flex items-center justify-between ${
            feedback.type === 'success'
              ? 'border-emerald-400/30 bg-emerald-500/10 text-emerald-300'
              : 'border-rose-400/30 bg-rose-500/10 text-rose-300'
          }`}
        >
          <div className="flex items-center gap-2.5">
            {feedback.type === 'success' ? (
              <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-400" />
            ) : (
              <AlertCircle className="w-4 h-4 shrink-0 text-rose-400" />
            )}
            <span>{tm(feedback.msg)}</span>
          </div>
          <button
            type="button"
            onClick={() => setFeedback(null)}
            title={t('common.close')}
            className="p-1 hover:bg-white/[0.08] rounded-lg transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Segmented Pill Tabs */}
      <div className="mac-pill-group inline-flex flex-wrap items-center gap-1 p-1 rounded-xl">
        <button
          type="button"
          onClick={() => {
            triggerHaptic('alignment');
            setActiveTab('templates');
          }}
          className={`py-2 px-4 rounded-lg text-xs font-semibold transition flex items-center gap-2 ${
            activeTab === 'templates'
              ? 'mac-pill-active text-white'
              : 'text-slate-400 hover:text-slate-100 hover:bg-white/[0.04]'
          }`}
        >
          <Layers className="w-3.5 h-3.5 text-indigo-300" />
          <span>{t('templates.tabTemplates', { count: templates.length })}</span>
        </button>
        <button
          type="button"
          onClick={() => {
            triggerHaptic('alignment');
            setActiveTab('secrets');
          }}
          className={`py-2 px-4 rounded-lg text-xs font-semibold transition flex items-center gap-2 ${
            activeTab === 'secrets'
              ? 'mac-pill-active text-white'
              : 'text-slate-400 hover:text-slate-100 hover:bg-white/[0.04]'
          }`}
        >
          <Key className="w-3.5 h-3.5 text-emerald-300" />
          <span>{t('templates.tabSecrets', { count: secrets.length })}</span>
        </button>
        <button
          type="button"
          onClick={() => {
            triggerHaptic('alignment');
            setActiveTab('snapshots');
          }}
          className={`py-2 px-4 rounded-lg text-xs font-semibold transition flex items-center gap-2 ${
            activeTab === 'snapshots'
              ? 'mac-pill-active text-white'
              : 'text-slate-400 hover:text-slate-100 hover:bg-white/[0.04]'
          }`}
        >
          <Camera className="w-3.5 h-3.5 text-sky-300" />
          <span>
            {t('snapshots.tabTitle')} ({snapshots.length})
          </span>
        </button>
      </div>

      {/* Tab 1: Templates Grid */}
      {activeTab === 'templates' && (
        <div>
          {loading && templates.length === 0 ? (
            <div className="mac-card py-12 text-center text-slate-400">
              {t('templates.loadingTemplates')}
            </div>
          ) : templates.length === 0 ? (
            <div className="mac-card py-16 px-6 text-center space-y-3">
              <div className="w-12 h-12 mx-auto rounded-2xl bg-white/[0.04] border border-white/[0.08] flex items-center justify-center text-slate-400">
                <Layers className="w-6 h-6 text-indigo-400" />
              </div>
              <h3 className="text-base font-semibold text-white">
                {t('templates.emptyTemplatesTitle')}
              </h3>
              <p className="text-sm text-slate-400 max-w-md mx-auto">
                {t('templates.emptyTemplatesDesc')}
              </p>
              <button
                type="button"
                onClick={() => setIsTemplateModalOpen(true)}
                className="mac-btn-primary mt-2 px-4 py-2 rounded-xl text-xs font-medium inline-flex items-center gap-2"
              >
                <Plus className="w-4 h-4" />
                <span>{t('templates.createFirstTemplate')}</span>
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              {templates.map((tpl) => (
                <div
                  key={tpl.id}
                  onMouseMove={updateCardSpotlight}
                  className="mac-card mac-card-hover p-5 flex flex-col justify-between space-y-4"
                >
                  <div className="space-y-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h3 className="font-semibold text-white text-base truncate">{tpl.name}</h3>
                        <span className="text-[11px] text-slate-500 font-mono">ID: {tpl.id}</span>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          triggerHaptic('light');
                          setConfirmDelete({ kind: 'template', id: tpl.id, name: tpl.name });
                        }}
                        className="text-slate-400 hover:text-rose-400 transition-colors p-1.5 rounded-lg hover:bg-rose-500/10"
                        title={t('templates.deleteTemplateTooltip')}
                        aria-label={t('templates.deleteTemplateTooltip')}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>

                    {/* Packages */}
                    <div>
                      <div className="text-xs text-slate-400 font-medium mb-2 flex items-center gap-1.5">
                        <Package className="w-3.5 h-3.5 text-indigo-400" />
                        <span>{t('templates.pipPackagesCount', { count: tpl.packages.length })}</span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {tpl.packages.length > 0 ? (
                          tpl.packages.map((pkg, i) => (
                            <span
                              key={i}
                              className="px-2 py-0.5 bg-white/[0.05] border border-white/[0.09] text-indigo-300 rounded-md text-[11px] font-mono"
                            >
                              {pkg}
                            </span>
                          ))
                        ) : (
                          <span className="text-xs text-slate-500 italic">
                            {t('templates.noneSpecified')}
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Secrets & Storage */}
                    <div className="p-3 rounded-xl bg-white/[0.03] border border-white/[0.06] text-xs text-slate-400 space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="flex items-center gap-1.5">
                          <Key className="w-3.5 h-3.5 text-emerald-400" />
                          <span>{t('templates.attachedSecrets')}</span>
                        </span>
                        <span className="font-mono text-slate-200 tabular-nums">
                          {tpl.secret_ids.length}
                        </span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span>{t('templates.attachedStorage')}</span>
                        <span className="font-mono text-slate-200 tabular-nums">
                          {tpl.storage_profile_ids.length}
                        </span>
                      </div>
                    </div>

                    {/* Startup Script Snippet */}
                    {tpl.startup_script && (
                      <div>
                        <div className="text-xs text-slate-400 font-medium mb-1 flex items-center gap-1">
                          <FileCode className="w-3 h-3 text-amber-400" />
                          <span>{t('templates.startupScript')}</span>
                        </div>
                        <pre className="bg-slate-950/80 border border-white/[0.08] rounded-xl p-2.5 text-[11px] font-mono text-slate-300 max-h-20 overflow-hidden line-clamp-3">
                          {tpl.startup_script}
                        </pre>
                      </div>
                    )}
                  </div>

                  <div className="pt-3 border-t border-white/[0.08] flex items-center justify-between text-[11px] text-slate-500 tabular-nums">
                    <span>{t('templates.updatedAt', { date: tpl.updated_at.split('T')[0] })}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tab 2: Secrets Vault Table */}
      {activeTab === 'secrets' && (
        <div className="mac-card overflow-hidden">
          <div className="px-6 py-4 border-b border-white/[0.08] bg-white/[0.02] flex items-center gap-2.5">
            <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
            <p className="text-xs text-slate-300">{t('templates.secretsVaultBanner')}</p>
          </div>

          {loading && secrets.length === 0 ? (
            <div className="py-12 text-center text-slate-400">{t('templates.loadingSecrets')}</div>
          ) : secrets.length === 0 ? (
            <div className="py-16 text-center text-slate-400 space-y-2">
              <Key className="w-8 h-8 text-slate-500 mx-auto" />
              <p className="text-sm">{t('templates.emptySecrets')}</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-white/[0.02] text-slate-400 text-[11px] uppercase tracking-wider border-b border-white/[0.08]">
                  <tr>
                    <th className="py-3.5 px-6">{t('templates.colKeyName')}</th>
                    <th className="py-3.5 px-6">{t('templates.colDescription')}</th>
                    <th className="py-3.5 px-6">{t('templates.colStoredValue')}</th>
                    <th className="py-3.5 px-6">{t('templates.colCreated')}</th>
                    <th className="py-3.5 px-6 text-right">{t('templates.colActions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06] text-slate-300">
                  {secrets.map((sec) => (
                    <tr key={sec.id} className="hover:bg-white/[0.03] transition-colors">
                      <td className="py-3.5 px-6">
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-emerald-500/10 border border-emerald-400/25 font-mono text-xs text-emerald-300 font-semibold">
                          <Key className="w-3 h-3" />
                          {sec.key_name}
                        </span>
                      </td>
                      <td className="py-3.5 px-6 text-slate-400 text-xs">
                        {sec.description || '—'}
                      </td>
                      <td className="py-3.5 px-6">
                        <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-white/[0.04] border border-white/[0.09] font-mono text-[11px] text-slate-400 tracking-widest">
                          <Lock className="w-3 h-3 text-slate-500" />
                          <span>••••••••••••</span>
                        </span>
                      </td>
                      <td className="py-3.5 px-6 text-xs text-slate-400 tabular-nums">
                        {sec.created_at.split('T')[0]}
                      </td>
                      <td className="py-3.5 px-6 text-right">
                        <button
                          type="button"
                          onClick={() => {
                            triggerHaptic('light');
                            setConfirmDelete({ kind: 'secret', id: sec.id, name: sec.key_name });
                          }}
                          className="text-slate-400 hover:text-rose-400 transition-colors p-1.5 rounded-lg hover:bg-rose-500/10"
                          title={t('templates.deleteSecretTooltip')}
                          aria-label={t('templates.deleteSecretTooltip')}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Tab 3: Environment Snapshots */}
      {activeTab === 'snapshots' && (
        <div className="space-y-4">
          <p className="text-xs text-slate-400">{t('snapshots.pageSubtitle')}</p>
          {snapshots.length === 0 ? (
            <div className="mac-card py-16 px-6 text-center space-y-3">
              <div className="w-12 h-12 mx-auto rounded-2xl bg-white/[0.04] border border-white/[0.08] flex items-center justify-center text-slate-400">
                <Camera className="w-6 h-6 text-sky-400" />
              </div>
              <p className="text-sm text-slate-400 max-w-lg mx-auto">
                {t('snapshots.noSnapshots')}
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              {snapshots.map((snap) => (
                <div
                  key={snap.id}
                  onMouseMove={updateCardSpotlight}
                  className="mac-card mac-card-hover p-5 flex flex-col justify-between space-y-4"
                >
                  <div className="space-y-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <h3 className="text-base font-semibold text-white truncate">{snap.name}</h3>
                        {snap.description && (
                          <p className="text-xs text-slate-400 mt-0.5 line-clamp-2">
                            {snap.description}
                          </p>
                        )}
                      </div>
                      <span
                        className={`px-2.5 py-0.5 text-[11px] font-semibold rounded-full border shrink-0 ${
                          snap.status === 'READY'
                            ? 'bg-emerald-500/15 text-emerald-300 border-emerald-400/30'
                            : snap.status === 'FAILED'
                            ? 'bg-rose-500/15 text-rose-300 border-rose-400/30'
                            : 'bg-amber-500/15 text-amber-300 border-amber-400/30'
                        }`}
                      >
                        {snap.status === 'READY'
                          ? t('snapshots.statusReady')
                          : snap.status === 'FAILED'
                          ? t('snapshots.statusFailed')
                          : t('snapshots.statusCreating')}
                      </span>
                    </div>

                    <div className="flex flex-wrap gap-1.5 text-[11px]">
                      <span className="px-2.5 py-0.5 rounded-lg bg-indigo-500/15 text-indigo-200 border border-indigo-400/30">
                        {snap.capture_mode === 'full_binary'
                          ? t('snapshots.modeFullBinary')
                          : t('snapshots.modeHybrid')}
                      </span>
                      {snap.python_version && (
                        <span className="px-2 py-0.5 rounded-lg bg-white/[0.06] border border-white/[0.08] text-slate-200 font-mono">
                          Py {snap.python_version}
                        </span>
                      )}
                      <span
                        className={`px-2 py-0.5 rounded-lg border font-mono ${getAcceleratorBadgeClass(
                          snap.accelerator
                        )}`}
                      >
                        {snap.accelerator}
                      </span>
                    </div>

                    <div className="text-xs text-slate-400 space-y-1 pt-1 tabular-nums">
                      <div>
                        {t('snapshots.packagesSummary', {
                          apt: snap.apt_package_count,
                          pip: snap.pip_package_count,
                        })}{' '}
                        ·{' '}
                        {t('snapshots.workspaceSize', {
                          size: formatSnapSize(snap.workspace_size_bytes),
                        })}
                        {snap.pylibs_size_bytes > 0 &&
                          ` · ${t('snapshots.pylibsSize', {
                            size: formatSnapSize(snap.pylibs_size_bytes),
                          })}`}
                      </div>
                      <div className="truncate font-mono text-[11px] text-slate-400">
                        {t('snapshots.ownerAccountLabel')}: {snap.account_email || snap.account_id}
                      </div>
                      <div className="truncate font-mono text-[11px] text-slate-500">
                        {t('snapshots.drivePathLabel')}: {snap.drive_folder_path}
                      </div>
                    </div>
                  </div>

                  <div className="pt-3 border-t border-white/[0.08] flex items-center justify-between gap-2">
                    <button
                      type="button"
                      disabled={snap.status !== 'READY'}
                      onClick={() => {
                        triggerHaptic('medium');
                        onLaunchFromSnapshot?.(snap);
                      }}
                      className="mac-btn-primary flex-1 px-3 py-2 rounded-xl text-xs font-semibold flex items-center justify-center gap-1.5 disabled:opacity-40"
                    >
                      <Play className="w-3.5 h-3.5 fill-current" />
                      <span>{t('snapshots.launchWithSnapshot')}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        triggerHaptic('light');
                        setConfirmDelete({ kind: 'snapshot', id: snap.id, name: snap.name });
                      }}
                      title={t('snapshots.deleteSnapshot')}
                      aria-label={t('snapshots.deleteSnapshot')}
                      className="p-2 rounded-xl text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 transition"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {confirmDelete && (
        <ModalPortal>
          <div
            className={MODAL_BACKDROP_CLASSES}
            onClick={() => setConfirmDelete(null)}
          >
            <div
              className="mac-modal-panel mac-card w-full max-w-md p-6 space-y-5 shadow-2xl overflow-y-auto"
              onClick={(e) => e.stopPropagation()}
            >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-rose-500/15 border border-rose-400/30 text-rose-300">
                  <Trash2 className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-semibold text-white">
                    {confirmDelete.kind === 'template'
                      ? t('templates.deleteTemplateTooltip')
                      : confirmDelete.kind === 'secret'
                      ? t('templates.deleteSecretTooltip')
                      : t('snapshots.deleteSnapshot')}
                  </h3>
                  <p className="text-xs text-slate-400 font-mono mt-0.5">{confirmDelete.name}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setConfirmDelete(null)}
                aria-label={t('common.close')}
                className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <p className="text-sm text-slate-300 leading-relaxed">
              {confirmDelete.kind === 'template'
                ? t('templates.confirmDeleteTemplate', { name: confirmDelete.name })
                : confirmDelete.kind === 'secret'
                ? t('templates.confirmDeleteSecret', { name: confirmDelete.name })
                : t('snapshots.confirmDelete', { name: confirmDelete.name })}
            </p>

            <div className="flex justify-end gap-2.5 pt-2 border-t border-white/[0.08]">
              <button
                type="button"
                onClick={() => setConfirmDelete(null)}
                className="mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium"
              >
                {t('common.cancel')}
              </button>
              <button
                type="button"
                onClick={() => void executeConfirmDelete()}
                className="px-4 py-2 rounded-xl text-xs font-semibold bg-rose-600 hover:bg-rose-500 text-white transition"
              >
                {t('common.delete')}
              </button>
            </div>
          </div>
        </div>
        </ModalPortal>
      )}

      {/* Modal: Sync Snapshots from Google Drive */}
      {isSyncModalOpen && (
        <ModalPortal>
          <div
            className={MODAL_BACKDROP_CLASSES}
            onClick={() => setIsSyncModalOpen(false)}
          >
            <div
              className="mac-modal-panel mac-card max-w-md w-full p-6 space-y-4 shadow-2xl overflow-y-auto"
              onClick={(e) => e.stopPropagation()}
            >
            <div className="flex items-center justify-between pb-3 border-b border-white/[0.08]">
              <h3 className="text-sm font-bold text-white">
                {t('snapshots.syncSelectSessionTitle')}
              </h3>
              <button
                type="button"
                onClick={() => setIsSyncModalOpen(false)}
                aria-label={t('common.close')}
                className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-white/[0.08] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <p className="text-xs text-slate-400">{t('snapshots.syncSelectSessionDesc')}</p>

            {syncAuthUrl && (
              <div className="p-3.5 rounded-xl bg-amber-500/10 border border-amber-400/35 space-y-2.5">
                <p className="text-xs text-amber-200">{t('snapshots.authRequiredDesc')}</p>
                <a
                  href={syncAuthUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => {
                    if (macDesktop) {
                      e.preventDefault();
                      triggerHaptic('light');
                      openExternalUrl(syncAuthUrl);
                    }
                  }}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-500/25 hover:bg-amber-500/35 border border-amber-400/40 text-amber-100 text-xs font-semibold transition"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  <span>{t('snapshots.openAuthPopup')}</span>
                </a>
              </div>
            )}

            {availableSessions.filter((s) => s.status === 'READY' || s.status === 'BUSY').length ===
            0 ? (
              <div className="p-3.5 rounded-xl bg-white/[0.03] border border-white/[0.08] text-xs text-slate-400">
                {t('snapshots.syncNoActiveSession')}
              </div>
            ) : (
              <select
                value={syncSessionId}
                onChange={(e) => setSyncSessionId(e.target.value)}
                className="mac-input w-full px-3 py-2 rounded-xl text-sm text-white"
              >
                {availableSessions
                  .filter((s) => s.status === 'READY' || s.status === 'BUSY')
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} ({s.account_email || s.account_id})
                    </option>
                  ))}
              </select>
            )}

            <div className="flex justify-end gap-2 pt-2 border-t border-white/[0.08]">
              <button
                type="button"
                onClick={() => setIsSyncModalOpen(false)}
                className="mac-btn-secondary px-3.5 py-2 rounded-xl text-xs font-medium"
              >
                {t('common.close')}
              </button>
              <button
                type="button"
                disabled={syncingDrive || !syncSessionId}
                onClick={() => void handleSyncFromDrive()}
                className="mac-btn-primary px-4 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${syncingDrive ? 'animate-spin' : ''}`} />
                <span>
                  {syncingDrive ? t('snapshots.syncRunning') : t('snapshots.syncDriveButton')}
                </span>
              </button>
            </div>
          </div>
        </div>
        </ModalPortal>
      )}

      {/* Modal: New Template */}
      {isTemplateModalOpen && (
        <ModalPortal>
          <div
            className={MODAL_BACKDROP_CLASSES}
            onClick={() => setIsTemplateModalOpen(false)}
          >
            <div
              className="mac-modal-panel mac-card max-w-2xl w-full shadow-2xl overflow-hidden flex flex-col"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.08] shrink-0">
                <h3 className="text-lg font-semibold text-white">
                  {t('templates.modalCreateTemplateTitle')}
                </h3>
                <button
                  type="button"
                  onClick={() => setIsTemplateModalOpen(false)}
                  title={t('common.close')}
                  className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-white/[0.08] transition"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <form
                onSubmit={handleCreateTemplate}
                className="p-6 space-y-4 overflow-y-auto min-h-0 flex-1"
              >
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  {t('templates.tplNameLabel')}
                </label>
                <input
                  type="text"
                  required
                  placeholder={t('templates.tplNamePlaceholder')}
                  value={tplName}
                  onChange={(e) => setTplName(e.target.value)}
                  className="mac-input w-full rounded-xl px-3 py-2 text-sm text-white"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  {t('templates.tplPackagesLabel')}
                </label>
                <textarea
                  rows={2}
                  placeholder="torch torchvision torchaudio transformers accelerate datasets flash-attn"
                  value={tplPackages}
                  onChange={(e) => setTplPackages(e.target.value)}
                  className="mac-input w-full rounded-xl px-3 py-2 text-xs font-mono text-white"
                />
                <p className="text-[11px] text-slate-400 mt-1">
                  {t('templates.tplPackagesHintPrefix')}
                  <code className="text-slate-200">uv pip install --system</code>
                  {t('templates.tplPackagesHintMiddle')}
                  <code className="text-slate-200">pip</code>
                  {t('templates.tplPackagesHintSuffix')}
                </p>
              </div>

              {secrets.length > 0 && (
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1.5">
                    {t('templates.injectSecretsLabel')}
                  </label>
                  <div className="grid grid-cols-2 gap-2 max-h-32 overflow-y-auto p-3 bg-white/[0.03] border border-white/[0.08] rounded-xl">
                    {secrets.map((sec) => (
                      <label
                        key={sec.id}
                        className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={tplSelectedSecrets.includes(sec.id)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setTplSelectedSecrets([...tplSelectedSecrets, sec.id]);
                            } else {
                              setTplSelectedSecrets(
                                tplSelectedSecrets.filter((id) => id !== sec.id)
                              );
                            }
                          }}
                          className="rounded bg-slate-950 border-white/20 text-indigo-500 focus:ring-0"
                        />
                        <span className="font-mono text-emerald-300">{sec.key_name}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}

              {profiles.length > 0 && (
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1.5">
                    {t('templates.attachStorageLabel')}
                  </label>
                  <div className="grid grid-cols-2 gap-2 max-h-32 overflow-y-auto p-3 bg-white/[0.03] border border-white/[0.08] rounded-xl">
                    {profiles.map((p) => (
                      <label
                        key={p.id}
                        className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={tplSelectedProfiles.includes(p.id)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setTplSelectedProfiles([...tplSelectedProfiles, p.id]);
                            } else {
                              setTplSelectedProfiles(
                                tplSelectedProfiles.filter((id) => id !== p.id)
                              );
                            }
                          }}
                          className="rounded bg-slate-950 border-white/20 text-indigo-500 focus:ring-0"
                        />
                        <span>
                          {p.name} ({formatStorageType(p.type)})
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  {t('templates.customStartupLabel')}
                </label>
                <textarea
                  rows={4}
                  placeholder={t('templates.startupPlaceholder')}
                  value={tplStartupScript}
                  onChange={(e) => setTplStartupScript(e.target.value)}
                  className="mac-input w-full rounded-xl px-3 py-2 text-xs font-mono text-slate-200"
                />
              </div>

              <div className="pt-4 border-t border-white/[0.08] flex justify-end gap-2.5">
                <button
                  type="button"
                  onClick={() => setIsTemplateModalOpen(false)}
                  className="mac-btn-secondary px-4 py-2 rounded-xl text-sm font-medium"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  className="mac-btn-primary px-4 py-2 rounded-xl text-sm font-medium"
                >
                  {t('templates.saveTemplate')}
                </button>
              </div>
            </form>
          </div>
        </div>
        </ModalPortal>
      )}

      {/* Modal: Add Secret */}
      {isSecretModalOpen && (
        <ModalPortal>
          <div
            className={MODAL_BACKDROP_CLASSES}
            onClick={() => setIsSecretModalOpen(false)}
          >
            <div
              className="mac-modal-panel mac-card max-w-md w-full shadow-2xl overflow-hidden flex flex-col"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.08] shrink-0">
                <h3 className="text-lg font-semibold text-white">
                  {t('templates.modalAddSecretTitle')}
                </h3>
                <button
                  type="button"
                  onClick={() => setIsSecretModalOpen(false)}
                  title={t('common.close')}
                  className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-white/[0.08] transition"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <form onSubmit={handleCreateSecret} className="p-6 space-y-4 overflow-y-auto min-h-0 flex-1">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  {t('templates.secKeyNameLabel')}
                </label>
                <input
                  type="text"
                  required
                  placeholder={t('templates.secKeyNamePlaceholder')}
                  value={secKeyName}
                  onChange={(e) =>
                    setSecKeyName(e.target.value.replace(/[^a-zA-Z0-9_]/g, '').toUpperCase())
                  }
                  className="mac-input w-full rounded-xl px-3 py-2 text-sm font-mono text-emerald-300"
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-xs font-medium text-slate-300">
                    {t('templates.secValueLabel')}
                  </label>
                  <button
                    type="button"
                    onClick={() => setShowSecretValue(!showSecretValue)}
                    className="text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1"
                  >
                    {showSecretValue ? (
                      <EyeOff className="w-3.5 h-3.5" />
                    ) : (
                      <Eye className="w-3.5 h-3.5" />
                    )}
                    <span>{showSecretValue ? t('templates.hide') : t('templates.show')}</span>
                  </button>
                </div>
                <input
                  type={showSecretValue ? 'text' : 'password'}
                  required
                  placeholder={t('templates.secValuePlaceholder')}
                  value={secValue}
                  onChange={(e) => setSecValue(e.target.value)}
                  className="mac-input w-full rounded-xl px-3 py-2 text-sm font-mono text-white"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  {t('templates.secDescLabel')}
                </label>
                <input
                  type="text"
                  placeholder={t('templates.secDescPlaceholder')}
                  value={secDescription}
                  onChange={(e) => setSecDescription(e.target.value)}
                  className="mac-input w-full rounded-xl px-3 py-2 text-xs text-white"
                />
              </div>

              <div className="pt-4 border-t border-white/[0.08] flex justify-end gap-2.5">
                <button
                  type="button"
                  onClick={() => setIsSecretModalOpen(false)}
                  className="mac-btn-secondary px-4 py-2 rounded-xl text-sm font-medium"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl text-sm font-medium transition shadow-lg shadow-emerald-600/20"
                >
                  {t('templates.saveSecret')}
                </button>
              </div>
            </form>
          </div>
        </div>
        </ModalPortal>
      )}
    </div>
  );
};
