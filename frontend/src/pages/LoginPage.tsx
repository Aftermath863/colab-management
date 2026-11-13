import React, { useState } from 'react';
import { Terminal, Lock, ArrowRight } from 'lucide-react';
import { api } from '../services/api';

interface Props {
  onLoginSuccess: () => void;
}

export const LoginPage: React.FC<Props> = ({ onLoginSuccess }) => {
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password) return;
    setLoading(true);
    setError(null);
    try {
      await api.login(password);
      onLoginSuccess();
    } catch (err: any) {
      setError(err.message || 'Authentication failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col justify-center items-center p-4">
      <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-2xl">
        <div className="flex flex-col items-center mb-8">
          <div className="w-14 h-14 rounded-2xl bg-blue-600 flex items-center justify-center text-white mb-4 shadow-lg shadow-blue-500/20">
            <Terminal className="w-8 h-8" />
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Colab Management</h1>
          <p className="text-sm text-slate-400 mt-1">Unified Fleet Control Console</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
              Admin Password
            </label>
            <div className="relative">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password"
                className="w-full px-4 py-3 bg-slate-800/80 border border-slate-700 rounded-xl text-white placeholder-slate-500 focus:outline-none focus:border-blue-500 transition pl-11"
                required
              />
              <Lock className="w-5 h-5 text-slate-500 absolute left-3.5 top-3.5" />
            </div>
          </div>

          {error && (
            <div className="text-xs text-red-400 bg-red-950/40 p-3 rounded-lg border border-red-800">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium rounded-xl transition flex items-center justify-center gap-2 shadow-lg shadow-blue-600/20"
          >
            {loading ? 'Authenticating...' : 'Sign In to Console'}
            <ArrowRight className="w-4 h-4" />
          </button>
        </form>

        <div className="mt-8 pt-6 border-t border-slate-800 text-center">
          <p className="text-xs text-slate-500">
            Default credentials configured via <code>ADMIN_PASSWORD</code>.
          </p>
        </div>
      </div>
    </div>
  );
};
