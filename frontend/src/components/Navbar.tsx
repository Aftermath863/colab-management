import React from 'react';
import { LayoutDashboard, Users, Cpu, LogOut, Terminal, Layers, PlaySquare, Bell } from 'lucide-react';
import { useI18n } from '../i18n';
import { LanguageSwitcher } from './LanguageSwitcher';

interface Props {
  activePage: string;
  onNavigate: (page: string) => void;
  onLogout: () => void;
}

export const Navbar: React.FC<Props> = ({ activePage, onNavigate, onLogout }) => {
  const { t } = useI18n();

  const navItems = [
    { id: 'overview', label: t('nav.overview'), icon: LayoutDashboard },
    { id: 'sessions', label: t('nav.sessions'), icon: Cpu },
    { id: 'jobs', label: t('nav.jobs'), icon: PlaySquare },
    { id: 'accounts', label: t('nav.accounts'), icon: Users },
    { id: 'templates', label: t('nav.templates'), icon: Layers },
    { id: 'notifications', label: t('nav.notifications'), icon: Bell },
  ];

  return (
    <header className="bg-slate-800 border-b border-slate-750 px-3 sm:px-6 py-2 lg:py-0 lg:h-14 flex flex-wrap lg:flex-nowrap items-center justify-between gap-2 lg:gap-4 sticky top-0 z-40">
      <div className="flex items-center gap-2 sm:gap-3 shrink-0">
        <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white shadow-md shrink-0">
          <Terminal className="w-5 h-5" />
        </div>
        <div className="whitespace-nowrap flex items-center">
          <span className="font-bold text-white text-sm sm:text-base tracking-tight">{t('nav.brand')}</span>
          <span className="hidden sm:inline-block ml-2 text-xs font-mono bg-blue-900/60 text-blue-300 px-2 py-0.5 rounded border border-blue-700/50">
            {t('nav.portBadge')}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2 sm:gap-3 shrink-0 lg:order-3">
        <LanguageSwitcher />
        <button
          type="button"
          onClick={onLogout}
          title={t('nav.signOut')}
          className="flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-lg text-xs font-medium text-slate-400 hover:text-red-400 hover:bg-red-950/30 border border-transparent hover:border-red-800/50 transition whitespace-nowrap"
        >
          <LogOut className="w-3.5 h-3.5 shrink-0" />
          <span className="hidden sm:inline">{t('nav.signOut')}</span>
        </button>
      </div>

      <nav className="w-full lg:w-auto lg:flex-1 lg:order-2 flex items-center lg:justify-center gap-1 overflow-x-auto pt-1.5 lg:pt-0 border-t border-slate-700/50 lg:border-t-0">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activePage === item.id;
          return (
            <button
              type="button"
              key={item.id}
              onClick={() => onNavigate(item.id)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition whitespace-nowrap shrink-0 ${
                isActive
                  ? 'bg-blue-600 text-white'
                  : 'text-slate-300 hover:bg-slate-700/60 hover:text-white'
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
