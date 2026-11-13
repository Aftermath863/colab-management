import React, { useState } from 'react';
import { Cpu, X } from 'lucide-react';
import { Account } from '../types';

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
  const [name, setName] = useState(`session-${Math.random().toString(36).substring(2, 7)}`);
  const [accelerator, setAccelerator] = useState('T4');
  const [shape, setShape] = useState('standard');
  const [lifecyclePolicy, setLifecyclePolicy] = useState('interactive');
  const [accountId, setAccountId] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accelerators = [
    { id: 'T4', name: 'Nvidia T4 (Default standard GPU)' },
    { id: 'L4', name: 'Nvidia L4 (High memory only)' },
    { id: 'A100', name: 'Nvidia A100 (40GB/80GB Pro+)' },
    { id: 'V5E1', name: 'TPU v5e (Single chip)' },
  ];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setLoading(true);
    setError(null);
    try {
      await onLaunch({
        name: name.trim(),
        accelerator,
        shape,
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
            Provision Colab Runtime
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1">
              Session Name
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
              Hardware Accelerator
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
                Machine Shape
              </label>
              <select
                value={shape}
                onChange={(e) => setShape(e.target.value)}
                disabled={accelerator === 'L4' || accelerator === 'V5E1'}
                className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-white focus:border-blue-500 focus:outline-none disabled:opacity-50"
              >
                <option value="standard">Standard RAM</option>
                <option value="hm">High-RAM (hm)</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1">
                Lifecycle Policy
              </label>
              <select
                value={lifecyclePolicy}
                onChange={(e) => setLifecyclePolicy(e.target.value)}
                className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-white focus:border-blue-500 focus:outline-none"
              >
                <option value="interactive">Interactive</option>
                <option value="keep_alive">Keep Alive</option>
                <option value="ephemeral">Ephemeral</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-1">
              Account Selector
            </label>
            <select
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded text-sm text-white focus:border-blue-500 focus:outline-none"
            >
              <option value="">Auto-Select Best Account (Balanced Quota)</option>
              {accounts.map((acc) => (
                <option key={acc.id} value={acc.id}>
                  {acc.email} ({acc.ccu_balance.toFixed(1)} CCU, {acc.active_assignments} active)
                </option>
              ))}
            </select>
          </div>

          {error && (
            <div className="text-xs text-red-400 bg-red-950/40 p-2.5 rounded border border-red-800">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded text-sm text-slate-300"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded text-sm font-medium text-white transition flex items-center gap-2"
            >
              {loading ? 'Provisioning...' : 'Launch Runtime'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
