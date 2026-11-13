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
  RefreshCw,
  Eye,
  EyeOff,
} from 'lucide-react';
import { api } from '../services/api';
import { Template, Secret, StorageProfile } from '../types';

export const TemplatesPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'templates' | 'secrets'>('templates');
  const [templates, setTemplates] = useState<Template[]>([]);
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [profiles, setProfiles] = useState<StorageProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

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
      const [tplData, secData, profData] = await Promise.all([
        api.getTemplates(),
        api.getSecrets(),
        api.getStorageProfiles(),
      ]);
      setTemplates(tplData);
      setSecrets(secData);
      setProfiles(profData);
    } catch (err: any) {
      setFeedback({ type: 'error', text: err.message || 'Failed to load templates and secrets' });
    } finally {
      setLoading(false);
    }
  };

  const handleCreateTemplate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tplName.trim()) return;

    const pkgs = tplPackages
      .split(/[,\s\n]+/)
      .map((p) => p.trim())
      .filter(Boolean);

    try {
      await api.createTemplate({
        name: tplName.trim(),
        packages: pkgs,
        secret_ids: tplSelectedSecrets,
        storage_profile_ids: tplSelectedProfiles,
        startup_script: tplStartupScript,
      });
      setFeedback({ type: 'success', text: `Template '${tplName}' created successfully!` });
      setIsTemplateModalOpen(false);
      resetTemplateForm();
      loadAll();
    } catch (err: any) {
      setFeedback({ type: 'error', text: err.message || 'Failed to create template' });
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
    if (!confirm(`Delete template '${name}'?`)) return;
    try {
      await api.deleteTemplate(id);
      setFeedback({ type: 'success', text: `Template '${name}' deleted.` });
      loadAll();
    } catch (err: any) {
      setFeedback({ type: 'error', text: err.message || 'Failed to delete template' });
    }
  };

  const handleCreateSecret = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!secKeyName.trim() || !secValue.trim()) return;

    try {
      await api.createSecret({
        key_name: secKeyName.trim().toUpperCase(),
        value: secValue.trim(),
        description: secDescription.trim(),
      });
      setFeedback({ type: 'success', text: `Secret '${secKeyName.toUpperCase()}' encrypted and saved!` });
      setIsSecretModalOpen(false);
      resetSecretForm();
      loadAll();
    } catch (err: any) {
      setFeedback({ type: 'error', text: err.message || 'Failed to save secret' });
    }
  };

  const resetSecretForm = () => {
    setSecKeyName('');
    setSecValue('');
    setSecDescription('');
    setShowSecretValue(false);
  };

  const handleDeleteSecret = async (id: string, keyName: string) => {
    if (!confirm(`Delete secret '${keyName}'?`)) return;
    try {
      await api.deleteSecret(id);
      setFeedback({ type: 'success', text: `Secret '${keyName}' deleted.` });
      loadAll();
    } catch (err: any) {
      setFeedback({ type: 'error', text: err.message || 'Failed to delete secret' });
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2">
            <Layers className="w-7 h-7 text-indigo-400" />
            Environment Templates & Secrets Vault
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Pre-configure pip dependencies, encrypted credentials (AES-256-GCM), and custom startup scripts for one-click session bootstrapping.
          </p>
        </div>

        {/* Action Button */}
        <div className="flex gap-2">
          {activeTab === 'templates' ? (
            <button
              onClick={() => setIsTemplateModalOpen(true)}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2 shadow-sm"
            >
              <Plus className="w-4 h-4" />
              New Template
            </button>
          ) : (
            <button
              onClick={() => setIsSecretModalOpen(true)}
              className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2 shadow-sm"
            >
              <Key className="w-4 h-4" />
              Add Secret
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
            <span>{feedback.text}</span>
          </div>
          <button onClick={() => setFeedback(null)} className="p-1 hover:bg-slate-800 rounded">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-slate-800">
        <button
          onClick={() => setActiveTab('templates')}
          className={`py-3 px-6 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === 'templates'
              ? 'border-indigo-500 text-indigo-400'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          <Layers className="w-4 h-4" />
          Templates ({templates.length})
        </button>
        <button
          onClick={() => setActiveTab('secrets')}
          className={`py-3 px-6 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === 'secrets'
              ? 'border-emerald-500 text-emerald-400'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          <Key className="w-4 h-4" />
          Secrets Vault ({secrets.length})
        </button>
      </div>

      {/* Tab 1: Templates Grid */}
      {activeTab === 'templates' && (
        <div>
          {loading && templates.length === 0 ? (
            <div className="py-12 text-center text-slate-400">Loading templates...</div>
          ) : templates.length === 0 ? (
            <div className="py-16 text-center border border-dashed border-slate-800 rounded-xl bg-slate-900/40">
              <Layers className="w-10 h-10 text-slate-600 mx-auto mb-3" />
              <h3 className="text-base font-semibold text-white">No Environment Templates</h3>
              <p className="text-sm text-slate-400 mt-1 max-w-md mx-auto">
                Create a template to pre-install packages and inject secrets when launching new Colab sessions.
              </p>
              <button
                onClick={() => setIsTemplateModalOpen(true)}
                className="mt-4 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition-colors inline-flex items-center gap-2"
              >
                <Plus className="w-4 h-4" />
                Create First Template
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
                        onClick={() => handleDeleteTemplate(tpl.id, tpl.name)}
                        className="text-slate-500 hover:text-rose-400 transition-colors p-1 rounded hover:bg-slate-800"
                        title="Delete template"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>

                    {/* Packages */}
                    <div>
                      <div className="text-xs text-slate-400 font-medium mb-1.5 flex items-center gap-1.5">
                        <Package className="w-3.5 h-3.5 text-indigo-400" />
                        Pip Packages ({tpl.packages.length}):
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
                          <span className="text-xs text-slate-600 italic">None specified</span>
                        )}
                      </div>
                    </div>

                    {/* Secrets & Storage */}
                    <div className="text-xs text-slate-400 space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="flex items-center gap-1">
                          <Key className="w-3 h-3 text-emerald-400" /> Attached Secrets:
                        </span>
                        <span className="font-mono text-slate-300">{tpl.secret_ids.length}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span>Attached Storage Profiles:</span>
                        <span className="font-mono text-slate-300">{tpl.storage_profile_ids.length}</span>
                      </div>
                    </div>

                    {/* Startup Script Snippet */}
                    {tpl.startup_script && (
                      <div>
                        <div className="text-xs text-slate-400 font-medium mb-1 flex items-center gap-1">
                          <FileCode className="w-3 h-3 text-amber-400" /> Startup Script:
                        </div>
                        <pre className="bg-slate-950 border border-slate-800 rounded p-2 text-[10px] font-mono text-slate-300 max-h-20 overflow-hidden line-clamp-3">
                          {tpl.startup_script}
                        </pre>
                      </div>
                    )}
                  </div>

                  <div className="pt-4 border-t border-slate-800/60 mt-4 flex items-center justify-between text-[11px] text-slate-500">
                    <span>Updated {tpl.updated_at.split('T')[0]}</span>
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
            <p className="text-xs text-slate-400">
              Secrets are stored with AES-256-GCM authenticated encryption. Raw values are write-only and injected into sessions as environment variables.
            </p>
          </div>

          {loading && secrets.length === 0 ? (
            <div className="py-12 text-center text-slate-400">Loading secrets...</div>
          ) : secrets.length === 0 ? (
            <div className="py-16 text-center text-slate-500">
              <Key className="w-8 h-8 text-slate-600 mx-auto mb-2" />
              No secrets stored in vault yet.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-950/60 text-slate-400 text-xs uppercase tracking-wider border-b border-slate-800">
                  <tr>
                    <th className="py-3 px-6">Key Name</th>
                    <th className="py-3 px-6">Description</th>
                    <th className="py-3 px-6">Stored Value</th>
                    <th className="py-3 px-6">Created</th>
                    <th className="py-3 px-6 text-right">Actions</th>
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
                          onClick={() => handleDeleteSecret(sec.id, sec.key_name)}
                          className="text-slate-500 hover:text-rose-400 transition-colors p-1 rounded hover:bg-slate-800"
                          title="Delete secret"
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

      {/* Modal: New Template */}
      {isTemplateModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-2xl w-full shadow-2xl overflow-hidden flex flex-col">
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
              <h3 className="text-lg font-semibold text-white">Create Environment Template</h3>
              <button
                onClick={() => setIsTemplateModalOpen(false)}
                className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateTemplate} className="p-6 space-y-4 overflow-y-auto max-h-[75vh]">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">Template Name *</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. PyTorch LLM Training"
                  value={tplName}
                  onChange={(e) => setTplName(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Pip Packages (Space or comma separated)
                </label>
                <textarea
                  rows={2}
                  placeholder="torch torchvision torchaudio transformers accelerate datasets flash-attn"
                  value={tplPackages}
                  onChange={(e) => setTplPackages(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-indigo-500"
                />
                <p className="text-[11px] text-slate-500 mt-1">
                  Colab daemon uses <code className="text-slate-400">uv pip install --system</code> with fallback to <code className="text-slate-400">pip</code>.
                </p>
              </div>

              {/* Secrets Checkboxes */}
              {secrets.length > 0 && (
                <div>
                  <label className="block text-xs font-medium text-slate-300 mb-1.5">
                    Inject Secrets from Vault
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
                    Attach Storage Profiles
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
                        <span>{p.name} ({p.type})</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Custom Startup Script (Bash)
                </label>
                <textarea
                  rows={4}
                  placeholder={`# Runs on instance after package installation\npython -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"`}
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
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors"
                >
                  Save Template
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
              <h3 className="text-lg font-semibold text-white">Add Encrypted Secret</h3>
              <button
                onClick={() => setIsSecretModalOpen(false)}
                className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleCreateSecret} className="p-6 space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">Key Name *</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. HF_TOKEN, WANDB_API_KEY"
                  value={secKeyName}
                  onChange={(e) => setSecKeyName(e.target.value.replace(/[^a-zA-Z0-9_]/g, '').toUpperCase())}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-emerald-400 focus:outline-none focus:border-emerald-500"
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-xs font-medium text-slate-300">Secret Value *</label>
                  <button
                    type="button"
                    onClick={() => setShowSecretValue(!showSecretValue)}
                    className="text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1"
                  >
                    {showSecretValue ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    {showSecretValue ? 'Hide' : 'Show'}
                  </button>
                </div>
                <input
                  type={showSecretValue ? 'text' : 'password'}
                  required
                  placeholder="Secret payload..."
                  value={secValue}
                  onChange={(e) => setSecValue(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-white focus:outline-none focus:border-emerald-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">Description (Optional)</label>
                <input
                  type="text"
                  placeholder="e.g. Personal HuggingFace token"
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
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium transition-colors"
                >
                  Save Secret
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
