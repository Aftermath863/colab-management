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
} from 'lucide-react';
import { api } from '../services/api';
import { Template, Secret, StorageProfile, Snapshot, Session } from '../types';
import { LocalizedText, useI18n } from '../i18n';

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

export const TemplatesPage: React.FC<TemplatesPageProps> = ({
  sessions: propSessions,
  onLaunchFromSnapshot,
  initialTab = 'templates',
}) => {
  const { t, tm, formatStorageType } = useI18n();
  const [activeTab, setActiveTab] = useState<'templates' | 'secrets' | 'snapshots'>(initialTab);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [profiles, setProfiles] = useState<StorageProfile[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [availableSessions, setAvailableSessions] = useState<Session[]>(propSessions || []);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: LocalizedText } | null>(null);

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

  useEffect(() => {
    loadAll();
  }, []);

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
      setFeedback({
        type: 'error',
        msg: err.message ? { raw: err.message } : { key: 'templates.loadFailed' },
      });
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteSnapshot = async (snap: Snapshot) => {
    if (!confirm(t('snapshots.confirmDelete', { name: snap.name }))) return;
    try {
      await api.deleteSnapshot(snap.id);
      await loadAll();
    } catch (err: any) {
      setFeedback({ type: 'error', msg: { raw: err.message || 'Failed to delete snapshot' } });
    }
  };

  const handleSyncFromDrive = async (targetSessId?: string) => {
    const sessId = targetSessId || syncSessionId;
    if (!sessId) return;
    setSyncingDrive(true);
    setSyncAuthUrl(null);
    try {
      const res = await api.syncSnapshotsFromDrive(sessId);
      if (res.action_status === 'AUTH_REQUIRED' && res.redirect_url) {
        setSyncAuthUrl(res.redirect_url);
        return;
      }
      setFeedback({ type: 'success', msg: { key: 'snapshots.syncSuccess' } });
      setIsSyncModalOpen(false);
      await loadAll();
    } catch (err: any) {
      setFeedback({ type: 'error', msg: { raw: err.message || 'Sync failed' } });
    } finally {
      setSyncingDrive(false);
    }
  };

  const handleCreateTemplate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tplName.trim()) return;

    const pkgs = tplPackages
      .split(/[,\s\n]+/)
      .map((p) => p.trim())
      .filter(Boolean);

    const createdName = tplName.trim();
    try {
      await api.createTemplate({
        name: createdName,
        packages: pkgs,
        secret_ids: tplSelectedSecrets,
        storage_profile_ids: tplSelectedProfiles,
        startup_script: tplStartupScript,
      });
      setFeedback({
        type: 'success',
        msg: { key: 'templates.templateCreated', params: { name: createdName } },
      });
      setIsTemplateModalOpen(false);
      resetTemplateForm();
      loadAll();
    } catch (err: any) {
      setFeedback({
        type: 'error',
        msg: err.message ? { raw: err.message } : { key: 'templates.templateCreateFailed' },
      });
    }
  };

  const resetTemplateForm = () => {
    setTplName('');
    setTplPackages('');
    setTplSelectedSecrets([]);
    setTplSelectedProfiles([]);
    setTplStartupScript('');
  };

  const handleDeleteTemplate = async (id: string, name: string) => {
    if (!confirm(t('templates.confirmDeleteTemplate', { name }))) return;
    try {
      await api.deleteTemplate(id);
      setFeedback({
        type: 'success',
        msg: { key: 'templates.templateDeleted', params: { name } },
      });
      loadAll();
    } catch (err: any) {
      setFeedback({
        type: 'error',
        msg: err.message ? { raw: err.message } : { key: 'templates.templateDeleteFailed' },
      });
    }
  };

  const handleCreateSecret = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!secKeyName.trim() || !secValue.trim()) return;

    const normalizedName = secKeyName.trim().toUpperCase();
    try {
      await api.createSecret({
        key_name: normalizedName,
        value: secValue.trim(),
        description: secDescription.trim(),
      });
      setFeedback({
        type: 'success',
        msg: { key: 'templates.secretCreated', params: { name: normalizedName } },
      });
      setIsSecretModalOpen(false);
      resetSecretForm();
      loadAll();
    } catch (err: any) {
      setFeedback({
        type: 'error',
        msg: err.message ? { raw: err.message } : { key: 'templates.secretCreateFailed' },
      });
    }
  };

  const resetSecretForm = () => {
    setSecKeyName('');
    setSecValue('');
    setSecDescription('');
    setShowSecretValue(false);
  };

  const handleDeleteSecret = async (id: string, keyName: string) => {
    if (!confirm(t('templates.confirmDeleteSecret', { name: keyName }))) return;
    try {
      await api.deleteSecret(id);
      setFeedback({
        type: 'success',
        msg: { key: 'templates.secretDeleted', params: { name: keyName } },
      });
      loadAll();
    } catch (err: any) {
      setFeedback({
        type: 'error',
        msg: err.message ? { raw: err.message } : { key: 'templates.secretDeleteFailed' },
      });
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            <Layers className="w-7 h-7 text-indigo-400" />
            {t('templates.title')}
          </h1>
          <p className="text-sm text-slate-400 mt-1">{t('templates.subtitle')}</p>
        </div>

        {/* Action Button */}
        <div className="flex gap-2">
          {activeTab === 'templates' ? (
            <button
              type="button"
              onClick={() => setIsTemplateModalOpen(true)}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2 shadow-sm"
            >
              <Plus className="w-4 h-4" />
              {t('templates.newTemplate')}
            </button>
          ) : activeTab === 'secrets' ? (
            <button
              type="button"
              onClick={() => setIsSecretModalOpen(true)}
              className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2 shadow-sm"
            >
              <Key className="w-4 h-4" />
              {t('templates.addSecret')}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => {
                const readySessions = availableSessions.filter(
                  (s) => s.status === 'READY' || s.status === 'BUSY',
                );
                if (readySessions.length > 0 && !syncSessionId) {
                  setSyncSessionId(readySessions[0].id);
                }
                setSyncAuthUrl(null);
                setIsSyncModalOpen(true);
              }}
              className="px-4 py-2 bg-sky-600 hover:bg-sky-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2 shadow-sm"
            >
              <RefreshCw className="w-4 h-4" />
              {t('snapshots.syncDriveButton')}
            </button>
          )}
        </div>
      </div>

      {/* Feedback Banner */}
      {feedback && (
        <div
          className={`p-4 rounded-lg text-sm flex items-center justify-between ${
            feedback.type === 'success'
              ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
              : 'bg-rose-500/10 border border-rose-500/20 text-rose-400'
          }`}
        >
          <div className="flex items-center gap-2">
            {feedback.type === 'success' ? (
              <CheckCircle2 className="w-4 h-4 shrink-0" />
            ) : (
              <AlertCircle className="w-4 h-4 shrink-0" />
            )}
            <span>{tm(feedback.msg)}</span>
          </div>
          <button
            type="button"
            onClick={() => setFeedback(null)}
            title={t('common.close')}
            className="p-1 hover:bg-slate-800 rounded"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-slate-800">
        <button
          type="button"
          onClick={() => setActiveTab('templates')}
          className={`py-3 px-6 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === 'templates'
              ? 'border-indigo-500 text-indigo-400'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          <Layers className="w-4 h-4" />
          {t('templates.tabTemplates', { count: templates.length })}
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('secrets')}
          className={`py-3 px-6 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === 'secrets'
              ? 'border-emerald-500 text-emerald-400'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          <Key className="w-4 h-4" />
          {t('templates.tabSecrets', { count: secrets.length })}
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('snapshots')}
          className={`py-3 px-6 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === 'snapshots'
              ? 'border-sky-500 text-sky-400'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          <Camera className="w-4 h-4" />
          {t('snapshots.tabTitle')} ({snapshots.length})
        </button>
      </div>

      {/* Tab 1: Templates Grid */}
      {activeTab === 'templates' && (
        <div>
          {loading && templates.length === 0 ? (
            <div className="py-12 text-center text-slate-400">{t('templates.loadingTemplates')}</div>
          ) : templates.length === 0 ? (
            <div className="py-16 text-center border border-dashed border-slate-800 rounded-xl bg-slate-900/40">
              <Layers className="w-10 h-10 text-slate-600 mx-auto mb-3" />
              <h3 className="text-base font-semibold text-white">{t('templates.emptyTemplatesTitle')}</h3>
              <p className="text-sm text-slate-400 mt-1 max-w-md mx-auto">
                {t('templates.emptyTemplatesDesc')}
              </p>
              <button
                type="button"
                onClick={() => setIsTemplateModalOpen(true)}
                className="mt-4 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors inline-flex items-center gap-2"
              >
                <Plus className="w-4 h-4" />
                {t('templates.createFirstTemplate')}
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {templates.map((tpl) => (
                <div
                  key={tpl.id}
                  className="bg-slate-900 border border-slate-800 rounded-xl p-5 hover:border-slate-700 transition-all flex flex-col justify-between shadow-sm"
                >
                  <div className="space-y-4">
                    <div className="flex items-start justify-between">
                      <div>
                        <h3 className="font-semibold text-white text-base">{tpl.name}</h3>
                        <span className="text-[11px] text-slate-500 font-mono">ID: {tpl.id}</span>
                      </div>
                      <button
                        type="button"
                        onClick={() => handleDeleteTemplate(tpl.id, tpl.name)}
                        className="text-slate-500 hover:text-rose-400 transition-colors p-1 rounded hover:bg-slate-800"
                        title={t('templates.deleteTemplateTooltip')}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>

                    {/* Packages */}
                    <div>
                      <div className="text-xs text-slate-400 font-medium mb-1.5 flex items-center gap-1.5">
                        <Package className="w-3.5 h-3.5 text-indigo-400" />
                        {t('templates.pipPackagesCount', { count: tpl.packages.length })}
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {tpl.packages.length > 0 ? (
                          tpl.packages.map((pkg, i) => (
                            <span
                              key={i}
                              className="px-2 py-0.5 bg-slate-800 border border-slate-700 text-indigo-300 rounded text-[11px] font-mono"
                            >
                              {pkg}
                            </span>
                          ))
                        ) : (
                          <span className="text-xs text-slate-600 italic">{t('templates.noneSpecified')}</span>
                        )}
                      </div>
                    </div>

                    {/* Secrets & Storage */}
                    <div className="text-xs text-slate-400 space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="flex items-center gap-1">
                          <Key className="w-3 h-3 text-emerald-400" /> {t('templates.attachedSecrets')}
                        </span>
                        <span className="font-mono text-slate-300">{tpl.secret_ids.length}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span>{t('templates.attachedStorage')}</span>
                        <span className="font-mono text-slate-300">{tpl.storage_profile_ids.length}</span>
                      </div>
                    </div>

                    {/* Startup Script Snippet */}
                    {tpl.startup_script && (
                      <div>
                        <div className="text-xs text-slate-400 font-medium mb-1 flex items-center gap-1">
                          <FileCode className="w-3 h-3 text-amber-400" /> {t('templates.startupScript')}
                        </div>
                        <pre className="bg-slate-950 border border-slate-800 rounded p-2 text-[10px] font-mono text-slate-300 max-h-20 overflow-hidden line-clamp-3">
                          {tpl.startup_script}
                        </pre>
                      </div>
                    )}
                  </div>

                  <div className="pt-4 border-t border-slate-800/60 mt-4 flex items-center justify-between text-[11px] text-slate-500">
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
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
          <div className="p-4 border-b border-slate-800 bg-slate-950/40 flex items-center justify-between">
            <p className="text-xs text-slate-400">{t('templates.secretsVaultBanner')}</p>
          </div>

          {loading && secrets.length === 0 ? (
            <div className="py-12 text-center text-slate-400">{t('templates.loadingSecrets')}</div>
          ) : secrets.length === 0 ? (
            <div className="py-16 text-center text-slate-500">
              <Key className="w-8 h-8 text-slate-600 mx-auto mb-2" />
              {t('templates.emptySecrets')}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-950/60 text-slate-400 text-xs uppercase tracking-wider border-b border-slate-800">
                  <tr>
                    <th className="py-3 px-6">{t('templates.colKeyName')}</th>
                    <th className="py-3 px-6">{t('templates.colDescription')}</th>
                    <th className="py-3 px-6">{t('templates.colStoredValue')}</th>
                    <th className="py-3 px-6">{t('templates.colCreated')}</th>
                    <th className="py-3 px-6 text-right">{t('templates.colActions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800 text-slate-300">
                  {secrets.map((sec) => (
                    <tr key={sec.id} className="hover:bg-slate-800/40 transition-colors">
                      <td className="py-3.5 px-6 font-mono text-emerald-400 font-semibold">{sec.key_name}</td>
                      <td className="py-3.5 px-6 text-slate-400 text-xs">{sec.description || '—'}</td>
                      <td className="py-3.5 px-6 font-mono text-xs text-slate-500">••••••••••••••••••••••••</td>
                      <td className="py-3.5 px-6 text-xs text-slate-500">{sec.created_at.split('T')[0]}</td>
                      <td className="py-3.5 px-6 text-right">
                        <button
                          type="button"
                          onClick={() => handleDeleteSecret(sec.id, sec.key_name)}
                          className="text-slate-500 hover:text-rose-400 transition-colors p-1 rounded hover:bg-slate-800"
                          title={t('templates.deleteSecretTooltip')}
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
            <div className="py-16 text-center border border-dashed border-slate-800 rounded-xl bg-slate-900/40">
              <Camera className="w-10 h-10 text-slate-600 mx-auto mb-3" />
              <p className="text-sm text-slate-400 max-w-lg mx-auto">{t('snapshots.noSnapshots')}</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {snapshots.map((snap) => (
                <div
                  key={snap.id}
                  className="bg-slate-900 border border-slate-800 rounded-xl p-5 flex flex-col justify-between space-y-4"
                >
                  <div className="space-y-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <h3 className="text-base font-semibold text-white">{snap.name}</h3>
                        {snap.description && (
                          <p className="text-xs text-slate-400 mt-0.5 line-clamp-2">{snap.description}</p>
                        )}
                      </div>
                      <span
                        className={`px-2 py-0.5 text-[11px] font-semibold rounded ${
                          snap.status === 'READY'
                            ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                            : snap.status === 'FAILED'
                              ? 'bg-rose-950 text-rose-400 border border-rose-800'
                              : 'bg-amber-950 text-amber-400 border border-amber-800'
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
                      <span className="px-2 py-0.5 rounded bg-indigo-950/70 text-indigo-300 border border-indigo-800/60">
                        {snap.capture_mode === 'full_binary'
                          ? t('snapshots.modeFullBinary')
                          : t('snapshots.modeHybrid')}
                      </span>
                      {snap.python_version && (
                        <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-mono">
                          Py {snap.python_version}
                        </span>
                      )}
                      <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-mono">
                        {snap.accelerator}
                      </span>
                    </div>

                    <div className="text-xs text-slate-400 space-y-1 pt-1">
                      <div>
                        {t('snapshots.packagesSummary', {
                          apt: snap.apt_package_count,
                          pip: snap.pip_package_count,
                        })}{' '}
                        · {t('snapshots.workspaceSize', { size: formatSnapSize(snap.workspace_size_bytes) })}
                        {snap.pylibs_size_bytes > 0 &&
                          ` · ${t('snapshots.pylibsSize', { size: formatSnapSize(snap.pylibs_size_bytes) })}`}
                      </div>
                      <div className="truncate font-mono text-[11px] text-slate-500">
                        {t('snapshots.ownerAccountLabel')}: {snap.account_email || snap.account_id}
                      </div>
                      <div className="truncate font-mono text-[11px] text-slate-500">
                        {t('snapshots.drivePathLabel')}: {snap.drive_folder_path}
                      </div>
                    </div>
                  </div>

                  <div className="pt-3 border-t border-slate-800 flex items-center justify-between gap-2">
                    <button
                      type="button"
                      disabled={snap.status !== 'READY'}
                      onClick={() => onLaunchFromSnapshot?.(snap)}
                      className="flex-1 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold flex items-center justify-center gap-1.5 disabled:opacity-40 transition-colors"
                    >
                      <Play className="w-3.5 h-3.5 fill-current" />
                      <span>{t('snapshots.launchWithSnapshot')}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDeleteSnapshot(snap)}
                      title={t('snapshots.deleteSnapshot')}
                      className="p-2 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-slate-800 transition-colors"
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

      {/* Modal: Sync Snapshots from Google Drive */}
      {isSyncModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-md w-full p-5 space-y-4 shadow-2xl">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-white">{t('snapshots.syncSelectSessionTitle')}</h3>
              <button
                type="button"
                onClick={() => setIsSyncModalOpen(false)}
                className="text-slate-400 hover:text-white p-1"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <p className="text-xs text-slate-400">{t('snapshots.syncSelectSessionDesc')}</p>

            {syncAuthUrl && (
              <div className="p-3 rounded-lg bg-amber-950/40 border border-amber-500/40 space-y-2">
                <p className="text-xs text-amber-200">{t('snapshots.authRequiredDesc')}</p>
                <a
                  href={syncAuthUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  <span>{t('snapshots.openAuthPopup')}</span>
                </a>
              </div>
            )}

            {availableSessions.filter((s) => s.status === 'READY' || s.status === 'BUSY').length === 0 ? (
              <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 text-xs text-slate-400">
                {t('snapshots.syncNoActiveSession')}
              </div>
            ) : (
              <select
                value={syncSessionId}
                onChange={(e) => setSyncSessionId(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-sm text-white focus:border-sky-500 focus:outline-none"
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

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setIsSyncModalOpen(false)}
                className="px-3 py-1.5 rounded-lg text-xs text-slate-300 hover:bg-slate-800"
              >
                {t('common.close')}
              </button>
              <button
                type="button"
                disabled={syncingDrive || !syncSessionId}
                onClick={() => handleSyncFromDrive()}
                className="px-3.5 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${syncingDrive ? 'animate-spin' : ''}`} />
                <span>{syncingDrive ? t('snapshots.syncRunning') : t('snapshots.syncDriveButton')}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal: New Template */}
      {isTemplateModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-2xl w-full shadow-2xl overflow-hidden flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
              <h3 className="text-lg font-semibold text-white">{t('templates.modalCreateTemplateTitle')}</h3>
              <button
                type="button"
                onClick={() => setIsTemplateModalOpen(false)}
                title={t('common.close')}
                className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateTemplate} className="p-6 space-y-4 overflow-y-auto max-h-[75vh]">
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
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500"
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
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-indigo-500"
                />
                <p className="text-[11px] text-slate-500 mt-1">
                  {t('templates.tplPackagesHintPrefix')}
                  <code className="text-slate-400">uv pip install --system</code>
                  {t('templates.tplPackagesHintMiddle')}
                  <code className="text-slate-400">pip</code>
                  {t('templates.tplPackagesHintSuffix')}
                </p>
              </div>

              {/* Secrets Checkboxes */}
              {secrets.length > 0 && (
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1.5">
                    {t('templates.injectSecretsLabel')}
                  </label>
                  <div className="grid grid-cols-2 gap-2 max-h-32 overflow-y-auto p-2 bg-slate-950 border border-slate-800 rounded-lg">
                    {secrets.map((sec) => (
                      <label key={sec.id} className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={tplSelectedSecrets.includes(sec.id)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setTplSelectedSecrets([...tplSelectedSecrets, sec.id]);
                            } else {
                              setTplSelectedSecrets(tplSelectedSecrets.filter((id) => id !== sec.id));
                            }
                          }}
                          className="rounded bg-slate-900 border-slate-700 text-indigo-600 focus:ring-0"
                        />
                        <span className="font-mono text-emerald-400">{sec.key_name}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}

              {/* Storage Profiles Checkboxes */}
              {profiles.length > 0 && (
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1.5">
                    {t('templates.attachStorageLabel')}
                  </label>
                  <div className="grid grid-cols-2 gap-2 max-h-32 overflow-y-auto p-2 bg-slate-950 border border-slate-800 rounded-lg">
                    {profiles.map((p) => (
                      <label key={p.id} className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={tplSelectedProfiles.includes(p.id)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setTplSelectedProfiles([...tplSelectedProfiles, p.id]);
                            } else {
                              setTplSelectedProfiles(tplSelectedProfiles.filter((id) => id !== p.id));
                            }
                          }}
                          className="rounded bg-slate-900 border-slate-700 text-indigo-600 focus:ring-0"
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
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-slate-300 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div className="pt-4 border-t border-slate-800 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setIsTemplateModalOpen(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-sm font-medium transition-colors"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors"
                >
                  {t('templates.saveTemplate')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal: Add Secret */}
      {isSecretModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-md w-full shadow-2xl overflow-hidden flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
              <h3 className="text-lg font-semibold text-white">{t('templates.modalAddSecretTitle')}</h3>
              <button
                type="button"
                onClick={() => setIsSecretModalOpen(false)}
                title={t('common.close')}
                className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateSecret} className="p-6 space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  {t('templates.secKeyNameLabel')}
                </label>
                <input
                  type="text"
                  required
                  placeholder={t('templates.secKeyNamePlaceholder')}
                  value={secKeyName}
                  onChange={(e) => setSecKeyName(e.target.value.replace(/[^a-zA-Z0-9_]/g, '').toUpperCase())}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-emerald-400 focus:outline-none focus:border-emerald-500"
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
                    {showSecretValue ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    {showSecretValue ? t('templates.hide') : t('templates.show')}
                  </button>
                </div>
                <input
                  type={showSecretValue ? 'text' : 'password'}
                  required
                  placeholder={t('templates.secValuePlaceholder')}
                  value={secValue}
                  onChange={(e) => setSecValue(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-white focus:outline-none focus:border-emerald-500"
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
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-emerald-500"
                />
              </div>

              <div className="pt-4 border-t border-slate-800 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setIsSecretModalOpen(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-sm font-medium transition-colors"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium transition-colors"
                >
                  {t('templates.saveSecret')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
