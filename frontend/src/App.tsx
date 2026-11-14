import React, { useState, useEffect, useRef, useCallback } from 'react';
import { LoginPage } from './pages/LoginPage';
import { OverviewPage } from './pages/OverviewPage';
import { AccountsPage } from './pages/AccountsPage';
import { SessionsPage } from './pages/SessionsPage';
import { SessionWorkbenchPage } from './pages/SessionWorkbenchPage';
import { TemplatesPage } from './pages/TemplatesPage';
import { JobsPage } from './pages/JobsPage';
import { NotificationsPage } from './pages/NotificationsPage';
import { Navbar } from './components/Navbar';
import { OAuthModal } from './components/OAuthModal';
import { NewSessionModal } from './components/NewSessionModal';
import { Account, Session } from './types';
import { api } from './services/api';
import { I18nProvider, Locale, safeReadStorage, safeWriteStorage, useI18n } from './i18n';
import {
  getMacRuntimeInfo,
  isInteractiveDragExcluded,
  isMacDesktop,
  onNativeCommand,
  showDaemonLogs,
  syncMenuBarSummary,
  triggerHaptic,
  windowAction,
} from './desktop/macBridge';
import { ToastProvider, useToast } from './desktop/ToastContext';
import { DesktopSidebar } from './desktop/DesktopSidebar';
import { CommandPalette } from './desktop/CommandPalette';

const SIDEBAR_STORAGE_KEY = 'colab_sidebar_collapsed';
const NAV_PAGE_ORDER = [
  'overview',
  'sessions',
  'jobs',
  'accounts',
  'templates',
  'notifications',
] as const;

