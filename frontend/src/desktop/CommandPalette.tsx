import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Bell,
  Code2,
  Copy,
  Cpu,
  FileText,
  FolderOpen,
  Globe,
  Layers,
  LayoutDashboard,
  LogOut,
  Play,
  PlaySquare,
  RefreshCw,
  Search,
  Terminal,
  UserPlus,
  Users,
} from 'lucide-react';
import { Session } from '../types';
import { useI18n } from '../i18n';
import {
  copyTextToClipboard,
  isMacDesktop,
  openIDE,
  openTerminalSSH,
  revealInFinder,
  showDaemonLogs,
  triggerHaptic,
} from './macBridge';
import { useToast } from './ToastContext';

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
  activePage: string;
  sessions: Session[];
  onNavigate: (page: string) => void;
  onNewSession: () => void;
  onSyncAccounts: () => void;
  onLinkAccount: () => void;
  onOpenSession: (session: Session) => void;
  onLogout: () => void;
  isMac?: boolean;
}

type CommandGroupId = 'actions' | 'runtimes' | 'navigation' | 'preferences';

interface CommandItem {
  id: string;
  group: CommandGroupId;
  title: string;
  subtitle?: string;
  shortcut?: string;
  badge?: string;
  keywords: string;
  icon: React.FC<{ className?: string }>;
  accentClass?: string;
  run: () => void;
}

