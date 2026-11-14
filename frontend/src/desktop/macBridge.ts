export type HapticStyle = 'light' | 'medium' | 'alignment' | 'success';
export type IDEKind = 'vscode' | 'cursor';
export type FinderTarget = 'workspace' | 'data' | 'logs';
export type WindowActionKind = 'startDrag' | 'zoom' | 'minimize' | 'close' | 'toggleFullScreen';
export type ToastVariant = 'info' | 'success' | 'warning' | 'error';

export interface MacRuntimeInfo {
  isMacApp: boolean;
  arch: string;
  version: string;
  dataDir?: string;
  workspaceDir?: string;
  daemonManaged?: boolean;
  autoAuthToken?: string;
}

export type NativeCommandName =
  | 'command-palette'
  | 'new-session'
  | 'sync-accounts'
  | 'link-account'
  | 'navigate'
  | 'open-session';

export interface NativeCommandDetail {
  command: NativeCommandName | string;
  payload?: Record<string, any> | string | null;
}

export interface ToastDispatchDetail {
  title: string;
  description?: string;
  variant?: ToastVariant;
  durationMs?: number;
}

export interface FleetSummaryPayload {
  activeRuntimes: number;
  totalCcu: number;
  burnRate: number;
}

declare global {
  interface Window {
    __COLAB_MACOS__?: Partial<MacRuntimeInfo> & Record<string, unknown>;
    webkit?: {
      messageHandlers?: {
        colabNative?: {
          postMessage: (message: Record<string, unknown>) => void;
        };
      };
    };
  }
}

let lastDragTimestamp = 0;

function hasWindow(): boolean {
  return typeof window !== 'undefined';
}

function postNativeMessage(message: Record<string, unknown>): boolean {
  if (!hasWindow()) return false;
  try {
    const handler = window.webkit?.messageHandlers?.colabNative;
    if (handler && typeof handler.postMessage === 'function') {
      handler.postMessage(message);
      return true;
    }
  } catch {
    // Ignore native bridge transport errors
  }
  return false;
}

export function isMacDesktop(): boolean {
  if (!hasWindow()) return false;
  return Boolean(window.__COLAB_MACOS__?.isMacApp);
}

export function getMacRuntimeInfo(): MacRuntimeInfo {
  if (!hasWindow() || !window.__COLAB_MACOS__) {
    return {
      isMacApp: false,
      arch: 'web',
      version: '1.2.0',
    };
  }
  const raw = window.__COLAB_MACOS__;
  return {
    isMacApp: Boolean(raw.isMacApp),
    arch: typeof raw.arch === 'string' && raw.arch ? raw.arch : 'arm64',
    version: typeof raw.version === 'string' && raw.version ? raw.version : '1.2.0',
    dataDir: typeof raw.dataDir === 'string' ? raw.dataDir : undefined,
    workspaceDir: typeof raw.workspaceDir === 'string' ? raw.workspaceDir : undefined,
    daemonManaged: typeof raw.daemonManaged === 'boolean' ? raw.daemonManaged : undefined,
    autoAuthToken: typeof raw.autoAuthToken === 'string' ? raw.autoAuthToken : undefined,
  };
}

export function triggerHaptic(style: HapticStyle = 'light'): void {
  postNativeMessage({ type: 'haptic', style });
}

export function notifyNative(title: string, body: string, subtitle?: string): boolean {
  const payload: Record<string, unknown> = { type: 'notify', title, body };
  if (subtitle) payload.subtitle = subtitle;
  return postNativeMessage(payload);
}

export function openExternalUrl(url: string): void {
  if (!hasWindow() || !url) return;
  if (isMacDesktop() && postNativeMessage({ type: 'openExternal', url })) {
    return;
  }
  if (typeof window.open === 'function') {
    window.open(url, '_blank', 'noopener,noreferrer');
  }
}

export function openTerminalSSH(port: number, sessionName: string, command?: string): boolean {
  const payload: Record<string, unknown> = {
    type: 'openTerminalSSH',
    port,
    sessionName,
  };
  if (command) payload.command = command;
  return postNativeMessage(payload);
}

export function openIDE(ide: IDEKind, port: number, remotePath: string = '/content'): boolean {
  if (isMacDesktop()) {
    const sent = postNativeMessage({
      type: 'openIDE',
      ide,
      port,
      remotePath,
    });
    if (sent) return true;
  }
  if (hasWindow()) {
    const cleanPath = remotePath.startsWith('/') ? remotePath : `/${remotePath}`;
    const url = `${ide}://vscode-remote/ssh-remote+root@localhost:${port}${cleanPath}`;
    window.location.href = url;
    return true;
  }
  return false;
}

export function revealInFinder(target: FinderTarget = 'workspace'): boolean {
  return postNativeMessage({ type: 'revealInFinder', target });
}

export function showDaemonLogs(): boolean {
  return postNativeMessage({ type: 'showDaemonLogs' });
}

export function windowAction(action: WindowActionKind): boolean {
  if (action === 'startDrag') {
    const now = Date.now();
    if (now - lastDragTimestamp < 40) {
      return false;
    }
    lastDragTimestamp = now;
  }
  return postNativeMessage({ type: 'windowAction', action });
}

export function updateFleetSummary(summary: FleetSummaryPayload): boolean {
  return postNativeMessage({
    type: 'updateFleetSummary',
    activeRuntimes: summary.activeRuntimes,
    totalCcu: Number(summary.totalCcu.toFixed(2)),
    burnRate: Number(summary.burnRate.toFixed(2)),
  });
}

export function syncMenuBarSummary(activeRuntimes: number, totalCcu: number, burnRate: number): boolean {
  return updateFleetSummary({ activeRuntimes, totalCcu, burnRate });
}

export function onNativeCommand(handler: (detail: NativeCommandDetail) => void): () => void {
  if (!hasWindow() || typeof window.addEventListener !== 'function') {
    return () => {};
  }
  const listener = (event: Event) => {
    const custom = event as CustomEvent<NativeCommandDetail>;
    if (custom?.detail && typeof custom.detail.command === 'string') {
      handler(custom.detail);
    }
  };
  window.addEventListener('colab:native-command', listener);
  return () => window.removeEventListener('colab:native-command', listener);
}

export function dispatchToast(detail: ToastDispatchDetail): void {
  if (!hasWindow() || typeof window.dispatchEvent !== 'function') return;
  try {
    window.dispatchEvent(new CustomEvent<ToastDispatchDetail>('colab:toast', { detail }));
  } catch {
    // Ignore event dispatch errors in restricted environments
  }
}

export function openCommandPalette(): void {
  if (!hasWindow() || typeof window.dispatchEvent !== 'function') return;
  try {
    window.dispatchEvent(new CustomEvent('colab:open-command-palette'));
  } catch {
    // Ignore
  }
}

export function isInteractiveDragExcluded(target: EventTarget | null): boolean {
  if (typeof Element === 'undefined' || !(target instanceof Element)) {
    return false;
  }
  return Boolean(
    target.closest(
      'button, a, input, select, textarea, label, [role="button"], [contenteditable="true"], [data-no-drag="true"], .mac-no-drag'
    )
  );
}

export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (!hasWindow()) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      triggerHaptic('success');
      return true;
    }
  } catch {
    // Fall back to execCommand below
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    ta.style.pointerEvents = 'none';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    if (ok) triggerHaptic('success');
    return ok;
  } catch {
    return false;
  }
}
