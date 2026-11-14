import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Cloud, HardDrive, Database, CheckCircle2, AlertCircle, X, ExternalLink, RefreshCw } from 'lucide-react';
import { api } from '../services/api';
import { StorageProfile } from '../types';
import { LocalizedText, useI18n } from '../i18n';
import { openExternalUrl, triggerHaptic } from '../desktop/macBridge';
import { useToast } from '../desktop/ToastContext';

interface StorageWizardModalProps {
  sessionId: string;
  isOpen: boolean;
  onClose: () => void;
  onStorageMounted?: () => void;
}

export const StorageWizardModal: React.FC<StorageWizardModalProps> = ({
  sessionId,
  isOpen,
  onClose,
  onStorageMounted,
}) => {
  const { t, tm } = useI18n();
  const toast = useToast();

  const [activeTab, setActiveTab] = useState<'gdrive' | 's3' | 'hf'>('gdrive');
  const [profiles, setProfiles] = useState<StorageProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string>('');

  // Status & notifications
  const [loading, setLoading] = useState(false);
  const [awaitingConsent, setAwaitingConsent] = useState(false);
  const popupRef = useRef<Window | null>(null);
  const [statusMsg, setStatusMsg] = useState<{
    type: 'success' | 'error' | 'info';
    msg: LocalizedText;
    link?: string;
  } | null>(null);

  // Form states for S3
  const [s3Name, setS3Name] = useState('');
  const [s3Provider, setS3Provider] = useState('AWS');
  const [s3Endpoint, setS3Endpoint] = useState('');
  const [s3AccessKey, setS3AccessKey] = useState('');
  const [s3SecretKey, setS3SecretKey] = useState('');
  const [s3MountPath, setS3MountPath] = useState('/content/data');

  // Form states for HF
  const [hfName, setHfName] = useState('');
  const [hfToken, setHfToken] = useState('');
  const [hfMountPath, setHfMountPath] = useState('/content/hf_models');

  useEffect(() => {
    if (isOpen) {
      loadProfiles();
      setStatusMsg(null);
      setAwaitingConsent(false);
      popupRef.current = null;
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen || typeof window === 'undefined') return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !loading) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [isOpen, loading, onClose]);

  const loadProfiles = async () => {
    try {
      const data = await api.getStorageProfiles();
      setProfiles(data);
    } catch (err: any) {
      console.error('Failed to load storage profiles', err);
    }
  };

  const openConsentPopup = (url: string) => {
    if (typeof window === 'undefined') return;
    const popup = typeof window.open === 'function'
      ? window.open(url, 'colab_gdrive_consent', 'width=580,height=700,noopener=no')
      : null;
    if (popup) {
      popupRef.current = popup;
    } else {
      openExternalUrl(url);
    }
  };

  const handleGDrivePropagate = useCallback(
    async (dryrun = false, postConsent = false) => {
      setLoading(true);
      setAwaitingConsent(false);
      setStatusMsg({
        type: 'info',
        msg: { key: postConsent ? 'storageModal.gdriveCompletingMount' : 'storageModal.gdrivePropagating' },
      });
      try {
        const res = await api.propagateGDrive(sessionId, dryrun);
        if (res.status === 'AUTH_REQUIRED' && res.redirect_url) {
          setStatusMsg({
            type: 'info',
            msg: { key: 'storageModal.gdriveAuthRequired' },
            link: res.redirect_url,
          });
          setAwaitingConsent(true);
          if (!postConsent) {
            openConsentPopup(res.redirect_url);
          }
        } else {
          popupRef.current = null;
          setStatusMsg({
            type: 'success',
            msg: { key: 'storageModal.gdriveSuccess' },
          });
          toast.pushToast({
            variant: 'success',
            title: t('storageModal.gdriveSuccess'),
          });
          if (onStorageMounted) onStorageMounted();
        }
      } catch (err: any) {
        popupRef.current = null;
        triggerHaptic('medium');
        setStatusMsg({
          type: 'error',
          msg: err.message ? { raw: err.message } : { key: 'storageModal.gdriveFailed' },
        });
      } finally {
        setLoading(false);
      }
    },
    [sessionId, onStorageMounted, t, toast]
  );

  // Automatically finalize credential propagation once the user closes the Google consent popup
  useEffect(() => {
    if (!isOpen || !awaitingConsent || loading) return;
    const timer = window.setInterval(() => {
      if (popupRef.current && popupRef.current.closed) {
        popupRef.current = null;
        setAwaitingConsent(false);
        void handleGDrivePropagate(false, true);
      }
    }, 900);
    return () => window.clearInterval(timer);
  }, [isOpen, awaitingConsent, loading, handleGDrivePropagate]);

  const handleMountExistingProfile = async () => {
    if (!selectedProfileId) return;
    setLoading(true);
    setStatusMsg({ type: 'info', msg: { key: 'storageModal.mountConfiguring' } });
    try {
      const res = await api.mountStorage(sessionId, selectedProfileId);
      if (res.mount_verified) {
        setStatusMsg({
          type: 'success',
          msg: { key: 'storageModal.mountVerified', params: { path: res.mount_path } },
        });
        toast.pushToast({
          variant: 'success',
          title: t('storageModal.mountVerified', { path: res.mount_path }),
        });
        if (onStorageMounted) onStorageMounted();
      } else {
        setStatusMsg({
          type: 'info',
          msg: { key: 'storageModal.mountPending', params: { path: res.mount_path } },
        });
      }
    } catch (err: any) {
      triggerHaptic('medium');
      setStatusMsg({
        type: 'error',
        msg: err.message ? { raw: err.message } : { key: 'storageModal.mountFailed' },
      });
    } finally {
      setLoading(false);
    }
  };

  const handleCreateAndMountS3 = async () => {
    if (!s3Name.trim() || !s3AccessKey.trim() || !s3SecretKey.trim()) {
      setStatusMsg({ type: 'error', msg: { key: 'storageModal.s3MissingFields' } });
      return;
    }
    setLoading(true);
    setStatusMsg({ type: 'info', msg: { key: 'storageModal.s3Creating' } });
    try {
      const profile = await api.createStorageProfile({
        name: s3Name.trim(),
        type: 's3',
        mount_path: s3MountPath.trim() || '/content/data',
        config: {
          provider: s3Provider,
          access_key_id: s3AccessKey.trim(),
          secret_access_key: s3SecretKey.trim(),
          endpoint: s3Endpoint.trim(),
        },
      });
      await api.mountStorage(sessionId, profile.id);
      setStatusMsg({
        type: 'success',
        msg: { key: 'storageModal.s3Success', params: { name: profile.name, path: profile.mount_path } },
      });
      toast.pushToast({
        variant: 'success',
        title: t('storageModal.s3Success', { name: profile.name, path: profile.mount_path }),
      });
      loadProfiles();
      if (onStorageMounted) onStorageMounted();
    } catch (err: any) {
      triggerHaptic('medium');
      setStatusMsg({
        type: 'error',
        msg: err.message ? { raw: err.message } : { key: 'storageModal.s3Failed' },
      });
    } finally {
      setLoading(false);
    }
  };

  const handleCreateAndMountHF = async () => {
    if (!hfName.trim() || !hfToken.trim()) {
      setStatusMsg({ type: 'error', msg: { key: 'storageModal.hfMissingFields' } });
      return;
    }
    setLoading(true);
    setStatusMsg({ type: 'info', msg: { key: 'storageModal.hfCreating' } });
    try {
      const profile = await api.createStorageProfile({
        name: hfName.trim(),
        type: 'hf',
        mount_path: hfMountPath.trim() || '/content/hf_models',
        config: {
          token: hfToken.trim(),
        },
      });
      await api.mountStorage(sessionId, profile.id);
      setStatusMsg({
        type: 'success',
        msg: { key: 'storageModal.hfSuccess', params: { path: profile.mount_path } },
      });
      toast.pushToast({
        variant: 'success',
        title: t('storageModal.hfSuccess', { path: profile.mount_path }),
      });
      loadProfiles();
      if (onStorageMounted) onStorageMounted();
    } catch (err: any) {
      triggerHaptic('medium');
      setStatusMsg({
        type: 'error',
        msg: err.message ? { raw: err.message } : { key: 'storageModal.hfFailed' },
      });
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 mac-modal-backdrop flex items-center justify-center p-4"
      onClick={() => {
        if (!loading) onClose();
      }}
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
              <Cloud className="w-4 h-4" />
            </span>
            <h2 className="text-base sm:text-lg font-semibold text-white">{t('storageModal.title')}</h2>
          </div>
          <div className="flex items-center gap-2">
            <kbd className="mac-kbd hidden sm:inline-flex">Esc</kbd>
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              title={t('common.close')}
              className="text-slate-400 hover:text-white transition-colors p-1.5 rounded-lg hover:bg-white/[0.08]"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Segmented Tab Navigation */}
        <div className="px-6 py-3 border-b border-white/[0.07] bg-black/20">
          <div className="mac-pill-group gap-1">
            <button
              type="button"
              onClick={() => {
                triggerHaptic('light');
                setActiveTab('gdrive');
                setStatusMsg(null);
              }}
              className={`py-1.5 px-3.5 rounded-lg text-xs font-medium transition-all flex items-center gap-2 ${
                activeTab === 'gdrive'
                  ? 'mac-pill-active text-white'
                  : 'text-slate-400 hover:text-slate-200 border border-transparent'
              }`}
            >
              <HardDrive className="w-3.5 h-3.5" />
              {t('storageModal.tabGdrive')}
            </button>
            <button
              type="button"
              onClick={() => {
                triggerHaptic('light');
                setActiveTab('s3');
                setStatusMsg(null);
              }}
              className={`py-1.5 px-3.5 rounded-lg text-xs font-medium transition-all flex items-center gap-2 ${
                activeTab === 's3'
                  ? 'mac-pill-active text-white'
                  : 'text-slate-400 hover:text-slate-200 border border-transparent'
              }`}
            >
              <Database className="w-3.5 h-3.5" />
              {t('storageModal.tabS3')}
            </button>
            <button
              type="button"
              onClick={() => {
                triggerHaptic('light');
                setActiveTab('hf');
                setStatusMsg(null);
              }}
              className={`py-1.5 px-3.5 rounded-lg text-xs font-medium transition-all flex items-center gap-2 ${
                activeTab === 'hf'
                  ? 'mac-pill-active text-white'
                  : 'text-slate-400 hover:text-slate-200 border border-transparent'
              }`}
            >
              <Cloud className="w-3.5 h-3.5 text-amber-400" />
              {t('storageModal.tabHf')}
            </button>
          </div>
        </div>

        {/* Tab Content */}
        <div className="p-6 space-y-6 overflow-y-auto max-h-[70vh]">
          {/* Status Alert */}
          {statusMsg && (
            <div
              className={`p-4 rounded-xl text-sm flex items-start gap-3 ${
                statusMsg.type === 'success'
                  ? 'bg-emerald-500/10 border border-emerald-400/25 text-emerald-300'
                  : statusMsg.type === 'error'
                    ? 'bg-rose-500/10 border border-rose-400/25 text-rose-300'
                    : 'bg-indigo-500/10 border border-indigo-400/25 text-indigo-200'
              }`}
            >
              {statusMsg.type === 'success' ? (
                <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" />
              ) : statusMsg.type === 'error' ? (
                <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
              ) : statusMsg.link && !loading ? (
                <ExternalLink className="w-5 h-5 shrink-0 mt-0.5" />
              ) : (
                <RefreshCw className="w-5 h-5 shrink-0 mt-0.5 animate-spin" />
              )}
              <div className="space-y-3 flex-1">
                <p>{tm(statusMsg.msg)}</p>
                {statusMsg.link && (
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => openConsentPopup(statusMsg.link!)}
                      className="mac-btn-primary px-3 py-1.5 rounded-lg text-xs font-medium gap-1.5"
                    >
                      {t('storageModal.grantConsent')} <ExternalLink className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      disabled={loading}
                      onClick={() => void handleGDrivePropagate(false, true)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 active:scale-[0.97] disabled:opacity-50 text-white rounded-lg text-xs font-medium transition-all"
                    >
                      <CheckCircle2 className="w-3.5 h-3.5" /> {t('storageModal.confirmConsentAndMount')}
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Tab 1: Google Drive */}
          {activeTab === 'gdrive' && (
            <div className="space-y-4">
              <div className="p-4 bg-white/[0.03] border border-white/[0.08] rounded-xl text-xs text-slate-300 space-y-2">
                <strong className="text-white block font-medium">{t('storageModal.gdriveInfoTitle')}</strong>
                <p>
                  {t('storageModal.gdriveInfoDesc1Prefix')}
                  <code className="text-indigo-300 font-mono">/content/drive/MyDrive</code>.
                </p>
                <p className="text-slate-400">{t('storageModal.gdriveInfoDesc2')}</p>
              </div>

              <div className="pt-2">
                <button
                  type="button"
                  onClick={() => handleGDrivePropagate(false)}
                  disabled={loading}
                  className="w-full mac-btn-primary py-2.5 px-4 disabled:opacity-50 rounded-xl text-sm font-semibold gap-2"
                >
                  {loading && <RefreshCw className="w-4 h-4 animate-spin" />}
                  {t('storageModal.gdriveMountBtn')}
                </button>
              </div>
            </div>
          )}

          {/* Tab 2: S3 / Cloudflare R2 */}
          {activeTab === 's3' && (
            <div className="space-y-4">
              {profiles.filter((p) => p.type === 's3').length > 0 && (
                <div className="p-3.5 bg-white/[0.03] border border-white/[0.08] rounded-xl space-y-2">
                  <label className="block text-xs font-medium text-slate-400">
                    {t('storageModal.selectExistingS3')}
                  </label>
                  <div className="flex gap-2">
                    <select
                      value={selectedProfileId}
                      onChange={(e) => setSelectedProfileId(e.target.value)}
                      className="flex-1 mac-input rounded-xl px-3 py-2 text-xs"
                    >
                      <option value="" className="bg-slate-900 text-white">
                        {t('storageModal.chooseProfile')}
                      </option>
                      {profiles
                        .filter((p) => p.type === 's3')
                        .map((p) => (
                          <option key={p.id} value={p.id} className="bg-slate-900 text-white">
                            {p.name} ({p.mount_path})
                          </option>
                        ))}
                    </select>
                    <button
                      type="button"
                      onClick={handleMountExistingProfile}
                      disabled={loading || !selectedProfileId}
                      className="mac-btn-primary px-4 py-2 disabled:opacity-50 text-xs font-medium rounded-xl"
                    >
                      {t('storageModal.mountProfileBtn')}
                    </button>
                  </div>
                </div>
              )}

              <div className="border-t border-white/[0.07] pt-4 space-y-3">
                <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                  {t('storageModal.createNewS3Heading')}
                </h4>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">{t('storageModal.profileName')}</label>
                    <input
                      type="text"
                      placeholder={t('storageModal.s3NamePlaceholder')}
                      value={s3Name}
                      onChange={(e) => setS3Name(e.target.value)}
                      className="w-full mac-input rounded-xl px-3 py-2 text-xs"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">{t('storageModal.provider')}</label>
                    <select
                      value={s3Provider}
                      onChange={(e) => setS3Provider(e.target.value)}
                      className="w-full mac-input rounded-xl px-3 py-2 text-xs"
                    >
                      <option value="AWS" className="bg-slate-900 text-white">
                        {t('storageModal.providerAws')}
                      </option>
                      <option value="Cloudflare" className="bg-slate-900 text-white">
                        {t('storageModal.providerCloudflare')}
                      </option>
                      <option value="MinIO" className="bg-slate-900 text-white">
                        {t('storageModal.providerMinio')}
                      </option>
                    </select>
                  </div>
                </div>

                <div>
                  <label className="block text-xs text-slate-400 mb-1">{t('storageModal.endpointLabel')}</label>
                  <input
                    type="text"
                    placeholder="https://<accountid>.r2.cloudflarestorage.com"
                    value={s3Endpoint}
                    onChange={(e) => setS3Endpoint(e.target.value)}
                    className="w-full mac-input rounded-xl px-3 py-2 text-xs"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">{t('storageModal.accessKeyLabel')}</label>
                    <input
                      type="text"
                      placeholder="AKIA..."
                      value={s3AccessKey}
                      onChange={(e) => setS3AccessKey(e.target.value)}
                      className="w-full mac-input rounded-xl px-3 py-2 text-xs"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">{t('storageModal.secretKeyLabel')}</label>
                    <input
                      type="password"
                      placeholder="••••••••••••"
                      value={s3SecretKey}
                      onChange={(e) => setS3SecretKey(e.target.value)}
                      className="w-full mac-input rounded-xl px-3 py-2 text-xs"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-xs text-slate-400 mb-1">{t('storageModal.remoteMountLabel')}</label>
                  <input
                    type="text"
                    placeholder="/content/data"
                    value={s3MountPath}
                    onChange={(e) => setS3MountPath(e.target.value)}
                    className="w-full mac-input rounded-xl px-3 py-2 text-xs"
                  />
                </div>

                <div className="pt-2">
                  <button
                    type="button"
                    onClick={handleCreateAndMountS3}
                    disabled={loading}
                    className="w-full mac-btn-primary py-2.5 px-4 disabled:opacity-50 rounded-xl text-xs font-semibold"
                  >
                    {t('storageModal.saveAndMountS3')}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Tab 3: Hugging Face */}
          {activeTab === 'hf' && (
            <div className="space-y-4">
              <div className="p-4 bg-white/[0.03] border border-white/[0.08] rounded-xl text-xs text-slate-300 space-y-2">
                <strong className="text-white block font-medium">{t('storageModal.hfInfoTitle')}</strong>
                <p>
                  {t('storageModal.hfInfoDescPrefix')}
                  <code className="text-amber-300 font-mono">/root/.cache/huggingface/token</code>
                  {t('storageModal.hfInfoDescSuffix')}
                </p>
              </div>

              <div className="space-y-3">
                <div>
                  <label className="block text-xs text-slate-400 mb-1">{t('storageModal.profileName')}</label>
                  <input
                    type="text"
                    placeholder={t('storageModal.hfNamePlaceholder')}
                    value={hfName}
                    onChange={(e) => setHfName(e.target.value)}
                    className="w-full mac-input rounded-xl px-3 py-2 text-xs"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">{t('storageModal.hfTokenLabel')}</label>
                  <input
                    type="password"
                    placeholder="hf_••••••••••••••••••••••••"
                    value={hfToken}
                    onChange={(e) => setHfToken(e.target.value)}
                    className="w-full mac-input rounded-xl px-3 py-2 text-xs"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">{t('storageModal.hfCachePathLabel')}</label>
                  <input
                    type="text"
                    value={hfMountPath}
                    onChange={(e) => setHfMountPath(e.target.value)}
                    className="w-full mac-input rounded-xl px-3 py-2 text-xs"
                  />
                </div>

                <div className="pt-2">
                  <button
                    type="button"
                    onClick={handleCreateAndMountHF}
                    disabled={loading}
                    className="w-full py-2.5 px-4 bg-gradient-to-b from-amber-500 to-amber-600 hover:brightness-110 active:scale-[0.97] border border-amber-300/30 shadow-[0_6px_20px_-6px_rgba(245,158,11,0.5),inset_0_1px_0_rgba(255,255,255,0.22)] disabled:opacity-50 text-white rounded-xl text-xs font-semibold transition-all"
                  >
                    {t('storageModal.hfAuthorizeBtn')}
                  </button>
                </div>
              </div>
            </div>
          )}
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