function fuzzyScore(query: string, target: string): number {
  const q = query.trim().toLowerCase();
  if (!q) return 1;
  const hay = target.toLowerCase();

  const exactIdx = hay.indexOf(q);
  if (exactIdx !== -1) {
    return 100 - exactIdx * 0.25 + (exactIdx === 0 ? 30 : 0);
  }

  let qi = 0;
  let score = 0;
  let consecutive = 0;
  for (let hi = 0; hi < hay.length && qi < q.length; hi += 1) {
    if (hay[hi] === q[qi]) {
      qi += 1;
      consecutive += 1;
      score += 4 + consecutive * 3;
    } else {
      consecutive = 0;
    }
  }
  return qi === q.length ? score : 0;
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({
  isOpen,
  onClose,
  activePage,
  sessions,
  onNavigate,
  onNewSession,
  onSyncAccounts,
  onLinkAccount,
  onOpenSession,
  onLogout,
  isMac = isMacDesktop(),
}) => {
  const { t, locale, toggleLocale } = useI18n();
  const isZh = locale === 'zh';
  const toast = useToast();

  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    if (isOpen) {
      setQuery('');
      setSelectedIndex(0);
      if (typeof window !== 'undefined') {
        window.requestAnimationFrame(() => {
          inputRef.current?.focus();
        });
      }
    }
  }, [isOpen]);

  const activeSessions = useMemo(
    () =>
      sessions.filter(
        (s) => s.status !== 'STOPPED' && s.status !== 'FAILED' && s.status !== 'LOST'
      ),
    [sessions]
  );

  const items = useMemo<CommandItem[]>(() => {
    const list: CommandItem[] = [
      {
        id: 'action-launch',
        group: 'actions',
        title: t('overview.launchRuntime'),
        subtitle: isZh ? '分配新的 Colab GPU / TPU 计算实例' : 'Provision a new Colab GPU / TPU runtime',
        shortcut: '⌘N',
        keywords: 'launch new runtime session gpu tpu create 创建 实例 启动',
        icon: Play,
        accentClass: 'text-blue-400',
        run: () => onNewSession(),
      },
      {
        id: 'action-sync',
        group: 'actions',
        title: t('overview.refreshQuota'),
        subtitle: isZh ? '同步所有 Google 账号 CCU 配额与消耗速率' : 'Sync CCU balances & burn rates across accounts',
        shortcut: '⇧⌘R',
        keywords: 'sync refresh quota ccu accounts 同步 配额 刷新',
        icon: RefreshCw,
        accentClass: 'text-emerald-400',
        run: () => onSyncAccounts(),
      },
      {
        id: 'action-link',
        group: 'actions',
        title: t('overview.linkAccount'),
        subtitle: isZh ? '绑定新的 Google 账号到算力池' : 'Connect a new Google account to the compute pool',
        keywords: 'link oauth google account connect 绑定 账号 授权',
        icon: UserPlus,
        accentClass: 'text-indigo-400',
        run: () => onLinkAccount(),
      },
    ];

    if (isMac) {
      list.push(
        {
          id: 'action-finder',
          group: 'actions',
          title: isZh ? '在访达中显示工作区' : 'Reveal Workspace in Finder',
          subtitle: isZh ? '打开本地 ColabManager 工作目录' : 'Open local ColabManager workspace folder',
          keywords: 'finder reveal workspace folder directory open 访达 目录 工作区',
          icon: FolderOpen,
          accentClass: 'text-amber-400',
          run: () => {
            revealInFinder('workspace');
          },
        },
        {
          id: 'action-logs',
          group: 'actions',
          title: isZh ? '查看守护进程日志' : 'Daemon Logs',
          subtitle: isZh ? '打开本地 FastAPI 后端实时日志窗口' : 'Open native backend daemon log tail window',
          shortcut: '⌘L',
          keywords: 'daemon logs tail debug backend console 日志 守护进程',
          icon: FileText,
          accentClass: 'text-sky-400',
          run: () => {
            showDaemonLogs();
          },
        }
      );
    }

    for (const sess of activeSessions) {
      list.push({
        id: `runtime-open-${sess.id}`,
        group: 'runtimes',
        title: `${isZh ? '打开工作台' : 'Open Workbench'}: ${sess.name}`,
        subtitle: `${sess.accelerator} · ${sess.account_email || sess.account_id}${
          sess.ssh_local_port ? ` · localhost:${sess.ssh_local_port}` : ''
        }`,
        badge: sess.accelerator,
        keywords: `workbench runtime session ${sess.name} ${sess.accelerator} ${sess.account_email || ''} ${sess.ssh_local_port || ''} 工作台`,
        icon: Activity,
        accentClass: 'text-emerald-400',
        run: () => onOpenSession(sess),
      });

      if (sess.ssh_local_port) {
        const port = sess.ssh_local_port;
        const sshCmd = `ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p ${port} root@localhost`;
        list.push({
          id: `runtime-copy-ssh-${sess.id}`,
          group: 'runtimes',
          title: `${isZh ? '复制 SSH 命令' : 'Copy SSH'}: ${sess.name}`,
          subtitle: `ssh -p ${port} root@localhost`,
          keywords: `copy ssh port ${port} ${sess.name} ${sess.accelerator} 复制`,
          icon: Copy,
          accentClass: 'text-cyan-400',
          run: () => {
            void copyTextToClipboard(sshCmd).then((ok) => {
              if (ok) {
                toast.pushToast({
                  variant: 'success',
                  title: isZh ? '已复制 SSH 连接命令' : 'Copied SSH command',
                  description: `ssh -p ${port} root@localhost`,
                });
              }
            });
          },
        });

        if (isMac) {
          list.push({
            id: `runtime-term-${sess.id}`,
            group: 'runtimes',
            title: `${isZh ? '在 Terminal.app 中打开' : 'Open in Terminal.app'}: ${sess.name}`,
            subtitle: `localhost:${port} · ${sess.accelerator}`,
            keywords: `terminal app ssh ${sess.name} ${port} ${sess.accelerator} 终端`,
            icon: Terminal,
            accentClass: 'text-amber-400',
            run: () => {
              openTerminalSSH(port, sess.name, sshCmd);
              toast.pushToast({
                variant: 'info',
                title: isZh ? '正在打开 Terminal.app' : 'Opening Terminal.app',
                description: `${sess.name} (localhost:${port})`,
              });
            },
          });
        }

        list.push({
          id: `runtime-cursor-${sess.id}`,
          group: 'runtimes',
          title: `${isZh ? '在 Cursor 中打开' : 'Open in Cursor'}: ${sess.name}`,
          subtitle: `ssh-remote · localhost:${port}/content`,
          keywords: `cursor ide ssh remote ${sess.name} ${port} ${sess.accelerator}`,
          icon: Code2,
          accentClass: 'text-purple-400',
          run: () => {
            openIDE('cursor', port, '/content');
            toast.pushToast({
              variant: 'info',
              title: isZh ? '正在启动 Cursor Remote-SSH' : 'Launching Cursor Remote-SSH',
              description: `${sess.name} (localhost:${port})`,
            });
          },
        });
      }
    }

    const navPages: Array<{
      id: string;
      label: string;
      shortcut: string;
      icon: React.FC<{ className?: string }>;
      keywords: string;
    }> = [
      { id: 'overview', label: t('nav.overview'), shortcut: '⌘1', icon: LayoutDashboard, keywords: 'overview home dashboard 总览 首页' },
      { id: 'sessions', label: t('nav.sessions'), shortcut: '⌘2', icon: Cpu, keywords: 'sessions runtimes instances gpu 算力 实例' },
      { id: 'jobs', label: t('nav.jobs'), shortcut: '⌘3', icon: PlaySquare, keywords: 'jobs batch async training 异步 任务' },
      { id: 'accounts', label: t('nav.accounts'), shortcut: '⌘4', icon: Users, keywords: 'accounts google pool quota 账号 池' },
      { id: 'templates', label: t('nav.templates'), shortcut: '⌘5', icon: Layers, keywords: 'templates secrets snapshots vault 模板 密钥 快照' },
      { id: 'notifications', label: t('nav.notifications'), shortcut: '⌘6', icon: Bell, keywords: 'notifications webhooks alerts 告警 通知' },
    ];

    for (const nav of navPages) {
      list.push({
        id: `nav-${nav.id}`,
        group: 'navigation',
        title: nav.label,
        subtitle:
          activePage === nav.id
            ? isZh
              ? '当前所在页面'
              : 'Current view'
            : isZh
              ? '跳转至页面'
              : 'Switch view',
        shortcut: nav.shortcut,
        keywords: `navigate go to ${nav.label} ${nav.keywords}`,
        icon: nav.icon,
        accentClass: activePage === nav.id ? 'text-blue-400' : 'text-slate-300',
        run: () => onNavigate(nav.id),
      });
    }

    list.push(
      {
        id: 'pref-language',
        group: 'preferences',
        title: isZh ? '切换界面语言 (中文 / EN)' : 'Toggle Language (中文 / EN)',
        subtitle: isZh ? '当前：简体中文 → 切换为 English' : 'Current: English → Switch to 简体中文',
        keywords: 'language locale zh en chinese english 语言 切换 中文 英文',
        icon: Globe,
        accentClass: 'text-sky-400',
        run: () => {
          const nextZh = locale !== 'zh';
          toggleLocale();
          toast.pushToast({
            variant: 'info',
            title: nextZh ? '已切换为简体中文' : 'Switched language to English',
          });
        },
      },
      {
        id: 'pref-logout',
        group: 'preferences',
        title: t('nav.signOut'),
        subtitle: isZh ? '锁定控制台并返回登录验证页' : 'Lock console and return to authentication vault',
        keywords: 'sign out logout lock exit 退出 登录 锁定',
        icon: LogOut,
        accentClass: 'text-rose-400',
        run: () => onLogout(),
      }
    );

    return list;
  }, [
    t,
    isZh,
    isMac,
    activeSessions,
    activePage,
    locale,
    toggleLocale,
    onNewSession,
    onSyncAccounts,
    onLinkAccount,
    onOpenSession,
    onNavigate,
    onLogout,
    toast,
  ]);

  const filteredItems = useMemo(() => {
    if (!query.trim()) return items;
    return items
      .map((item) => ({
        item,
        score: fuzzyScore(query, `${item.title} ${item.subtitle || ''} ${item.keywords}`),
      }))
      .filter((entry) => entry.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((entry) => entry.item);
  }, [items, query]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  useEffect(() => {
    if (selectedIndex >= filteredItems.length) {
      setSelectedIndex(Math.max(0, filteredItems.length - 1));
    }
  }, [filteredItems.length, selectedIndex]);

  useEffect(() => {
    const el = itemRefs.current[selectedIndex];
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedIndex]);

  const executeItem = (item: CommandItem) => {
    triggerHaptic('light');
    onClose();
    item.run();
  };

  useEffect(() => {
    if (!isOpen || typeof window === 'undefined') return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (filteredItems.length > 0) {
          triggerHaptic('alignment');
          setSelectedIndex((prev) => (prev + 1) % filteredItems.length);
        }
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (filteredItems.length > 0) {
          triggerHaptic('alignment');
          setSelectedIndex((prev) => (prev - 1 + filteredItems.length) % filteredItems.length);
        }
        return;
      }
      if (e.key === 'Enter') {
        if (filteredItems.length > 0) {
          e.preventDefault();
          const chosen = filteredItems[selectedIndex] || filteredItems[0];
          if (chosen) executeItem(chosen);
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown, true);
    return () => window.removeEventListener('keydown', handleKeyDown, true);
  }, [isOpen, filteredItems, selectedIndex, onClose]);

  if (!isOpen) return null;

  const groupLabels: Record<CommandGroupId, string> = {
    actions: isZh ? '快捷操作' : 'Quick Actions',
    runtimes: isZh ? '活跃计算实例' : 'Active Runtimes',
    navigation: isZh ? '页面导航' : 'Navigation',
    preferences: isZh ? '偏好与会话' : 'Preferences & Session',
  };

  const orderedGroups: CommandGroupId[] = ['actions', 'runtimes', 'navigation', 'preferences'];
  let flatIdx = -1;

  return (
    <div
      className="fixed inset-0 z-[90] mac-modal-backdrop flex items-start justify-center pt-[12vh] px-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={isZh ? '命令面板' : 'Command Palette'}
    >
      <div
        className="w-full max-w-2xl mac-modal-panel mac-glass-heavy rounded-2xl overflow-hidden shadow-glass-lg border border-white/[0.12]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search Input Header */}
        <div className="flex items-center gap-3 px-4 py-3.5 border-b border-white/[0.08] bg-white/[0.02]">
          <Search className="w-4 h-4 text-blue-400 shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              isZh
                ? '搜索命令、计算实例、SSH 端口或页面...'
                : 'Search commands, runtimes, SSH ports, or views...'
            }
            className="w-full bg-transparent text-sm text-white placeholder-slate-400 focus:outline-none"
          />
          <button
            type="button"
            onClick={onClose}
            className="mac-kbd hover:border-white/25 transition-colors cursor-pointer"
          >
            Esc
          </button>
        </div>

        {/* Results */}
        <div className="max-h-[58vh] overflow-y-auto p-2 space-y-3">
          {filteredItems.length === 0 ? (
            <div className="py-12 text-center text-xs text-slate-400">
              {isZh ? '未找到匹配的命令或计算实例' : 'No matching commands or runtimes found'}
            </div>
          ) : (
            orderedGroups.map((groupId) => {
              const groupItems = filteredItems.filter((item) => item.group === groupId);
              if (groupItems.length === 0) return null;

              return (
                <div key={groupId} className="space-y-1">
                  <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400/90">
                    {groupLabels[groupId]}
                  </div>
                  {groupItems.map((item) => {
                    flatIdx += 1;
                    const idx = flatIdx;
                    const isSelected = idx === selectedIndex;
                    const Icon = item.icon;
                    return (
                      <button
                        key={item.id}
                        ref={(el) => {
                          itemRefs.current[idx] = el;
                        }}
                        type="button"
                        onMouseEnter={() => setSelectedIndex(idx)}
                        onClick={() => executeItem(item)}
                        className={`w-full text-left px-3 py-2.5 rounded-xl flex items-center justify-between gap-3 transition-colors ${
                          isSelected
                            ? 'bg-blue-600/25 border border-blue-400/35 shadow-[inset_0_1px_0_rgba(255,255,255,0.1)]'
                            : 'border border-transparent hover:bg-white/[0.04]'
                        }`}
                      >
                        <div className="flex items-center gap-3 min-w-0">
                          <div
                            className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 border ${
                              isSelected
                                ? 'bg-blue-500/20 border-blue-400/30'
                                : 'bg-white/[0.04] border-white/[0.07]'
                            }`}
                          >
                            <Icon className={`w-4 h-4 ${item.accentClass || 'text-slate-300'}`} />
                          </div>
                          <div className="min-w-0">
                            <div className="text-xs font-semibold text-white truncate">
                              {item.title}
                            </div>
                            {item.subtitle && (
                              <div className="text-[11px] text-slate-400 truncate mt-0.5 tabular-nums">
                                {item.subtitle}
                              </div>
                            )}
                          </div>
                        </div>

                        <div className="flex items-center gap-2 shrink-0">
                          {item.badge && (
                            <span className="px-2 py-0.5 rounded-md text-[10px] font-mono font-semibold bg-indigo-500/15 text-indigo-300 border border-indigo-400/25">
                              {item.badge}
                            </span>
                          )}
                          {item.shortcut && <kbd className="mac-kbd">{item.shortcut}</kbd>}
                        </div>
                      </button>
                    );
                  })}
                </div>
              );
            })
          )}
        </div>

        {/* Footer Hints */}
        <div className="px-4 py-2.5 border-t border-white/[0.07] bg-black/25 flex items-center justify-between text-[11px] text-slate-400">
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5">
              <kbd className="mac-kbd">↑</kbd>
              <kbd className="mac-kbd">↓</kbd>
              <span>{isZh ? '选择' : 'Navigate'}</span>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <kbd className="mac-kbd">↵</kbd>
              <span>{isZh ? '执行' : 'Run'}</span>
            </span>
          </div>
          <span className="tabular-nums">
            {isZh
              ? `${activeSessions.length} 个活跃实例`
              : `${activeSessions.length} active runtime${activeSessions.length === 1 ? '' : 's'}`}
          </span>
        </div>
      </div>
    </div>
  );
};
