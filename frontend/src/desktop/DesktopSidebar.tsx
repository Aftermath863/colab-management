import React from 'react';
import {
  Bell,
  Cpu,
  FileText,
  FolderOpen,
  Layers,
  LayoutDashboard,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  PlaySquare,
  Plus,
  Search,
  Terminal,
  Users,
  Zap,
} from 'lucide-react';
import { Account, Session } from '../types';
import { useI18n } from '../i18n';
import { LanguageSwitcher } from '../components/LanguageSwitcher';
import { isMacDesktop, revealInFinder, showDaemonLogs, triggerHaptic } from './macBridge';

interface DesktopSidebarProps {
  activePage: string;
  collapsed: boolean;
  onToggleCollapse: () => void;
  onNavigate: (page: string) => void;
  onOpenCommandPalette: () => void;
  onNewSession: () => void;
  onLogout: () => void;
  accounts: Account[];
  sessions: Session[];
}

export const DesktopSidebar: React.FC<DesktopSidebarProps> = ({
  activePage,
  collapsed,
  onToggleCollapse,
  onNavigate,
  onOpenCommandPalette,
  onNewSession,
  onLogout,
  accounts,
  sessions,
}) => {
  const { t, locale, toggleLocale } = useI18n();
  const isZh = locale === 'zh';
  const isMac = isMacDesktop();

  const totalCcu = accounts.reduce((sum, acc) => sum + (acc.ccu_balance || 0), 0);
  const totalBurnRate = accounts.reduce((sum, acc) => sum + (acc.burn_rate_hourly || 0), 0);
  const activeSessionsCount = sessions.filter(
    (s) => s.status !== 'STOPPED' && s.status !== 'FAILED' && s.status !== 'LOST'
  ).length;

  const poolCapacityReference = Math.max(100, accounts.length * 100);
  const ccuBarPercent = Math.min(100, Math.max(accounts.length > 0 ? 4 : 0, (totalCcu / poolCapacityReference) * 100));
  const ccuBarColor =
    totalCcu <= 5
      ? 'bg-rose-500'
      : totalCcu <= 20
        ? 'bg-amber-400'
        : 'bg-gradient-to-r from-emerald-400 to-cyan-400';

  const navItems = [
    { id: 'overview', label: t('nav.overview'), icon: LayoutDashboard, shortcut: '⌘1' },
    {
      id: 'sessions',
      label: t('nav.sessions'),
      icon: Cpu,
      shortcut: '⌘2',
      badge: activeSessionsCount > 0 ? activeSessionsCount : undefined,
    },
    { id: 'jobs', label: t('nav.jobs'), icon: PlaySquare, shortcut: '⌘3' },
    { id: 'accounts', label: t('nav.accounts'), icon: Users, shortcut: '⌘4' },
    { id: 'templates', label: t('nav.templates'), icon: Layers, shortcut: '⌘5' },
    { id: 'notifications', label: t('nav.notifications'), icon: Bell, shortcut: '⌘6' },
  ];

  return (
    <aside
      className={`hidden md:flex flex-col shrink-0 h-screen sticky top-0 z-30 mac-glass-heavy border-r border-white/[0.08] select-none transition-[width] duration-200 ease-out ${
        collapsed ? 'w-[68px]' : 'w-64'
      }`}
    >
      {/* Draggable Titlebar Safe Zone (clears macOS traffic lights when inside .app) */}
      <div
        data-mac-drag-region="true"
        className={`${isMac ? 'pt-9' : 'pt-4'} pb-3 px-3 border-b border-white/[0.06]`}
      >
        <div className={`flex items-center ${collapsed ? 'justify-center' : 'justify-between'} gap-2`}>
          {!collapsed && (
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-blue-500 via-indigo-500 to-cyan-500 flex items-center justify-center text-white shadow-glow-blue border border-white/20 shrink-0">
                <Terminal className="w-4 h-4" />
              </div>
              <div className="min-w-0">
                <div className="text-sm font-bold text-white tracking-tight truncate">
                  {t('nav.brand')}
                </div>
                <div className="flex items-center gap-1.5 text-[10px] font-mono text-slate-400">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  <span className="truncate">{t('nav.portBadge')}</span>
                </div>
              </div>
            </div>
          )}

          <button
            type="button"
            data-no-drag="true"
            onClick={() => {
              triggerHaptic('light');
              onToggleCollapse();
            }}
            title={isZh ? '折叠/展开侧边栏 (⌘\\)' : 'Toggle Sidebar (⌘\\)'}
            aria-label={isZh ? '折叠/展开侧边栏' : 'Toggle Sidebar'}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition-colors shrink-0"
          >
            {collapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Quick Search (⌘K) + Launch Trigger */}
      <div className="px-3 pt-3 pb-2 space-y-2">
        {collapsed ? (
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              onOpenCommandPalette();
            }}
            title={isZh ? '快速搜索 (⌘K)' : 'Quick Search (⌘K)'}
            aria-label="Quick Search"
            className="w-full h-9 rounded-xl bg-white/[0.04] hover:bg-white/[0.09] border border-white/[0.08] flex items-center justify-center text-slate-300 hover:text-white transition-colors"
          >
            <Search className="w-4 h-4" />
          </button>
        ) : (
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              onOpenCommandPalette();
            }}
            className="w-full px-3 py-2 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.08] hover:border-white/[0.14] flex items-center justify-between gap-2 text-xs text-slate-400 hover:text-slate-200 transition-all shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]"
          >
            <span className="flex items-center gap-2 truncate">
              <Search className="w-3.5 h-3.5 text-slate-400 shrink-0" />
              <span className="truncate">{isZh ? '快速搜索与命令...' : 'Quick Search...'}</span>
            </span>
            <kbd className="mac-kbd">⌘K</kbd>
          </button>
        )}

        <button
          type="button"
          onClick={() => {
            triggerHaptic('light');
            onNewSession();
          }}
          title={collapsed ? `${t('overview.launchRuntime')} (⌘N)` : undefined}
          className={`w-full mac-btn-primary rounded-xl text-xs font-semibold gap-2 ${
            collapsed ? 'h-9 px-0' : 'px-3 py-2 justify-between'
          }`}
        >
          <span className="flex items-center gap-2 truncate">
            <Plus className="w-3.5 h-3.5 shrink-0" />
            {!collapsed && <span className="truncate">{t('overview.launchRuntime')}</span>}
          </span>
          {!collapsed && (
            <kbd className="px-1.5 py-0.5 rounded bg-white/15 border border-white/20 font-mono text-[10px] leading-none">
              ⌘N
            </kbd>
          )}
        </button>
      </div>

      {/* Navigation Items */}
      <nav className="flex-1 overflow-y-auto px-2.5 py-2 space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activePage === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                triggerHaptic('light');
                onNavigate(item.id);
              }}
              title={collapsed ? `${item.label} (${item.shortcut})` : undefined}
              className={`group relative w-full flex items-center rounded-xl text-xs font-medium transition-all ${
                collapsed ? 'justify-center px-0 py-2.5' : 'justify-between px-3 py-2.5'
              } ${
                isActive
                  ? 'mac-pill-active text-white'
                  : 'text-slate-300/90 hover:text-white hover:bg-white/[0.06] border border-transparent'
              }`}
            >
              <span className="flex items-center gap-2.5 min-w-0">
                <Icon className={`w-4 h-4 shrink-0 ${isActive ? 'text-white' : 'text-slate-400 group-hover:text-slate-200'}`} />
                {!collapsed && <span className="truncate">{item.label}</span>}
              </span>

              {!collapsed && (
                <span className="flex items-center gap-1.5 shrink-0">
                  {item.badge !== undefined && (
                    <span
                      className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold tabular-nums ${
                        isActive
                          ? 'bg-white/20 text-white'
                          : 'bg-emerald-500/20 text-emerald-300 border border-emerald-400/25'
                      }`}
                    >
                      {item.badge}
                    </span>
                  )}
                  <kbd
                    className={`mac-kbd transition-opacity ${
                      isActive
                        ? 'opacity-80 bg-white/15 border-white/25 text-white'
                        : 'opacity-0 group-hover:opacity-75'
                    }`}
                  >
                    {item.shortcut}
                  </kbd>
                </span>
              )}

              {collapsed && item.badge !== undefined && (
                <span className="absolute top-1.5 right-2 w-2 h-2 rounded-full bg-emerald-400 ring-2 ring-slate-950" />
              )}
            </button>
          );
        })}
      </nav>

      {/* Bottom Fleet Telemetry Glass Capsule */}
      <div className="p-2.5 border-t border-white/[0.06] space-y-2">
        {collapsed ? (
          <div className="space-y-1.5">
            <div
              title={`${t('overview.poolCcuBalance')}: ${totalCcu.toFixed(1)} CCU · ${activeSessionsCount} GPU`}
              className="rounded-xl bg-white/[0.04] border border-white/[0.08] py-2 flex flex-col items-center gap-1"
            >
              <Zap className="w-3.5 h-3.5 text-amber-400" />
              <span className="text-[10px] font-mono font-semibold text-white tabular-nums">
                {totalCcu.toFixed(0)}
              </span>
            </div>

            {isMac && (
              <div className="flex flex-col gap-1">
                <button
                  type="button"
                  onClick={() => {
                    triggerHaptic('light');
                    revealInFinder('workspace');
                  }}
                  title={isZh ? '在访达中打开工作区' : 'Reveal Workspace in Finder'}
                  className="w-full h-8 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.07] flex items-center justify-center text-slate-300 hover:text-white transition-colors"
                >
                  <FolderOpen className="w-3.5 h-3.5 text-amber-400" />
                </button>
                <button
                  type="button"
                  onClick={() => {
                    triggerHaptic('light');
                    showDaemonLogs();
                  }}
                  title={isZh ? '守护进程日志 (⌘L)' : 'Daemon Logs (⌘L)'}
                  className="w-full h-8 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.07] flex items-center justify-center text-slate-300 hover:text-white transition-colors"
                >
                  <FileText className="w-3.5 h-3.5 text-sky-400" />
                </button>
              </div>
            )}

            <button
              type="button"
              onClick={() => {
                triggerHaptic('light');
                toggleLocale();
              }}
              title={t('common.languageSwitch')}
              className="w-full h-8 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.07] flex items-center justify-center text-[10px] font-semibold text-slate-300 hover:text-white transition-colors"
            >
              {locale === 'zh' ? '中' : 'EN'}
            </button>

            <button
              type="button"
              onClick={onLogout}
              title={t('nav.signOut')}
              aria-label={t('nav.signOut')}
              className="w-full h-8 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-rose-950/35 flex items-center justify-center transition-colors"
            >
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <>
            <div className="rounded-2xl bg-white/[0.035] border border-white/[0.08] p-3 space-y-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]">
              <div className="flex items-center justify-between text-[11px]">
                <span className="font-semibold text-slate-300 flex items-center gap-1.5">
                  <Zap className="w-3.5 h-3.5 text-amber-400" />
                  {isZh ? '算力遥测胶囊' : 'Fleet Telemetry'}
                </span>
                <span className="inline-flex items-center gap-1 text-[10px] font-mono font-semibold text-emerald-300 bg-emerald-500/15 border border-emerald-400/25 px-1.5 py-0.5 rounded-full tabular-nums">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  {activeSessionsCount} GPU
                </span>
              </div>

              <div>
                <div className="flex items-baseline justify-between text-xs mb-1">
                  <span className="font-bold text-white tabular-nums">{totalCcu.toFixed(1)} CCU</span>
                  <span className="text-[11px] font-mono text-slate-400 tabular-nums">
                    {totalBurnRate.toFixed(2)} CCU/h
                  </span>
                </div>
                <div className="h-1.5 w-full rounded-full bg-white/[0.07] overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${ccuBarColor}`}
                    style={{ width: `${ccuBarPercent}%` }}
                  />
                </div>
              </div>

              {isMac && (
                <div className="grid grid-cols-2 gap-1.5 pt-0.5">
                  <button
                    type="button"
                    onClick={() => {
                      triggerHaptic('light');
                      revealInFinder('workspace');
                    }}
                    className="mac-btn-secondary rounded-lg px-2 py-1.5 text-[11px] font-medium gap-1.5"
                  >
                    <FolderOpen className="w-3 h-3 text-amber-400 shrink-0" />
                    <span className="truncate">{isZh ? '访达' : 'Finder'}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      triggerHaptic('light');
                      showDaemonLogs();
                    }}
                    title={isZh ? '查看守护进程日志 (⌘L)' : 'Show Daemon Logs (⌘L)'}
                    className="mac-btn-secondary rounded-lg px-2 py-1.5 text-[11px] font-medium gap-1.5"
                  >
                    <FileText className="w-3 h-3 text-sky-400 shrink-0" />
                    <span className="truncate">{isZh ? '日志' : 'Logs'}</span>
                  </button>
                </div>
              )}
            </div>

            <div className="flex items-center justify-between gap-2 pt-0.5">
              <LanguageSwitcher />
              <button
                type="button"
                onClick={onLogout}
                title={t('nav.signOut')}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-slate-400 hover:text-rose-300 hover:bg-rose-950/35 border border-transparent hover:border-rose-500/30 transition-colors whitespace-nowrap"
              >
                <LogOut className="w-3.5 h-3.5 shrink-0" />
                <span>{t('nav.signOut')}</span>
              </button>
            </div>
          </>
        )}
      </div>
    </aside>
  );
};
