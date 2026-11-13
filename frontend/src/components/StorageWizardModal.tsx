import React, { useState, useEffect } from 'react';
import { Cloud, HardDrive, Database, CheckCircle2, AlertCircle, X, ExternalLink, RefreshCw } from 'lucide-react';
import { api } from '../services/api';
import { StorageProfile } from '../types';

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
  const [activeTab, setActiveTab] = useState<'gdrive' | 's3' | 'hf'>('gdrive');
  const [profiles, setProfiles] = useState<StorageProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string>('');

  // Status & notifications
  const [loading, setLoading] = useState(false);
  const [statusMsg, setStatusMsg] = useState<{ type: 'success' | 'error' | 'info'; text: string; link?: string } | null>(null);

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
    }
  }, [isOpen]);

  const loadProfiles = async () => {
    try {
      const data = await api.getStorageProfiles();
      setProfiles(data);
    } catch (err: any) {
      console.error('Failed to load storage profiles', err);
    }
  };

  const handleGDrivePropagate = async (dryrun = false) => {
    setLoading(true);
    setStatusMsg({ type: 'info', text: 'Executing Google Drive credential propagation handshake...' });
    try {
      const res = await api.propagateGDrive(sessionId, dryrun);
      if (res.status === 'AUTH_REQUIRED' && res.redirect_url) {
        setStatusMsg({
          type: 'info',
          text: 'Google Drive authorization required. Click below to grant permissions in your browser, then retry.',
          link: res.redirect_url,
        });
      } else {
        setStatusMsg({
          type: 'success',
          text: 'Google Drive credentials propagated successfully! Runtime /content/drive is mounted and accessible.',
        });
        if (onStorageMounted) onStorageMounted();
      }
    } catch (err: any) {
      setStatusMsg({ type: 'error', text: err.message || 'Google Drive propagation failed.' });
    } finally {
      setLoading(false);
    }
  };

  const handleMountExistingProfile = async () => {
    if (!selectedProfileId) return;
    setLoading(true);
    setStatusMsg({ type: 'info', text: 'Configuring storage and validating remote mount accessibility...' });
    try {
      const res = await api.mountStorage(sessionId, selectedProfileId);
      if (res.mount_verified) {
        setStatusMsg({
          type: 'success',
          text: `Storage profile successfully mounted and verified at ${res.mount_path}!`,
        });
        if (onStorageMounted) onStorageMounted();
      } else {
        setStatusMsg({
          type: 'info',
          text: `Storage configuration deployed to ${res.mount_path}. Preflight check pending.`,
        });
      }
    } catch (err: any) {
      setStatusMsg({ type: 'error', text: err.message || 'Failed to mount storage profile.' });
    } finally {
      setLoading(false);
    }
  };

  const handleCreateAndMountS3 = async () => {
    if (!s3Name.trim() || !s3AccessKey.trim() || !s3SecretKey.trim()) {
      setStatusMsg({ type: 'error', text: 'Please fill in Name, Access Key, and Secret Key.' });
      return;
    }
    setLoading(true);
    setStatusMsg({ type: 'info', text: 'Creating S3 storage profile and injecting rclone config...' });
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
        text: `S3 profile '${profile.name}' created and mounted at ${profile.mount_path}!`,
      });
      loadProfiles();
      if (onStorageMounted) onStorageMounted();
    } catch (err: any) {
      setStatusMsg({ type: 'error', text: err.message || 'Failed to configure S3 storage.' });
    } finally {
      setLoading(false);
    }
  };

  const handleCreateAndMountHF = async () => {
    if (!hfName.trim() || !hfToken.trim()) {
      setStatusMsg({ type: 'error', text: 'Please provide a Profile Name and Hugging Face Token.' });
      return;
    }
    setLoading(true);
    setStatusMsg({ type: 'info', text: 'Creating Hugging Face profile and injecting authorization token...' });
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
        text: `Hugging Face token authorized on instance! Cache directory: ${profile.mount_path}.`,
      });
      loadProfiles();
      if (onStorageMounted) onStorageMounted();
    } catch (err: any) {
      setStatusMsg({ type: 'error', text: err.message || 'Failed to authorize Hugging Face.' });
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-2xl w-full shadow-2xl overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Cloud className="w-5 h-5 text-indigo-400" />
            <h2 className="text-lg font-semibold text-white">Cloud Storage & Mount Wizard</h2>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white transition-colors p-1 rounded-lg hover:bg-slate-800"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-slate-800 px-6 bg-slate-950/40">
          <button
            onClick={() => { setActiveTab('gdrive'); setStatusMsg(null); }}
            className={`py-3 px-4 text-xs font-medium border-b-2 transition-colors flex items-center gap-2 ${
              activeTab === 'gdrive'
                ? 'border-indigo-500 text-indigo-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <HardDrive className="w-4 h-4" />
            Google Drive
          </button>
          <button
            onClick={() => { setActiveTab('s3'); setStatusMsg(null); }}
            className={`py-3 px-4 text-xs font-medium border-b-2 transition-colors flex items-center gap-2 ${
              activeTab === 's3'
                ? 'border-indigo-500 text-indigo-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Database className="w-4 h-4" />
            S3 / Cloudflare R2
          </button>
          <button
            onClick={() => { setActiveTab('hf'); setStatusMsg(null); }}
            className={`py-3 px-4 text-xs font-medium border-b-2 transition-colors flex items-center gap-2 ${
              activeTab === 'hf'
                ? 'border-indigo-500 text-indigo-400'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Cloud className="w-4 h-4 text-amber-400" />
            Hugging Face Hub
          </button>
        </div>

        {/* Tab Content */}
        <div className="p-6 space-y-6 overflow-y-auto max-h-[70vh]">
          {/* Status Alert */}
          {statusMsg && (
            <div
              className={`p-4 rounded-lg text-sm flex items-start gap-3 ${
                statusMsg.type === 'success'
                  ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                  : statusMsg.type === 'error'
                  ? 'bg-rose-500/10 border border-rose-500/20 text-rose-400'
                  : 'bg-indigo-500/10 border border-indigo-500/20 text-indigo-300'
              }`}
            >
              {statusMsg.type === 'success' ? (
                <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" />
              ) : statusMsg.type === 'error' ? (
                <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
              ) : (
                <RefreshCw className="w-5 h-5 shrink-0 mt-0.5 animate-spin" />
              )}
              <div className="space-y-2">
                <p>{statusMsg.text}</p>
                {statusMsg.link && (
                  <a
                    href={statusMsg.link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-xs font-medium transition-colors"
                  >
                    Grant Consent in Browser <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                )}
              </div>
            </div>
          )}

          {/* Tab 1: Google Drive */}
          {activeTab === 'gdrive' && (
            <div className="space-y-4">
              <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg text-xs text-slate-300 space-y-2">
                <strong className="text-white block font-medium">Official Credentials Propagation:</strong>
                <p>
                  Colab utilizes an ephemeral token propagation flow via Google's control plane to authenticate and mount
                  Google Drive directly at <code className="text-indigo-300 font-mono">/content/drive/MyDrive</code>.
                </p>
                <p className="text-slate-400">
                  This connects your files with native zero-copy kernel bandwidth, bypassing third-party FUSE dependencies.
                </p>
              </div>

              <div className="pt-2">
                <button
                  onClick={() => handleGDrivePropagate(false)}
                  disabled={loading}
                  className="w-full py-2.5 px-4 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 shadow-sm"
                >
                  {loading && <RefreshCw className="w-4 h-4 animate-spin" />}
                  Propagate Credentials & Mount Drive
                </button>
              </div>
            </div>
          )}

          {/* Tab 2: S3 / Cloudflare R2 */}
          {activeTab === 's3' && (
            <div className="space-y-4">
              {profiles.filter((p) => p.type === 's3').length > 0 && (
                <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg space-y-2">
                  <label className="block text-xs font-medium text-slate-400">Select Existing S3 Profile</label>
                  <div className="flex gap-2">
                    <select
                      value={selectedProfileId}
                      onChange={(e) => setSelectedProfileId(e.target.value)}
                      className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white"
                    >
                      <option value="">-- Choose Profile --</option>
                      {profiles
                        .filter((p) => p.type === 's3')
                        .map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name} ({p.mount_path})
                          </option>
                        ))}
                    </select>
                    <button
                      onClick={handleMountExistingProfile}
                      disabled={loading || !selectedProfileId}
                      className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-medium rounded-lg transition-colors"
                    >
                      Mount Profile
                    </button>
                  </div>
                </div>
              )}

              <div className="border-t border-slate-800 pt-4 space-y-3">
                <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                  Or Create New S3 / Cloudflare R2 Profile
                </h4>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">Profile Name</label>
                    <input
                      type="text"
                      placeholder="e.g. My R2 Models"
                      value={s3Name}
                      onChange={(e) => setS3Name(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">Provider</label>
                    <select
                      value={s3Provider}
                      onChange={(e) => setS3Provider(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white"
                    >
                      <option value="AWS">Amazon S3</option>
                      <option value="Cloudflare">Cloudflare R2</option>
                      <option value="MinIO">MinIO / Custom S3</option>
                    </select>
                  </div>
                </div>

                <div>
                  <label className="block text-xs text-slate-400 mb-1">Endpoint URL (Optional for AWS, Required for R2)</label>
                  <input
                    type="text"
                    placeholder="https://<accountid>.r2.cloudflarestorage.com"
                    value={s3Endpoint}
                    onChange={(e) => setS3Endpoint(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">Access Key ID</label>
                    <input
                      type="text"
                      placeholder="AKIA..."
                      value={s3AccessKey}
                      onChange={(e) => setS3AccessKey(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-slate-400 mb-1">Secret Access Key</label>
                    <input
                      type="password"
                      placeholder="••••••••••••"
                      value={s3SecretKey}
                      onChange={(e) => setS3SecretKey(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-xs text-slate-400 mb-1">Remote Mount / Target Path</label>
                  <input
                    type="text"
                    placeholder="/content/data"
                    value={s3MountPath}
                    onChange={(e) => setS3MountPath(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white"
                  />
                </div>

                <div className="pt-2">
                  <button
                    onClick={handleCreateAndMountS3}
                    disabled={loading}
                    className="w-full py-2 px-4 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-xs font-medium transition-colors"
                  >
                    Save & Mount S3 Profile
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Tab 3: Hugging Face */}
          {activeTab === 'hf' && (
            <div className="space-y-4">
              <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg text-xs text-slate-300 space-y-2">
                <strong className="text-white block font-medium">Hugging Face CLI & Hub Authorization:</strong>
                <p>
                  Deploys your user access token to <code className="text-amber-300 font-mono">/root/.cache/huggingface/token</code>,
                  granting frictionless access to gated models (e.g. LLaMA, Gemma, SDXL) in both Python and CLI scripts.
                </p>
              </div>

              <div className="space-y-3">
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Profile Name</label>
                  <input
                    type="text"
                    placeholder="e.g. HuggingFace Main"
                    value={hfName}
                    onChange={(e) => setHfName(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Hugging Face Access Token (User Read/Write)</label>
                  <input
                    type="password"
                    placeholder="hf_••••••••••••••••••••••••"
                    value={hfToken}
                    onChange={(e) => setHfToken(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Target Cache Path</label>
                  <input
                    type="text"
                    value={hfMountPath}
                    onChange={(e) => setHfMountPath(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs text-white"
                  />
                </div>

                <div className="pt-2">
                  <button
                    onClick={handleCreateAndMountHF}
                    disabled={loading}
                    className="w-full py-2.5 px-4 bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white rounded-lg text-xs font-medium transition-colors"
                  >
                    Authorize Hugging Face on Session
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 bg-slate-950/60 border-t border-slate-800 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-sm font-medium transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
