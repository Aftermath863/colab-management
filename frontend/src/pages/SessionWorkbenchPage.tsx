import React, { useState, useEffect } from 'react';
import {
  ExternalLink,
  Terminal as TermIcon,
  Folder,
  Code2,
  Square,
  ArrowLeft,
  Cloud,
  Key,
  Activity,
  Camera,
  Laptop,
  Command,
} from 'lucide-react';
import { Session } from '../types';
import { TerminalTab } from '../components/TerminalTab';
import { FileExplorerTab } from '../components/FileExplorerTab';
import { MonacoEditorTab } from '../components/MonacoEditorTab';
import { TelemetryCharts } from '../components/TelemetryCharts';
import { SSHHelperModal } from '../components/SSHHelperModal';
import { StorageWizardModal } from '../components/StorageWizardModal';
import { SnapshotModal } from '../components/SnapshotModal';
import { StopSessionModal } from '../components/StopSessionModal';
import { LanguageSwitcher } from '../components/LanguageSwitcher';
import { api } from '../services/api';
import { useI18n } from '../i18n';
import {
  isMacDesktop,
  triggerHaptic,
  notifyToast,
  openCommandPalette,
  openExternalUrl,
  openTerminalSSH,
  openInIDE,
  handleMacDragMouseDown,
  handleMacDragDoubleClick,
  getAcceleratorBadgeClass,
  getSessionStatusStyles,
} from '../utils/macDesktop';

interface Props {
  session: Session;
  autoRestoreSnapshotId?: string | null;
  onClearAutoRestore?: () => void;
  onBack: () => void;
  onStop: (sessionId: string) => Promise<void>;
}

type WorkbenchTabId = 'terminal' | 'files' | 'editor' | 'metrics';

