import React from 'react';
import { Cpu, Users, Zap, ExternalLink, Play, AlertTriangle } from 'lucide-react';
import { Account, Session } from '../types';

interface Props {
  accounts: Account[];
  sessions: Session[];
  onOpenSession: (session: Session) => void;
  onNewSession: () => void;
  onLinkAccount: () => void;
}

export const OverviewPage: React.FC<Props> = ({
  accounts,
  sessions,
  onOpenSession,
  onNewSession,
  onLinkAccount,
}) => {
  const totalCcu = accounts.reduce((acc, a) => acc + a.ccu_balance, 0);
  const totalBurnRate = accounts.reduce((acc, a) => acc + a.burn_rate_hourly, 0);
  const activeSessions = sessions.filter(
    (s) => s.status !== 'STOPPED' && s.status !== 'FAILED' && s.status !== 'LOST'
  );
  const orphanSessions = sessions.filter((s) => s.status === 'ORPHAN');

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-8">
      {/* Top Banner & Quick Actions */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">Fleet Overview</h2>
          <p className="text-sm text-slate-400 mt-1">
            Real-time status of compute clusters, CCU balances, and active sessions.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={onLinkAccount}
            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg text-sm font-medium text-slate-200 transition flex items-center gap-2"
          >
            <Users className="w-4 h-4" /> Link Account
          </button>
          <button
            onClick={onNewSession}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition flex items-center gap-2 shadow-lg shadow-blue-600/20"
          >
            <Play className="w-4 h-4 fill-current" /> Launch Runtime
          </button>
        </div>
      </div>

      {/* Metrics Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-slate-800/80 border border-slate-750 p-5 rounded-xl">
          <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider mb-2">
            <span>Pool CCU Balance</span>
            <Zap className="w-4 h-4 text-amber-400" />
          </div>
          <div className="text-2xl font-bold text-white">{totalCcu.toFixed(2)} CCU</div>
          <p className="text-xs text-slate-400 mt-1">Across {accounts.length} linked accounts</p>
        </div>

        <div className="bg-slate-800/80 border border-slate-750 p-5 rounded-xl">
          <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider mb-2">
            <span>Hourly Burn Rate</span>
            <Zap className="w-4 h-4 text-red-400" />
          </div>
          <div className="text-2xl font-bold text-white">{totalBurnRate.toFixed(2)} CCU/h</div>
          <p className="text-xs text-slate-400 mt-1">Estimated consumption</p>
        </div>

        <div className="bg-slate-800/80 border border-slate-750 p-5 rounded-xl">
          <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider mb-2">
            <span>Active Runtimes</span>
            <Cpu className="w-4 h-4 text-green-400" />
          </div>
          <div className="text-2xl font-bold text-white">{activeSessions.length}</div>
          <p className="text-xs text-slate-400 mt-1">Running Google Colab VMs</p>
        </div>

        <div className="bg-slate-800/80 border border-slate-750 p-5 rounded-xl">
          <div className="flex items-center justify-between text-slate-400 text-xs font-semibold uppercase tracking-wider mb-2">
            <span>Accounts Ready</span>
            <Users className="w-4 h-4 text-blue-400" />
          </div>
          <div className="text-2xl font-bold text-white">
            {accounts.filter((a) => a.status === 'ACTIVE').length} / {accounts.length}
          </div>
          <p className="text-xs text-slate-400 mt-1">Ready for instance assignment</p>
        </div>
      </div>

      {/* Orphan Alert Banner */}
      {orphanSessions.length > 0 && (
        <div className="bg-amber-950/40 border border-amber-800/80 rounded-xl p-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-400" />
            <div>
              <div className="text-sm font-semibold text-amber-200">
                {orphanSessions.length} Orphaned Runtime(s) Discovered
              </div>
              <div className="text-xs text-amber-300/80 mt-0.5">
                These instances are running on Google Colab but were provisioned outside the platform. Stop them to prevent unintended CCU burn.
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Active Sessions Table */}
      <div className="bg-slate-800/80 border border-slate-750 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-750 flex items-center justify-between">
          <h3 className="font-semibold text-white text-base">Active Runtimes</h3>
          <span className="text-xs text-slate-400">{activeSessions.length} instances</span>
        </div>

        {activeSessions.length === 0 ? (
          <div className="p-12 text-center text-slate-500">
            No active compute instances. Click "Launch Runtime" above to start one.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-300">
              <thead className="bg-slate-850 text-xs uppercase font-semibold text-slate-400 border-b border-slate-750">
                <tr>
                  <th className="px-6 py-3">Session Name</th>
                  <th className="px-6 py-3">Accelerator</th>
                  <th className="px-6 py-3">Account</th>
                  <th className="px-6 py-3">Status</th>
                  <th className="px-6 py-3">SSH Port</th>
                  <th className="px-6 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-750">
                {activeSessions.map((sess) => (
                  <tr key={sess.id} className="hover:bg-slate-750/50 transition">
                    <td className="px-6 py-4 font-medium text-white flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                      {sess.name}
                    </td>
                    <td className="px-6 py-4">
                      <span className="px-2 py-0.5 text-xs font-mono bg-blue-900/60 text-blue-300 rounded border border-blue-700/50">
                        {sess.accelerator} ({sess.shape})
                      </span>
                    </td>
                    <td className="px-6 py-4 text-xs font-mono text-slate-400">
                      {sess.account_email || sess.account_id}
                    </td>
                    <td className="px-6 py-4">
                      <span className="px-2 py-0.5 text-xs font-semibold rounded bg-emerald-950 text-emerald-400 border border-emerald-800">
                        {sess.status}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-xs font-mono">
                      {sess.ssh_local_port ? (
                        <span className="text-slate-300">localhost:{sess.ssh_local_port}</span>
                      ) : (
                        <span className="text-slate-500">N/A</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right space-x-2">
                      {sess.colab_web_url && (
                        <a
                          href={sess.colab_web_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 px-2.5 py-1 bg-amber-600/80 hover:bg-amber-600 rounded text-xs font-medium text-white transition"
                        >
                          <ExternalLink className="w-3 h-3" /> Colab
                        </a>
                      )}
                      <button
                        onClick={() => onOpenSession(sess)}
                        className="px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-xs font-medium text-white transition"
                      >
                        Workbench
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
