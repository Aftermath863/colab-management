import type React from 'react';
import { normalizeAcceleratorId } from './accelerators';

export type HapticStyle = 'light' | 'medium' | 'alignment' | 'success';
export type ToastVariant = 'info' | 'success' | 'warning' | 'error';
export type IDEKind = 'vscode' | 'cursor';

export interface MacRuntimeInfo {
  isMacApp?: boolean;
  arch?: string;
  version?: string;
  dataDir?: string;
  workspaceDir?: string;
  daemonManaged?: boolean;
  autoAuthToken?: string;
}

declare global {
  interface Window {
    __COLAB_MACOS__?: MacRuntimeInfo;
    webkit?: {
      messageHandlers?: {
        colabNative?: {
          postMessage: (message: unknown) => void;
        };
      };
    };
  }
}

const INTERACTIVE_DRAG_IGNORE_SELECTOR =
  'button, a, input, select, textarea, [role="button"], [data-no-drag="true"]';

export function getMacRuntimeInfo(): MacRuntimeInfo | null {
  if (typeof window === 'undefined') return null;
  return window.__COLAB_MACOS__ ?? null;
}

export function isMacDesktop(): boolean {
  return Boolean(getMacRuntimeInfo()?.isMacApp);
}

export function getMacArch(): string {
  return getMacRuntimeInfo()?.arch || 'arm64';
}

export function getMacVersion(): string {
  return getMacRuntimeInfo()?.version || '1.2.0';
}

function postNativeMessage(payload: Record<string, unknown>): boolean {
  if (typeof window === 'undefined') return false;
  const handler = window.webkit?.messageHandlers?.colabNative;
  if (!handler || typeof handler.postMessage !== 'function') {
    return false;
  }
  try {
    handler.postMessage(payload);
    return true;
  } catch {
    return false;
  }
}

export function triggerHaptic(style: HapticStyle = 'light'): void {
  postNativeMessage({ type: 'haptic', style });
}

export function notifyToast(
  title: string,
  description?: string,
  variant: ToastVariant = 'info'
): void {
  if (typeof window === 'undefined' || typeof window.dispatchEvent !== 'function') return;
  try {
    window.dispatchEvent(
      new CustomEvent('colab:toast', {
        detail: { title, description, variant },
      })
    );
  } catch {
    // Ignore dispatch errors in restricted environments
  }
}

export function openCommandPalette(): void {
  if (typeof window === 'undefined' || typeof window.dispatchEvent !== 'function') return;
  triggerHaptic('light');
  try {
    window.dispatchEvent(new CustomEvent('colab:open-command-palette'));
  } catch {
    // Ignore dispatch errors in restricted environments
  }
}

export function openExternalUrl(url: string): void {
  if (!url || typeof window === 'undefined') return;
  if (postNativeMessage({ type: 'openExternal', url })) {
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}

export function openTerminalSSH(port: number, sessionName: string = 'colab', command?: string): void {
  triggerHaptic('medium');
  const payload: Record<string, unknown> = {
    type: 'openTerminalSSH',
    port,
    sessionName,
  };
  if (command) {
    payload.command = command;
  }
  postNativeMessage(payload);
}

export function openInIDE(ide: IDEKind, port: number, remotePath: string = '/content'): void {
  triggerHaptic('medium');
  if (postNativeMessage({ type: 'openIDE', ide, port, remotePath })) {
    return;
  }
  if (typeof window !== 'undefined') {
    const scheme = ide === 'cursor' ? 'cursor' : 'vscode';
    window.open(
      `${scheme}://vscode-remote/ssh-remote+root@localhost:${port}${remotePath}`,
      '_blank',
      'noopener,noreferrer'
    );
  }
}

function isInteractiveDragTarget(target: EventTarget | null): boolean {
  if (!target || typeof Element === 'undefined') return false;
  const el = target instanceof Element ? target : (target as Node).parentElement;
  if (!el || typeof el.closest !== 'function') return false;
  return Boolean(el.closest(INTERACTIVE_DRAG_IGNORE_SELECTOR));
}

export function handleMacDragMouseDown(e: React.MouseEvent<HTMLElement>): void {
  if (!isMacDesktop() || e.button !== 0) return;
  if (isInteractiveDragTarget(e.target)) return;
  postNativeMessage({ type: 'windowAction', action: 'startDrag' });
}

export function handleMacDragDoubleClick(e: React.MouseEvent<HTMLElement>): void {
  if (!isMacDesktop()) return;
  if (isInteractiveDragTarget(e.target)) return;
  postNativeMessage({ type: 'windowAction', action: 'zoom' });
}

export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall back to execCommand below
  }

  if (typeof document === 'undefined' || !document.body) return false;
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    textarea.style.pointerEvents = 'none';
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

export function updateCardSpotlight(e: React.MouseEvent<HTMLElement>): void {
  const el = e.currentTarget;
  if (!el || typeof el.getBoundingClientRect !== 'function') return;
  const rect = el.getBoundingClientRect();
  el.style.setProperty('--spot-x', `${Math.round(e.clientX - rect.left)}px`);
  el.style.setProperty('--spot-y', `${Math.round(e.clientY - rect.top)}px`);
}

export function getAcceleratorBadgeClass(accelerator?: string | null): string {
  const norm = normalizeAcceleratorId(accelerator);
  switch (norm) {
    case 'H100':
      return 'bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-400/30 shadow-[0_0_14px_-4px_rgba(217,70,239,0.5)]';
    case 'A100':
      return 'bg-violet-500/15 text-violet-300 border-violet-400/30 shadow-[0_0_14px_-4px_rgba(139,92,246,0.45)]';
    case 'L4':
    case 'G4':
    case 'T4':
      return 'bg-sky-500/15 text-sky-300 border-sky-400/30';
    case 'V5E1':
    case 'V6E1':
      return 'bg-emerald-500/15 text-emerald-300 border-emerald-400/30';
    default:
      return 'bg-white/[0.06] text-slate-300 border-white/10';
  }
}

export function getSessionStatusStyles(status: string): {
  badgeClass: string;
  dotClass: string;
  isPulsing: boolean;
} {
  switch (status) {
    case 'READY':
    case 'BUSY':
      return {
        badgeClass: 'bg-emerald-500/15 text-emerald-300 border-emerald-400/30',
        dotClass: 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.85)]',
        isPulsing: true,
      };
    case 'PROVISIONING':
    case 'BOOTSTRAPPING':
      return {
        badgeClass: 'bg-sky-500/15 text-sky-300 border-sky-400/30',
        dotClass: 'bg-sky-400 shadow-[0_0_8px_rgba(56,189,248,0.85)]',
        isPulsing: true,
      };
    case 'ORPHAN':
      return {
        badgeClass: 'bg-amber-500/15 text-amber-300 border-amber-400/35',
        dotClass: 'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.85)]',
        isPulsing: true,
      };
    case 'FAILED':
    case 'LOST':
      return {
        badgeClass: 'bg-rose-500/15 text-rose-300 border-rose-400/30',
        dotClass: 'bg-rose-400',
        isPulsing: false,
      };
    default:
      return {
        badgeClass: 'bg-white/[0.06] text-slate-400 border-white/10',
        dotClass: 'bg-slate-500',
        isPulsing: false,
      };
  }
}