const AppContent: React.FC = () => {
  const { locale, formatActionError } = useI18n();
  const isZh = locale === 'zh';
  const toast = useToast();

  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(
    Boolean(safeReadStorage('token'))
  );
  const [activePage, setActivePage] = useState<string>('overview');
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [oauthFlow, setOauthFlow] = useState<{ authUrl: string; state?: string } | null>(null);
  const [showNewSessionModal, setShowNewSessionModal] = useState<boolean>(false);
  const [initialSnapshotId, setInitialSnapshotId] = useState<string | undefined>(undefined);
  const [autoRestoreSnapshotId, setAutoRestoreSnapshotId] = useState<string | null>(null);
  const [isSyncingAccounts, setIsSyncingAccounts] = useState<boolean>(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(
    () => safeReadStorage(SIDEBAR_STORAGE_KEY) === '1'
  );
  const [commandPaletteOpen, setCommandPaletteOpen] = useState<boolean>(false);

  const syncingAccountsRef = useRef<boolean>(false);
  const sessionsRef = useRef<Session[]>(sessions);
  sessionsRef.current = sessions;

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      safeWriteStorage(SIDEBAR_STORAGE_KEY, next ? '1' : '0');
      return next;
    });
  }, []);

  // Apply html.is-mac-app class and auto-auth token when running inside native macOS app
  useEffect(() => {
    if (typeof document !== 'undefined' && isMacDesktop()) {
      document.documentElement.classList.add('is-mac-app');
    }
    const info = getMacRuntimeInfo();
    if (info.isMacApp && info.autoAuthToken && !safeReadStorage('token')) {
      safeWriteStorage('token', info.autoAuthToken);
      setIsAuthenticated(true);
    }
  }, []);

  // Native window drag & double-click zoom support for [data-mac-drag-region="true"]
  useEffect(() => {
    if (typeof window === 'undefined' || !isMacDesktop()) return;

    const handleMouseDown = (e: MouseEvent) => {
      if (e.button !== 0 || e.defaultPrevented) return;
      const target = e.target;
      if (!(target instanceof Element)) return;
      if (!target.closest('[data-mac-drag-region="true"]')) return;
      if (isInteractiveDragExcluded(target)) return;
      windowAction('startDrag');
    };

    const handleDoubleClick = (e: MouseEvent) => {
      if (e.defaultPrevented) return;
      const target = e.target;
      if (!(target instanceof Element)) return;
      if (!target.closest('[data-mac-drag-region="true"]')) return;
      if (isInteractiveDragExcluded(target)) return;
      windowAction('zoom');
    };

    window.addEventListener('mousedown', handleMouseDown);
    window.addEventListener('dblclick', handleDoubleClick);
    return () => {
      window.removeEventListener('mousedown', handleMouseDown);
      window.removeEventListener('dblclick', handleDoubleClick);
    };
  }, []);

  const handleLogout = useCallback(() => {
    api.logout();
    setIsAuthenticated(false);
    setSelectedSession(null);
    setCommandPaletteOpen(false);
    setAccounts([]);
    setSessions([]);
    syncMenuBarSummary(0, 0, 0);
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const handleUnauthorized = () => {
      handleLogout();
    };
    window.addEventListener('auth:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('auth:unauthorized', handleUnauthorized);
  }, [handleLogout]);

  const refreshData = useCallback(async () => {
    if (!isAuthenticated) return;
    try {
      const [accs, sess] = await Promise.all([api.getAccounts(), api.getSessions()]);
      setAccounts(accs);
      setSessions(sess);
    } catch (err: any) {
      console.error('Failed to load fleet state:', err);
      if (err?.message?.toLowerCase().includes('authenticated') || !safeReadStorage('token')) {
        handleLogout();
      }
    }
  }, [isAuthenticated, handleLogout]);

  const syncPoolAccounts = useCallback(
    async (silent = true): Promise<void> => {
      if (!isAuthenticated || syncingAccountsRef.current) return;
      syncingAccountsRef.current = true;
      setIsSyncingAccounts(true);
      try {
        const updatedAccounts = await api.syncAllAccounts();
        setAccounts(updatedAccounts);
        const sess = await api.getSessions();
        setSessions(sess);
        if (!silent) {
          toast.pushToast({
            variant: 'success',
            title: isZh ? '账号配额已同步' : 'Fleet quotas synchronized',
            description: isZh
              ? `已更新 ${updatedAccounts.length} 个 Google 账号 CCU 状态`
              : `Updated CCU state across ${updatedAccounts.length} account${updatedAccounts.length === 1 ? '' : 's'}`,
          });
        }
      } catch (err: any) {
        if (!silent) {
          toast.pushToast({
            variant: 'error',
            title: formatActionError('overview.syncQuotaFailed', err?.message),
          });
        } else {
          console.error('Background CCU sync failed:', err);
        }
      } finally {
        syncingAccountsRef.current = false;
        setIsSyncingAccounts(false);
      }
    },
    [isAuthenticated, formatActionError, isZh, toast]
  );

  // Sync live fleet summary to macOS Menu Bar HUD
  useEffect(() => {
    if (!isAuthenticated) {
      syncMenuBarSummary(0, 0, 0);
      return;
    }
    const activeRuntimes = sessions.filter(
      (s) => s.status !== 'STOPPED' && s.status !== 'FAILED' && s.status !== 'LOST'
    ).length;
    const totalCcu = accounts.reduce((sum, a) => sum + (a.ccu_balance || 0), 0);
    const burnRate = accounts.reduce((sum, a) => sum + (a.burn_rate_hourly || 0), 0);
    syncMenuBarSummary(activeRuntimes, totalCcu, burnRate);
  }, [isAuthenticated, accounts, sessions]);

  useEffect(() => {
    if (isAuthenticated) {
      api.getMe().catch(() => {
        handleLogout();
      });
      void refreshData();
      void syncPoolAccounts(true);
      const pollInterval = setInterval(() => void refreshData(), 10000);
      const syncInterval = setInterval(() => {
        if (typeof document === 'undefined' || !document.hidden) {
          void syncPoolAccounts(true);
        }
      }, 60000);
      return () => {
        clearInterval(pollInterval);
        clearInterval(syncInterval);
      };
    }
  }, [isAuthenticated, handleLogout, refreshData, syncPoolAccounts]);

  const handleLoginSuccess = () => {
    triggerHaptic('success');
    setIsAuthenticated(true);
    setActivePage('overview');
  };

  const handleStartOAuth = useCallback(async () => {
    try {
      const flow = await api.startOAuthFlow();
      setOauthFlow({ authUrl: flow.auth_url, state: flow.state });
    } catch (err: any) {
      toast.pushToast({
        variant: 'error',
        title: formatActionError('oauth.startFailed', err?.message),
      });
    }
  }, [formatActionError, toast]);

  const handleCompleteOAuth = async (code: string) => {
    await api.completeOAuth(code, oauthFlow?.state);
    await refreshData();
  };

  const handleLaunchSession = async (params: {
    name: string;
    accelerator: string;
    shape: string;
    lifecycle_policy: string;
    account_id?: string;
    template_id?: string;
    snapshot_id?: string;
  }) => {
    const sess = await api.createSession(params);
    await refreshData();
    void syncPoolAccounts(true);
    setAutoRestoreSnapshotId(params.snapshot_id || null);
    setSelectedSession(sess);
  };

  const handleStopSession = async (sessionId: string) => {
    await api.stopSession(sessionId);
    await refreshData();
    void syncPoolAccounts(true);
    if (selectedSession?.id === sessionId) {
      setSelectedSession(null);
    }
    toast.pushToast({
      variant: 'info',
      title: isZh ? '计算实例已停止' : 'Runtime stopped',
    });
  };

  // Global keyboard shortcuts (⌘K, ⌘N, ⇧⌘R, ⌘1..⌘6, ⌘\, ⌘L)
  useEffect(() => {
    if (!isAuthenticated || typeof window === 'undefined') return;

    const handleKeyDown = (e: KeyboardEvent) => {
      const isMacPlatform =
        typeof navigator !== 'undefined' &&
        /Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent || '');
      const mod = isMacPlatform
        ? e.metaKey && !e.ctrlKey && !e.altKey
        : (e.metaKey || e.ctrlKey) && !e.altKey;

      if (!mod) return;

      const keyLower = e.key.toLowerCase();

      if (keyLower === 'k' && !e.shiftKey) {
        e.preventDefault();
        triggerHaptic('light');
        setCommandPaletteOpen((prev) => !prev);
        return;
      }

      if (e.key === '\\' && !e.shiftKey) {
        e.preventDefault();
        triggerHaptic('light');
        toggleSidebar();
        return;
      }

      if (keyLower === 'n' && !e.shiftKey) {
        e.preventDefault();
        triggerHaptic('light');
        setShowNewSessionModal(true);
        return;
      }

      if (keyLower === 'r' && e.shiftKey) {
        e.preventDefault();
        triggerHaptic('light');
        void syncPoolAccounts(false);
        return;
      }

      if (keyLower === 'l' && !e.shiftKey && isMacDesktop()) {
        e.preventDefault();
        triggerHaptic('light');
        showDaemonLogs();
        return;
      }

      if (!e.shiftKey && ['1', '2', '3', '4', '5', '6'].includes(e.key)) {
        const target = NAV_PAGE_ORDER[Number(e.key) - 1];
        if (target) {
          e.preventDefault();
          triggerHaptic('light');
          setSelectedSession(null);
          setActivePage(target);
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isAuthenticated, toggleSidebar, syncPoolAccounts]);

  // Listen to custom 'colab:open-command-palette' event
  useEffect(() => {
    if (!isAuthenticated || typeof window === 'undefined') return;
    const handleOpenPalette = () => {
      triggerHaptic('light');
      setCommandPaletteOpen(true);
    };
    window.addEventListener('colab:open-command-palette', handleOpenPalette);
    return () => window.removeEventListener('colab:open-command-palette', handleOpenPalette);
  }, [isAuthenticated]);

  // Listen to native Swift -> JS commands ('colab:native-command')
  useEffect(() => {
    if (!isAuthenticated) return;
    return onNativeCommand((detail) => {
      const { command, payload } = detail;
      switch (command) {
        case 'command-palette':
          setCommandPaletteOpen((prev) => !prev);
          break;
        case 'new-session':
          setShowNewSessionModal(true);
          break;
        case 'sync-accounts':
          void syncPoolAccounts(false);
          break;
        case 'link-account':
          void handleStartOAuth();
          break;
        case 'navigate': {
          const targetPage =
            typeof payload === 'string'
              ? payload
              : typeof payload?.page === 'string'
                ? payload.page
                : null;
          if (targetPage) {
            setSelectedSession(null);
            setActivePage(targetPage);
          }
          break;
        }
        case 'open-session': {
          const targetId =
            typeof payload === 'string'
              ? payload
              : typeof payload?.sessionId === 'string'
                ? payload.sessionId
                : typeof payload?.id === 'string'
                  ? payload.id
                  : null;
          if (targetId) {
            const existing = sessionsRef.current.find((s) => s.id === targetId);
            if (existing) {
              setSelectedSession(existing);
            } else {
              void api
                .getSessions()
                .then((list) => {
                  setSessions(list);
                  const found = list.find((s) => s.id === targetId);
                  if (found) setSelectedSession(found);
                })
                .catch(() => {});
            }
          }
          break;
        }
        default:
          break;
      }
    });
  }, [isAuthenticated, syncPoolAccounts, handleStartOAuth]);

  if (!isAuthenticated) {
    return <LoginPage onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <>
      {selectedSession ? (
        <div className="min-h-screen mac-ambient-bg text-slate-100">
          <SessionWorkbenchPage
            session={selectedSession}
            autoRestoreSnapshotId={autoRestoreSnapshotId}
            onClearAutoRestore={() => setAutoRestoreSnapshotId(null)}
            onBack={() => {
              setSelectedSession(null);
              setAutoRestoreSnapshotId(null);
              void refreshData();
            }}
            onStop={handleStopSession}
          />
        </div>
      ) : (
        <div className="min-h-screen mac-ambient-bg text-slate-100 flex flex-col md:flex-row">
          <DesktopSidebar
            activePage={activePage}
            collapsed={sidebarCollapsed}
            onToggleCollapse={toggleSidebar}
            onNavigate={(page) => setActivePage(page)}
            onOpenCommandPalette={() => setCommandPaletteOpen(true)}
            onNewSession={() => setShowNewSessionModal(true)}
            onLogout={handleLogout}
            accounts={accounts}
            sessions={sessions}
          />

          <div className="md:hidden">
            <Navbar
              activePage={activePage}
              onNavigate={(page) => setActivePage(page)}
              onLogout={handleLogout}
              onOpenCommandPalette={() => setCommandPaletteOpen(true)}
            />
          </div>

          <main className="flex-1 min-w-0 overflow-y-auto">
            <div key={activePage} className="mac-page-enter">
              {activePage === 'overview' && (
                <OverviewPage
                  accounts={accounts}
                  sessions={sessions}
                  onOpenSession={(sess) => setSelectedSession(sess)}
                  onNewSession={() => setShowNewSessionModal(true)}
                  onLinkAccount={handleStartOAuth}
                  onSyncAccounts={() => syncPoolAccounts(false)}
                  isSyncing={isSyncingAccounts}
                />
              )}

              {activePage === 'sessions' && (
                <SessionsPage
                  sessions={sessions}
                  onOpenSession={(sess) => setSelectedSession(sess)}
                  onNewSession={() => setShowNewSessionModal(true)}
                  onStopSession={handleStopSession}
                  onRefresh={refreshData}
                />
              )}

              {activePage === 'jobs' && <JobsPage />}

              {activePage === 'accounts' && (
                <AccountsPage
                  accounts={accounts}
                  onRefresh={refreshData}
                  onLinkAccount={handleStartOAuth}
                  onSyncAll={() => syncPoolAccounts(false)}
                  isSyncingAll={isSyncingAccounts}
                />
              )}

              {activePage === 'templates' && (
                <TemplatesPage
                  sessions={sessions}
                  onLaunchFromSnapshot={(snap) => {
                    setInitialSnapshotId(snap.id);
                    setShowNewSessionModal(true);
                  }}
                />
              )}

              {activePage === 'notifications' && <NotificationsPage />}
            </div>
          </main>
        </div>
      )}

      <CommandPalette
        isOpen={commandPaletteOpen}
        onClose={() => setCommandPaletteOpen(false)}
        activePage={activePage}
        sessions={sessions}
        onNavigate={(page) => {
          setSelectedSession(null);
          setActivePage(page);
        }}
        onNewSession={() => setShowNewSessionModal(true)}
        onSyncAccounts={() => {
          void syncPoolAccounts(false);
        }}
        onLinkAccount={() => {
          void handleStartOAuth();
        }}
        onOpenSession={(sess) => setSelectedSession(sess)}
        onLogout={handleLogout}
      />

      {oauthFlow && (
        <OAuthModal
          authUrl={oauthFlow.authUrl}
          onComplete={handleCompleteOAuth}
          onClose={() => setOauthFlow(null)}
        />
      )}

      {showNewSessionModal && (
        <NewSessionModal
          accounts={accounts}
          initialSnapshotId={initialSnapshotId}
          onLaunch={handleLaunchSession}
          onClose={() => {
            setShowNewSessionModal(false);
            setInitialSnapshotId(undefined);
          }}
        />
      )}
    </>
  );
};

export const App: React.FC<{ initialLocale?: Locale }> = ({ initialLocale }) => (
  <I18nProvider initialLocale={initialLocale}>
    <ToastProvider>
      <AppContent />
    </ToastProvider>
  </I18nProvider>
);
