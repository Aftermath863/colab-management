import React, { useState } from 'react';
import { Play, Square, ExternalLink, Terminal } from 'lucide-react';
import { Session } from '../types';
import { StopSessionModal } from '../components/StopSessionModal';
import { SnapshotModal } from '../components/SnapshotModal';
import { useI18n } from '../i18n';

interface Props {
  sessions: Session[];
  onOpenSession: (session: Session) => void;
  onNewSession: () => void;
  onStopSession: (sessionId: string) => Promise<void>;
  onRefresh: () => Promise<void>;
}

export const SessionsPage: React.FC<Props> = ({
  sessions,
  onOpenSession,
  onNewSession,
  onStopSession,
  onRefresh,
}) => {
  const { t, formatActionError, formatSessionStatus, formatShape } = useI18n();
  const [filter, setFilter] = useState<'all' | 'active' | 'stopped'>('all');
  const [stoppingId, setStoppingId] = useState<string | null>(null);
  const [stopTarget, setStopTarget] = useState<Session | null>(null);
  const [snapshotSaveTarget, setSnapshotSaveTarget] = useState<Session | null>(null);

  const filteredSessions = sessions.filter((s) => {
    if (filter === 'active') return s.status !== 'STOPPED' && s.status !== 'FAILED';
    if (filter === 'stopped') return s.status === 'STOPPED' || s.status === 'FAILED';
    return true;
  });

  const handleStopDirect = async (sessionId: string) => {
    setStoppingId(sessionId);
    try {
      await onStopSession(sessionId);
      await onRefresh();
    } catch (err: any) {
      alert(formatActionError('sessions.stopFailed', err?.message));
    } finally {
      setStoppingId(null);
    }
  };

  const tabLabels = {
    all: t('sessions.filterAll'),
    active: t('sessions.filterActive'),
    stopped: t('sessions.filterStopped'),
  };

  return (
    <div className="p-4 sm:p-8 max-w-7xl mx-auto space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-white tracking-tight">{t('sessions.title')}</h2>
          <p className="text-sm text-slate-400 mt-1">{t('sessions.subtitle')}</p>
        </div>

        <button
          type="button"
          onClick={onNewSession}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg text-sm font-medium text-white transition flex items-center gap-2 shadow-lg shadow-blue-600/20"
        >
          <Play className="w-4 h-4 fill-current" /> {t('sessions.launchRuntime')}
        </button>
      </div>

      {/* Filter Tabs */}
      <div className="flex items-center gap-2 border-b border-slate-750 pb-3">
        {(['all', 'active', 'stopped'] as const).map((tab) => (
          <button
            type="button"
            key={tab}
            onClick={() => setFilter(tab)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold uppercase tracking-wider transition ${
              filter === tab
                ? 'bg-slate-800 text-blue-400 border border-slate-700'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            {tabLabels[tab]} (
            {
              sessions.filter((s) => {
                if (tab === 'active') return s.status !== 'STOPPED' && s.status !== 'FAILED';
                if (tab === 'stopped') return s.status === 'STOPPED' || s.status === 'FAILED';
                return true;
              }).length
            }
            )
          </button>
        ))}
      </div>

      {/* Sessions Table */}
      <div className="bg-slate-800/80 border border-slate-750 rounded-xl overflow-hidden">
        {filteredSessions.length === 0 ? (
          <div className="p-12 text-center text-slate-500">{t('sessions.noMatchingSessions')}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-slate-300">
              <thead className="bg-slate-850 text-xs uppercase font-semibold text-slate-400 border-b border-slate-750">
                <tr>
                  <th className="px-6 py-3">{t('sessions.colSession')}</th>
                  <th className="px-6 py-3">{t('sessions.colAccelerator')}</th>
                  <th className="px-6 py-3">{t('sessions.colAccount')}</th>
                  <th className="px-6 py-3">{t('sessions.colStatus')}</th>
                  <th className="px-6 py-3">{t('sessions.colSshPort')}</th>
                  <th className="px-6 py-3">{t('sessions.colCreated')}</th>
                  <th className="px-6 py-3 text-right">{t('sessions.colActions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-750">
                {filteredSessions.map((sess) => {
                  const isActive = sess.status !== 'STOPPED' && sess.status !== 'FAILED';
                  return (
                    <tr key={sess.id} className="hover:bg-slate-750/50 transition">
                      <td className="px-6 py-4 font-medium text-white flex items-center gap-2">
                        {isActive && <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />}
                        {sess.name}
                      </td>
                      <td className="px-6 py-4">
                        <span className="px-2 py-0.5 text-xs font-mono bg-blue-900/60 text-blue-300 rounded border border-blue-700/50">
                          {sess.accelerator} ({formatShape(sess.shape)})
                        </span>
                      </td>
                      <td className="px-6 py-4 text-xs font-mono text-slate-400">
                        {sess.account_email || sess.account_id}
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`px-2 py-0.5 text-xs font-semibold rounded ${
                            sess.status === 'READY' || sess.status === 'BUSY'
                              ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                              : sess.status === 'ORPHAN'
                              ? 'bg-amber-950 text-amber-400 border border-amber-800'
                              : 'bg-slate-800 text-slate-400 border border-slate-700'
                          }`}
                        >
                          {formatSessionStatus(sess.status)}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-xs font-mono">
                        {sess.ssh_local_port ? (
                          <span className="text-slate-200">localhost:{sess.ssh_local_port}</span>
                        ) : (
                          <span className="text-slate-500">—</span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-xs text-slate-400">
                        {sess.created_at?.split(' ')[0] || t('sessions.recently')}
                      </td>
                      <td className="px-6 py-4 text-right space-x-2">
                        {sess.colab_web_url && (
                          <a
                            href={sess.colab_web_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 px-2.5 py-1 bg-amber-600/80 hover:bg-amber-600 rounded text-xs font-medium text-white transition"
                          >
                            <ExternalLink className="w-3 h-3" /> {t('sessions.openColab')}
                          </a>
                        )}

                        {isActive && (
                          <>
                            <button
                              type="button"
                              onClick={() => onOpenSession(sess)}
                              className="px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-xs font-medium text-white transition inline-flex items-center gap-1"
                            >
                              <Terminal className="w-3 h-3" /> {t('sessions.workbench')}
                            </button>
                            <button
                              type="button"
                              onClick={() => setStopTarget(sess)}
                              disabled={stoppingId === sess.id}
                              title={t('sessions.stopTooltip')}
                              className="px-2.5 py-1 bg-red-950/60 hover:bg-red-900 border border-red-800/60 rounded text-xs text-red-300 transition"
                            >
                              <Square className="w-3 h-3 fill-current inline" />
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {stopTarget && (
        <StopSessionModal
          session={stopTarget}
          onSaveAndStop={() => {
            const target = stopTarget;
            setStopTarget(null);
            setSnapshotSaveTarget(target);
          }}
          onStopDirectly={async () => {
            const target = stopTarget;
            setStopTarget(null);
            await handleStopDirect(target.id);
          }}
          onClose={() => setStopTarget(null)}
        />
      )}

      {snapshotSaveTarget && (
        <SnapshotModal
          session={snapshotSaveTarget}
          initialTab="save"
          initialStopAfterSave={true}
          onCompleted={async (task) => {
            if (task.status === 'COMPLETED') {
              await onRefresh();
            }
          }}
          onClose={() => setSnapshotSaveTarget(null)}
        />
      )}
    </div>
  );
};
