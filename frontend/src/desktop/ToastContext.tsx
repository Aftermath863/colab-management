import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';
import { dispatchToast, ToastDispatchDetail, ToastVariant, triggerHaptic } from './macBridge';

export type { ToastVariant };

export interface ToastItem {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
  durationMs: number;
}

interface ToastContextValue {
  toasts: ToastItem[];
  pushToast: (toast: ToastDispatchDetail) => string;
  dismissToast: (id: string) => void;
  clearToasts: () => void;
}

const DEFAULT_DURATION_MS = 4200;
const MAX_VISIBLE_TOASTS = 4;

const fallbackToastContext: ToastContextValue = {
  toasts: [],
  pushToast: (toast) => {
    dispatchToast(toast);
    return '';
  },
  dismissToast: () => {},
  clearToasts: () => {},
};

const ToastContext = createContext<ToastContextValue>(fallbackToastContext);

const VARIANT_STYLES: Record<
  ToastVariant,
  {
    border: string;
    iconColor: string;
    progressBg: string;
    Icon: React.FC<{ className?: string }>;
  }
> = {
  success: {
    border: 'border-emerald-400/25 shadow-[0_18px_48px_-14px_rgba(16,185,129,0.28)]',
    iconColor: 'text-emerald-400',
    progressBg: 'bg-emerald-400/80',
    Icon: CheckCircle2,
  },
  error: {
    border: 'border-rose-400/30 shadow-[0_18px_48px_-14px_rgba(244,63,94,0.3)]',
    iconColor: 'text-rose-400',
    progressBg: 'bg-rose-400/85',
    Icon: AlertCircle,
  },
  warning: {
    border: 'border-amber-400/30 shadow-[0_18px_48px_-14px_rgba(245,158,11,0.28)]',
    iconColor: 'text-amber-400',
    progressBg: 'bg-amber-400/85',
    Icon: AlertTriangle,
  },
  info: {
    border: 'border-sky-400/25 shadow-[0_18px_48px_-14px_rgba(56,189,248,0.25)]',
    iconColor: 'text-sky-400',
    progressBg: 'bg-sky-400/80',
    Icon: Info,
  },
};

const ToastCard: React.FC<{
  toast: ToastItem;
  onDismiss: (id: string) => void;
}> = ({ toast, onDismiss }) => {
  const [paused, setPaused] = useState(false);
  const remainingRef = useRef<number>(toast.durationMs);
  const startedAtRef = useRef<number>(Date.now());
  const style = VARIANT_STYLES[toast.variant] || VARIANT_STYLES.info;
  const Icon = style.Icon;

  useEffect(() => {
    if (paused) return;
    startedAtRef.current = Date.now();
    const timer = window.setTimeout(() => {
      onDismiss(toast.id);
    }, Math.max(200, remainingRef.current));

    return () => {
      window.clearTimeout(timer);
      const elapsed = Date.now() - startedAtRef.current;
      remainingRef.current = Math.max(0, remainingRef.current - elapsed);
    };
  }, [paused, toast.id, onDismiss]);

  return (
    <div
      role="status"
      aria-live="polite"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className={`pointer-events-auto mac-toast-enter mac-glass-heavy rounded-2xl border overflow-hidden ${style.border}`}
    >
      <div className="p-3.5 flex items-start gap-3">
        <div className="mt-0.5 shrink-0">
          <Icon className={`w-4 h-4 ${style.iconColor}`} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-white leading-snug break-words">{toast.title}</p>
          {toast.description && (
            <p className="text-[11px] text-slate-300/85 mt-1 leading-relaxed break-words">
              {toast.description}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={() => onDismiss(toast.id)}
          aria-label="Dismiss notification"
          className="p-1 -mr-1 -mt-0.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition-colors shrink-0"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="h-[2px] w-full bg-white/[0.06] overflow-hidden">
        <div
          className={`h-full origin-left ${style.progressBg}`}
          style={{
            animation: `macToastProgress ${toast.durationMs}ms linear forwards`,
            animationPlayState: paused ? 'paused' : 'running',
          }}
        />
      </div>
    </div>
  );
};

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const seqRef = useRef(0);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const clearToasts = useCallback(() => {
    setToasts([]);
  }, []);

  const pushToast = useCallback((detail: ToastDispatchDetail): string => {
    const title = (detail?.title || '').trim();
    if (!title) return '';
    const variant: ToastVariant =
      detail.variant === 'success' ||
      detail.variant === 'error' ||
      detail.variant === 'warning' ||
      detail.variant === 'info'
        ? detail.variant
        : 'info';
    const durationMs =
      typeof detail.durationMs === 'number' && detail.durationMs >= 800
        ? detail.durationMs
        : DEFAULT_DURATION_MS;

    seqRef.current += 1;
    const id = `toast-${Date.now()}-${seqRef.current}`;

    if (variant === 'success') {
      triggerHaptic('success');
    } else if (variant === 'error' || variant === 'warning') {
      triggerHaptic('medium');
    } else {
      triggerHaptic('light');
    }

    setToasts((prev) => [...prev.slice(-(MAX_VISIBLE_TOASTS - 1)), {
      id,
      title,
      description: detail.description,
      variant,
      durationMs,
    }]);
    return id;
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') {
      return;
    }
    const handleToastEvent = (event: Event) => {
      const custom = event as CustomEvent<ToastDispatchDetail>;
      if (custom?.detail) {
        pushToast(custom.detail);
      }
    };
    window.addEventListener('colab:toast', handleToastEvent);
    return () => window.removeEventListener('colab:toast', handleToastEvent);
  }, [pushToast]);

  const contextValue = useMemo<ToastContextValue>(
    () => ({
      toasts,
      pushToast,
      dismissToast,
      clearToasts,
    }),
    [toasts, pushToast, dismissToast, clearToasts]
  );

  return (
    <ToastContext.Provider value={contextValue}>
      {children}
      {toasts.length > 0 && (
        <div
          aria-label="Notifications"
          className="fixed top-14 right-4 z-[100] flex flex-col gap-2.5 w-[calc(100vw-2rem)] max-w-sm pointer-events-none"
        >
          {toasts.map((item) => (
            <ToastCard key={item.id} toast={item} onDismiss={dismissToast} />
          ))}
        </div>
      )}
    </ToastContext.Provider>
  );
};

export function useToast(): ToastContextValue {
  return useContext(ToastContext);
}
