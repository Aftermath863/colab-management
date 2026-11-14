import type React from 'react';
import { normalizeAcceleratorId } from './accelerators';
import {
  copyTextToClipboard,
  dispatchToast,
  getMacRuntimeInfo as getBridgeRuntimeInfo,
  isInteractiveDragExcluded,
  isMacDesktop,
  openCommandPalette as dispatchCommandPalette,
  openExternalUrl,
  openIDE,
  openTerminalSSH as postTerminalSSH,
  triggerHaptic,
  windowAction,
  type HapticStyle,
  type IDEKind,
  type MacRuntimeInfo,
  type ToastVariant,
} from '../desktop/macBridge';

export type { HapticStyle, IDEKind, MacRuntimeInfo, ToastVariant };
export { isMacDesktop, openExternalUrl, triggerHaptic };

export function getMacRuntimeInfo(): Partial<MacRuntimeInfo> | null {
  if (typeof window === 'undefined') return null;
  return window.__COLAB_MACOS__ ?? null;
}

export function getMacArch(): string {
  return getBridgeRuntimeInfo().arch || 'arm64';
}

export function getMacVersion(): string {
  return getBridgeRuntimeInfo().version || '1.2.0';
}

export function notifyToast(
  title: string,
  description?: string,
  variant: ToastVariant = 'info'
): void {
  dispatchToast({ title, description, variant });
}

export function openCommandPalette(): void {
  triggerHaptic('light');
  dispatchCommandPalette();
}

export function openTerminalSSH(port: number, sessionName: string = 'colab', command?: string): void {
  triggerHaptic('medium');
  postTerminalSSH(port, sessionName, command);
}

export function openInIDE(ide: IDEKind, port: number, remotePath: string = '/content'): void {
  triggerHaptic('medium');
  openIDE(ide, port, remotePath);
}

export function handleMacDragMouseDown(e: React.MouseEvent<HTMLElement>): void {
  if (!isMacDesktop() || e.button !== 0 || e.detail >= 2) return;
  if (isInteractiveDragExcluded(e.target)) return;
  windowAction('startDrag');
}

export function handleMacDragDoubleClick(e: React.MouseEvent<HTMLElement>): void {
  if (!isMacDesktop()) return;
  if (isInteractiveDragExcluded(e.target)) return;
  windowAction('zoom');
}

export async function copyToClipboard(text: string): Promise<boolean> {
  return copyTextToClipboard(text, false);
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
