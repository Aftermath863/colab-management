import React from 'react';
import { LayoutDashboard, Users, Cpu, LogOut, Terminal, Layers, PlaySquare, Bell, Search } from 'lucide-react';
import { useI18n } from '../i18n';
import { LanguageSwitcher } from './LanguageSwitcher';
import { isMacDesktop, triggerHaptic } from '../desktop/macBridge';

interface Props {
  activePage: string;
  onNavigate: (page: string) => void;
  onLogout: () => void;
  onOpenCommandPalette?: () => void;
}

export const Navbar: React.FC<Props> = ({
  activePage,
  onNavigate,
  onLogout,
  onOpenCommandPalette,
}) => {
  const { t } = useI18n();
  const isMac = isMacDesktop();

  const navItems = [
    { id: 'overview', label: t('nav.overview'), icon: LayoutDashboard },
    { id: 'sessions', label: t('nav.sessions'), icon: Cpu },
    { id: 'jobs', label: t('nav.jobs'), icon: PlaySquare },
    { id: 'accounts', label: t('nav.accounts'), icon: Users },
    { id: 'templates', label: t('nav.templates'), icon: Layers },
    { id: 'notifications', label: t('nav.notifications'), icon: Bell },
  ];

  return (
    <header
      data-mac-drag-region="true"
      className={`mac-glass-heavy border-b border-white/[0.08] px-3 sm:px-6 py-2 lg:py-0 lg:h-14 flex flex-wrap lg:flex-nowrap items-center justify-between gap-2 lg:gap-4 sticky top-0 z-40 ${
        isMac ? 'pl-20' : ''
      }`}
    >
      <div className="flex items-center gap-2 sm:gap-3 shrink-0">
        <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center text-white shadow-glow-blue border border-white/20 shrink-0">
          <Terminal className="w-4 h-4" />
        </div>
        <div className="whitespace-nowrap flex items-center">
          <span className="font-bold text-white text-sm sm:text-base tracking-tight">{t('nav.brand')}</span>
          <span className="hidden sm:inline-block ml-2 text-xs font-mono bg-blue-500/15 text-blue-300 px-2 py-0.5 rounded-md border border-blue-400/25">
            {t('nav.portBadge')}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2 sm:gap-3 shrink-0 lg:order-3">
        {onOpenCommandPalette && (
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              onOpenCommandPalette();
            }}
            className="mac-btn-secondary px-2.5 py-1.5 rounded-lg text-xs gap-1.5"
            title="⌘K"
          >
            <Search className="w-3.5 h-3.5 text-slate-300" />
            <kbd className="mac-kbd">⌘K</kbd>
          </button>
        )}
        <LanguageSwitcher />
        <button
          type="button"
          onClick={onLogout}
          title={t('nav.signOut')}
          className="flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-lg text-xs font-medium text-slate-400 hover:text-rose-300 hover:bg-rose-950/35 border border-transparent hover:border-rose-500/30 transition whitespace-nowrap"
        >
          <LogOut className="w-3.5 h-3.5 shrink-0" />
          <span className="hidden sm:inline">{t('nav.signOut')}</span>
        </button>
      </div>

      <nav className="w-full lg:w-auto lg:flex-1 lg:order-2 flex items-center lg:justify-center gap-1 overflow-x-auto pt-1.5 lg:pt-0 border-t border-white/[0.06] lg:border-t-0">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activePage === item.id;
          return (
            <button
              type="button"
              key={item.id}
              onClick={() => {
                triggerHaptic('light');
                onNavigate(item.id);
              }}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs sm:text-sm font-medium transition whitespace-nowrap shrink-0 ${
                isActive
                  ? 'mac-pill-active text-white'
                  : 'text-slate-300 hover:bg-white/[0.06] hover:text-white border border-transparent'
              }`}
            >
              <Icon className="w-4 h-4 shrink-0" />
              {item.label}
            </button>
          );
        })}
      </nav>
    </header>
  );
};