export const SessionWorkbenchPage: React.FC<Props> = ({
  session,
  autoRestoreSnapshotId = null,
  onClearAutoRestore,
  onBack,
  onStop,
}) => {
  const { t, locale, formatActionError } = useI18n();
  const isZh = locale === 'zh';
  const macDesktop = isMacDesktop();

  const [activeTab, setActiveTab] = useState<WorkbenchTabId>('terminal');
  const [editingFile, setEditingFile] = useState<string | null>(null);
  const [colabUrl, setColabUrl] = useState<string | null>(session.colab_web_url || null);

  // Modals
  const [isSSHModalOpen, setIsSSHModalOpen] = useState(false);
  const [isStorageModalOpen, setIsStorageModalOpen] = useState(false);
  const [isSnapshotModalOpen, setIsSnapshotModalOpen] = useState(Boolean(autoRestoreSnapshotId));
  const [snapshotModalTab, setSnapshotModalTab] = useState<'save' | 'restore'>(
    autoRestoreSnapshotId ? 'restore' : 'save'
  );
  const [snapshotStopAfterSave, setSnapshotStopAfterSave] = useState(false);
  const [isStopModalOpen, setIsStopModalOpen] = useState(false);

  const anyModalOpen =
    isSSHModalOpen || isStorageModalOpen || isSnapshotModalOpen || isStopModalOpen;

  useEffect(() => {
    if (autoRestoreSnapshotId) {
      setSnapshotModalTab('restore');
      setSnapshotStopAfterSave(false);
      setIsSnapshotModalOpen(true);
    }
  }, [autoRestoreSnapshotId]);

  useEffect(() => {
    if (!colabUrl) {
      api
        .getColabUrl(session.id)
        .then((url) => setColabUrl(url))
        .catch(() => {});
    }
  }, [session.id, colabUrl]);

  // ⌃1 – ⌃4 keyboard shortcuts for switching workbench tabs
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const tabMap: Record<string, WorkbenchTabId> = {
      '1': 'terminal',
      '2': 'files',
      '3': 'editor',
      '4': 'metrics',
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (anyModalOpen) return;
      if (e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey && tabMap[e.key]) {
        e.preventDefault();
        triggerHaptic('alignment');
        setActiveTab(tabMap[e.key]);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [anyModalOpen]);

  const handleOpenFileInEditor = (path: string) => {
    setEditingFile(path);
    setActiveTab('editor');
  };

  const handleTabChange = (tab: WorkbenchTabId) => {
    triggerHaptic('alignment');
    setActiveTab(tab);
  };

  const statusStyle = getSessionStatusStyles(session.status);

  const tabs: Array<{
    id: WorkbenchTabId;
    label: string;
    shortcut: string;
    icon: React.ReactNode;
  }> = [
    {
      id: 'terminal',
      label: t('workbench.tabTerminal'),
      shortcut: '⌃1',
      icon: <TermIcon className="w-3.5 h-3.5 shrink-0" />,
    },
    {
      id: 'files',
      label: t('workbench.tabFiles'),
      shortcut: '⌃2',
      icon: <Folder className="w-3.5 h-3.5 shrink-0" />,
    },
    {
      id: 'editor',
      label: `${t('workbench.tabEditor')}${editingFile ? ` (${editingFile.split('/').pop()})` : ''}`,
      shortcut: '⌃3',
      icon: <Code2 className="w-3.5 h-3.5 shrink-0" />,
    },
    {
      id: 'metrics',
      label: t('workbench.tabMetrics'),
      shortcut: '⌃4',
      icon: <Activity className="w-3.5 h-3.5 shrink-0" />,
    },
  ];

  return (
    <div className="mac-ambient-bg h-screen flex flex-col text-white overflow-hidden">
      {/* Unified Draggable macOS Studio Header */}
      <header
        data-mac-drag-region="true"
        onMouseDown={handleMacDragMouseDown}
        onDoubleClick={handleMacDragDoubleClick}
        className={`mac-glass-heavy min-h-[3.5rem] py-2 border-b border-white/[0.08] flex flex-wrap items-center justify-between gap-2 sm:gap-4 select-none ${
          macDesktop ? 'pl-20 pr-3 sm:pr-4' : 'px-3 sm:px-4'
        }`}
      >
        <div className="flex items-center gap-2 sm:gap-3 min-w-0">
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              onBack();
            }}
            title={t('workbench.backTooltip')}
            className="p-1.5 hover:bg-white/[0.08] rounded-lg text-slate-300 hover:text-white transition shrink-0"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>

          <div className="flex flex-wrap items-center gap-2 min-w-0">
            <span
              className={`w-2.5 h-2.5 rounded-full shrink-0 ${statusStyle.dotClass} ${
                statusStyle.isPulsing ? 'animate-pulse' : ''
              }`}
            />
            <h1 className="font-semibold text-sm sm:text-base truncate max-w-[160px] sm:max-w-xs tracking-tight">
              {session.name}
            </h1>
            <span
              className={`px-2 py-0.5 text-xs font-mono rounded-lg border shrink-0 ${getAcceleratorBadgeClass(
                session.accelerator
              )}`}
            >
              {session.accelerator}
            </span>

            {/* Clickable SSH Tunnel Pill */}
            {session.ssh_local_port ? (
              <div className="inline-flex items-center gap-1.5 shrink-0">
                <button
                  type="button"
                  onClick={() => {
                    triggerHaptic('light');
                    setIsSSHModalOpen(true);
                  }}
                  className="flex items-center gap-1.5 px-2.5 py-1 text-xs font-mono bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-200 rounded-lg border border-indigo-400/30 transition cursor-pointer tabular-nums"
                  title={t('workbench.sshBridgeTooltip')}
                >
                  <Key className="w-3 h-3 text-indigo-300" />
                  <span>{t('workbench.sshBadge', { port: session.ssh_local_port })}</span>
                </button>

                {macDesktop && (
                  <>
                    <button
                      type="button"
                      onClick={() => openTerminalSSH(session.ssh_local_port!, session.name)}
                      title={isZh ? '在 Terminal.app 中打开 SSH' : 'Open in Terminal.app'}
                      className="mac-btn-secondary px-2 py-1 rounded-lg text-xs font-medium inline-flex items-center gap-1"
                    >
                      <TermIcon className="w-3 h-3 text-emerald-400" />
                      <span className="hidden lg:inline">Terminal.app</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => openInIDE('cursor', session.ssh_local_port!)}
                      title={isZh ? '在 Cursor 中连接远程工作区' : 'Open Remote in Cursor'}
                      className="mac-btn-secondary px-2 py-1 rounded-lg text-xs font-medium inline-flex items-center gap-1"
                    >
                      <Laptop className="w-3 h-3 text-sky-400" />
                      <span className="hidden lg:inline">Cursor</span>
                    </button>
                  </>
                )}
              </div>
            ) : (
              <span className="px-2 py-0.5 text-xs text-slate-400 bg-white/[0.04] border border-white/[0.08] rounded-lg shrink-0">
                {t('workbench.sshPoolExhausted')}
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 max-w-full">
          <button
            type="button"
            onClick={openCommandPalette}
            title={isZh ? '打开命令面板 (⌘K)' : 'Command Palette (⌘K)'}
            aria-label={isZh ? '打开命令面板' : 'Command Palette'}
            className="mac-btn-secondary hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium"
          >
            <Command className="w-3.5 h-3.5 text-slate-300" />
            <kbd className="mac-kbd text-[10px]">⌘K</kbd>
          </button>

          <LanguageSwitcher />

          {/* Environment Snapshot Button */}
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              setSnapshotModalTab('save');
              setSnapshotStopAfterSave(false);
              setIsSnapshotModalOpen(true);
            }}
            className="flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 bg-indigo-500/20 hover:bg-indigo-500/30 border border-indigo-400/35 rounded-lg text-xs font-medium text-indigo-100 transition whitespace-nowrap"
          >
            <Camera className="w-3.5 h-3.5 text-indigo-300 shrink-0" />
            <span>{t('snapshots.buttonLabel')}</span>
          </button>

          {/* Cloud Storage Wizard Button */}
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              setIsStorageModalOpen(true);
            }}
            className="mac-btn-secondary flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap"
          >
            <Cloud className="w-3.5 h-3.5 text-sky-400 shrink-0" />
            <span>{t('workbench.cloudStorage')}</span>
          </button>

          {colabUrl && (
            <a
              href={colabUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => {
                if (macDesktop) {
                  e.preventDefault();
                  triggerHaptic('light');
                  openExternalUrl(colabUrl);
                }
              }}
              className="flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 border border-amber-400/35 rounded-lg text-xs font-medium text-amber-100 transition whitespace-nowrap"
            >
              <ExternalLink className="w-3.5 h-3.5 text-amber-300 shrink-0" />
              <span>{t('workbench.openInColab')}</span>
            </a>
          )}

          <button
            type="button"
            onClick={() => {
              triggerHaptic('medium');
              setIsStopModalOpen(true);
            }}
            className="flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 bg-rose-500/20 hover:bg-rose-500/30 border border-rose-400/35 rounded-lg text-xs font-medium text-rose-100 transition whitespace-nowrap"
          >
            <Square className="w-3.5 h-3.5 fill-current text-rose-300 shrink-0" />
            <span>{t('workbench.stopSession')}</span>
          </button>
        </div>
      </header>

      {/* Segmented Workbench Tabs Strip */}
      <div className="mac-glass border-b border-white/[0.08] px-3 sm:px-4 py-1.5 flex items-center justify-between gap-3 overflow-x-auto">
        <div className="mac-pill-group inline-flex items-center gap-1 p-1 rounded-xl">
          {tabs.map((tab) => {
            const isActive = activeTab === tab.id;
            return (
              <button
                type="button"
                key={tab.id}
                onClick={() => handleTabChange(tab.id)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition whitespace-nowrap shrink-0 ${
                  isActive
                    ? 'mac-pill-active text-white'
                    : 'text-slate-400 hover:text-slate-100 hover:bg-white/[0.04]'
                }`}
              >
                {tab.icon}
                <span>{tab.label}</span>
                <kbd className="mac-kbd text-[10px] opacity-75">{tab.shortcut}</kbd>
              </button>
            );
          })}
        </div>

        <div className="hidden md:flex items-center gap-2 text-[11px] text-slate-400 font-mono shrink-0">
          <span className="px-2 py-0.5 rounded-md bg-white/[0.04] border border-white/[0.07]">
            {session.remote_workdir || '/content'}
          </span>
        </div>
      </div>

      {/* Studio Canvas */}
      <div className="flex-1 overflow-hidden relative bg-slate-950/65">
        {activeTab === 'terminal' && <TerminalTab sessionId={session.id} />}
        {activeTab === 'files' && (
          <FileExplorerTab sessionId={session.id} onEditFile={handleOpenFileInEditor} />
        )}
        {activeTab === 'editor' &&
          (editingFile ? (
            <MonacoEditorTab sessionId={session.id} filePath={editingFile} />
          ) : (
            <div className="h-full flex items-center justify-center p-6">
              <div className="mac-card max-w-md w-full p-8 text-center space-y-4">
                <div className="w-12 h-12 mx-auto rounded-2xl bg-indigo-500/15 border border-indigo-400/25 flex items-center justify-center text-indigo-300">
                  <Code2 className="w-6 h-6" />
                </div>
                <p className="text-sm text-slate-300 leading-relaxed">
                  {t('workbench.selectFilePrompt')}
                </p>
                <button
                  type="button"
                  onClick={() => handleTabChange('files')}
                  className="mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium inline-flex items-center gap-2"
                >
                  <Folder className="w-3.5 h-3.5 text-sky-400" />
                  <span>{t('workbench.tabFiles')}</span>
                </button>
              </div>
            </div>
          ))}
        {activeTab === 'metrics' && (
          <div className="h-full overflow-y-auto p-6">
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

      {/* Environment Snapshot Save/Restore Modal */}
      {isSnapshotModalOpen && (
        <SnapshotModal
          session={session}
          initialTab={snapshotModalTab}
          initialStopAfterSave={snapshotStopAfterSave}
          initialRestoreSnapshotId={autoRestoreSnapshotId || undefined}
          autoStartRestore={Boolean(autoRestoreSnapshotId && snapshotModalTab === 'restore')}
          onCompleted={(task) => {
            if (task.action === 'restore') {
              onClearAutoRestore?.();
            }
            if (task.stop_after_save && task.status === 'COMPLETED') {
              setIsSnapshotModalOpen(false);
              onBack();
            }
          }}
          onClose={() => {
            setIsSnapshotModalOpen(false);
            onClearAutoRestore?.();
          }}
        />
      )}

      {/* Stop Session Confirmation Modal */}
      {isStopModalOpen && (
        <StopSessionModal
          session={session}
          onSaveAndStop={() => {
            setIsStopModalOpen(false);
            setSnapshotModalTab('save');
            setSnapshotStopAfterSave(true);
            setIsSnapshotModalOpen(true);
          }}
          onStopDirectly={async () => {
            setIsStopModalOpen(false);
            try {
              await onStop(session.id);
              triggerHaptic('success');
              notifyToast(
                isZh ? '实例已停止' : 'Runtime Stopped',
                session.name,
                'success'
              );
            } catch (err: any) {
              notifyToast(
                formatActionError('sessions.stopFailed', err?.message),
                undefined,
                'error'
              );
            }
          }}
          onClose={() => setIsStopModalOpen(false)}
        />
      )}
    </div>
  );
};
