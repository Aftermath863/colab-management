import React, { useState, useEffect } from 'react';
import { ExternalLink, Terminal as TermIcon, Folder, Code2, Square, ArrowLeft, Cloud, Key, ShieldCheck, Activity } from 'lucide-react';
import { Session } from '../types';
import { TerminalTab } from '../components/TerminalTab';
import { FileExplorerTab } from '../components/FileExplorerTab';
import { MonacoEditorTab } from '../components/MonacoEditorTab';
import { TelemetryCharts } from '../components/TelemetryCharts';
import { SSHHelperModal } from '../components/SSHHelperModal';
import { StorageWizardModal } from '../components/StorageWizardModal';
import { api } from '../services/api';

interface Props {
  session: Session;
  onBack: () => void;
  onStop: (sessionId: string) => Promise<void>;
}

export const SessionWorkbenchPage: React.FC<Props> = ({ session, onBack, onStop }) => {
  const [activeTab, setActiveTab] = useState<'terminal' | 'files' | 'editor' | 'metrics'>('terminal');
  const [editingFile, setEditingFile] = useState<string | null>(null);
  const [colabUrl, setColabUrl] = useState<string | null>(session.colab_web_url || null);

  // Modals
  const [isSSHModalOpen, setIsSSHModalOpen] = useState(false);
  const [isStorageModalOpen, setIsStorageModalOpen] = useState(false);

  useEffect(() => {
    if (!colabUrl) {
      api.getColabUrl(session.id)
        .then((url) => setColabUrl(url))
        .catch(() => {});
    }
  }, [session.id]);

  const handleOpenFileInEditor = (path: string) => {
    setEditingFile(path);
    setActiveTab('editor');
  };

  return (
    <div className="h-screen flex flex-col bg-slate-900 text-white">
      {/* Top Header */}
      <header className="h-14 bg-slate-800 border-b border-slate-700 flex items-center justify-between px-4">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="p-1.5 hover:bg-slate-700 rounded text-slate-400 hover:text-white">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-green-400 animate-pulse" />
            <h1 className="font-semibold text-base">{session.name}</h1>
            <span className="px-2 py-0.5 text-xs font-mono bg-blue-900/60 text-blue-300 rounded border border-blue-700/50">
              {session.accelerator}
            </span>

            {/* Clickable SSH Badge */}
            {session.ssh_local_port ? (
              <button
                onClick={() => setIsSSHModalOpen(true)}
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-mono bg-indigo-900/50 hover:bg-indigo-800/60 text-indigo-300 rounded border border-indigo-700/50 transition cursor-pointer"
                title="Open SSH Connection & IDE Bridge"
              >
                <Key className="w-3 h-3 text-indigo-400" />
                <span>SSH: localhost:{session.ssh_local_port}</span>
              </button>
            ) : (
              <span className="px-2 py-0.5 text-xs text-slate-500 bg-slate-800 rounded">
                SSH Pool Exhausted
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Cloud Storage Wizard Button */}
          <button
            onClick={() => setIsStorageModalOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-xs font-medium text-slate-200 transition"
          >
            <Cloud className="w-3.5 h-3.5 text-sky-400" />
            Cloud Storage
          </button>

          {colabUrl && (
            <a
              href={colabUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-600 hover:bg-amber-500 rounded text-xs font-medium text-white transition shadow"
            >
              <ExternalLink className="w-4 h-4" />
              Open in Colab
            </a>
          )}
          <button
            onClick={() => onStop(session.id)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600/80 hover:bg-red-600 rounded text-xs font-medium text-white transition"
          >
            <Square className="w-3.5 h-3.5 fill-current" />
            Stop Session
          </button>
        </div>
      </header>

      {/* Tabs Header */}
      <div className="h-10 bg-slate-850 border-b border-slate-750 flex items-center px-4 gap-2">
        <button
          onClick={() => setActiveTab('terminal')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-t text-xs font-medium transition ${
            activeTab === 'terminal' ? 'bg-slate-900 text-blue-400 border-t-2 border-blue-500' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <TermIcon className="w-3.5 h-3.5" />
          Terminal
        </button>
        <button
          onClick={() => setActiveTab('files')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-t text-xs font-medium transition ${
            activeTab === 'files' ? 'bg-slate-900 text-blue-400 border-t-2 border-blue-500' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <Folder className="w-3.5 h-3.5" />
          Files (/content)
        </button>
        <button
          onClick={() => setActiveTab('editor')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-t text-xs font-medium transition ${
            activeTab === 'editor' ? 'bg-slate-900 text-blue-400 border-t-2 border-blue-500' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <Code2 className="w-3.5 h-3.5" />
          Editor {editingFile ? `(${editingFile.split('/').pop()})` : ''}
        </button>
        <button
          onClick={() => setActiveTab('metrics')}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-t text-xs font-medium transition ${
            activeTab === 'metrics' ? 'bg-slate-900 text-blue-400 border-t-2 border-blue-500' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <Activity className="w-3.5 h-3.5" />
          Metrics
        </button>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-hidden relative">
        {activeTab === 'terminal' && <TerminalTab sessionId={session.id} />}
        {activeTab === 'files' && (
          <FileExplorerTab sessionId={session.id} onEditFile={handleOpenFileInEditor} />
        )}
        {activeTab === 'editor' && (
          editingFile ? (
            <MonacoEditorTab
              sessionId={session.id}
              filePath={editingFile}
            />
          ) : (
            <div className="h-full flex items-center justify-center text-slate-500 text-sm">
              Select a file from the Files tab to open in the Monaco editor.
            </div>
          )
        )}
        {activeTab === 'metrics' && (
          <div className="h-full overflow-y-auto p-6 bg-slate-950">
            <TelemetryCharts sessionId={session.id} />
          </div>
        )}
      </div>

      {/* SSH Connection Helper Modal */}
      {isSSHModalOpen && (
        <SSHHelperModal
          sessionId={session.id}
          sessionName={session.name}
          isOpen={isSSHModalOpen}
          onClose={() => setIsSSHModalOpen(false)}
        />
      )}

      {/* Cloud Storage Wizard Modal */}
      {isStorageModalOpen && (
        <StorageWizardModal
          sessionId={session.id}
          isOpen={isStorageModalOpen}
          onClose={() => setIsStorageModalOpen(false)}
        />
      )}
    </div>
  );
};
