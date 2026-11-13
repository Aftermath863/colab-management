import React, { useState, useEffect } from 'react';
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
import { I18nProvider, Locale, safeReadStorage, useI18n } from './i18n';

const AppContent: React.FC = () => {
  const { formatActionError } = useI18n();
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(
    Boolean(safeReadStorage('token'))
  );
  const [activePage, setActivePage] = useState<string>('overview');
  const [selectedSession, setSelectedSession] = useState<Session | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [oauthFlow, setOauthFlow] = useState<{ authUrl: string; state?: string } | null>(null);
  const [showNewSessionModal, setShowNewSessionModal] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);

  const handleLogout = () => {
    api.logout();
    setIsAuthenticated(false);
    setSelectedSession(null);
  };

  useEffect(() => {
    const handleUnauthorized = () => {
      handleLogout();
    };
    window.addEventListener('auth:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('auth:unauthorized', handleUnauthorized);
  }, []);

  const refreshData = async () => {
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
  };

  useEffect(() => {
    if (isAuthenticated) {
      api.getMe().catch(() => {
        handleLogout();
      });
      refreshData();
      const interval = setInterval(refreshData, 10000);
      return () => clearInterval(interval);
    }
  }, [isAuthenticated]);

  const handleLoginSuccess = () => {
    setIsAuthenticated(true);
    setActivePage('overview');
  };

  const handleStartOAuth = async () => {
    try {
      const flow = await api.startOAuthFlow();
      setOauthFlow({ authUrl: flow.auth_url, state: flow.state });
    } catch (err: any) {
      alert(formatActionError('oauth.startFailed', err?.message));
    }
  };

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
  }) => {
    const sess = await api.createSession(params);
    await refreshData();
    setSelectedSession(sess);
  };

  const handleStopSession = async (sessionId: string) => {
    await api.stopSession(sessionId);
    await refreshData();
    if (selectedSession?.id === sessionId) {
      setSelectedSession(null);
    }
  };

  if (!isAuthenticated) {
    return <LoginPage onLoginSuccess={handleLoginSuccess} />;
  }

  if (selectedSession) {
    return (
      <SessionWorkbenchPage
        session={selectedSession}
        onBack={() => setSelectedSession(null)}
        onStop={handleStopSession}
      />
    );
  }

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 flex flex-col">
      <Navbar
        activePage={activePage}
        onNavigate={(page) => setActivePage(page)}
        onLogout={handleLogout}
      />

      <main className="flex-1">
        {activePage === 'overview' && (
          <OverviewPage
            accounts={accounts}
            sessions={sessions}
            onOpenSession={(sess) => setSelectedSession(sess)}
            onNewSession={() => setShowNewSessionModal(true)}
            onLinkAccount={handleStartOAuth}
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
          />
        )}

        {activePage === 'templates' && <TemplatesPage />}

        {activePage === 'notifications' && <NotificationsPage />}
      </main>

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
          onLaunch={handleLaunchSession}
          onClose={() => setShowNewSessionModal(false)}
        />
      )}
    </div>
  );
};

export const App: React.FC<{ initialLocale?: Locale }> = ({ initialLocale }) => (
  <I18nProvider initialLocale={initialLocale}>
    <AppContent />
  </I18nProvider>
);
