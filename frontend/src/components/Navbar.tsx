import React from 'react';
import { LayoutDashboard, Users, Cpu, LogOut, Terminal, Layers, PlaySquare, Bell } from 'lucide-react';

interface Props {
  activePage: string;
  onNavigate: (page: string) => void;
  onLogout: () => void;
}

export const Navbar: React.FC<Props> = ({ activePage, onNavigate, onLogout }) => {
  const navItems = [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard },
    { id: 'sessions', label: 'Sessions', icon: Cpu },
    { id: 'jobs', label: 'Jobs', icon: PlaySquare },
    { id: 'accounts', label: 'Accounts', icon: Users },
    { id: 'templates', label: 'Templates & Secrets', icon: Layers },
    { id: 'notifications', label: 'Notifications', icon: Bell },
  ];

  return (
    <header className="h-14 bg-slate-800 border-b border-slate-750 flex items-center justify-between px-6 sticky top-0 z-40">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white shadow-md">
          <Terminal className="w-5 h-5" />
        </div>
        <div>
          <span className="font-bold text-white text-base tracking-tight">Colab Console</span>
          <span className="ml-2 text-xs font-mono bg-blue-900/60 text-blue-300 px-2 py-0.5 rounded border border-blue-700/50">
            Port 1113
          </span>
        </div>
      </div>

      <nav className="flex items-center gap-1">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activePage === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onNavigate(item.id)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition ${
                isActive
                  ? 'bg-blue-600 text-white'
                  : 'text-slate-300 hover:bg-slate-700/60 hover:text-white'
              }`}
            >
              <Icon className="w-4 h-4" />
              {item.label}
            </button>
          );
        })}
      </nav>

      <div className="flex items-center gap-3">
        <button
          onClick={onLogout}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-400 hover:text-red-400 hover:bg-red-950/30 border border-transparent hover:border-red-800/50 transition"
        >
          <LogOut className="w-3.5 h-3.5" />
          Sign Out
        </button>
      </div>
    </header>
  );
};
