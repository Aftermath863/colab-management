import React, { useEffect, useState } from 'react';
import { AlertTriangle, Camera, Loader2, Square, X } from 'lucide-react';
import { Session } from '../types';
import { useI18n } from '../i18n';
import { triggerHaptic } from '../desktop/macBridge';

interface StopSessionModalProps {
  session: Session;
  onSaveAndStop: () => void;
  onStopDirectly: () => Promise<void> | void;
  onClose: () => void;
}

export const StopSessionModal: React.FC<StopSessionModalProps> = ({
  session,
  onSaveAndStop,
  onStopDirectly,
  onClose,
}) => {
  const { t } = useI18n();
  const [stopping, setStopping] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !stopping) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [stopping, onClose]);

  const handleStopDirect = async () => {
    triggerHaptic('medium');
    setStopping(true);
    try {
      await onStopDirectly();
    } finally {
      setStopping(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 mac-modal-backdrop flex items-center justify-center p-4"
      onClick={() => {
        if (!stopping) onClose();
      }}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-md mac-modal-panel mac-glass-heavy border border-white/[0.1] rounded-2xl shadow-glass-lg overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 bg-white/[0.02] border-b border-white/[0.08] flex items-center justify-between">
          <div className="flex items-center space-x-2.5">
            <span className="w-7 h-7 rounded-lg bg-amber-500/15 border border-amber-400/30 flex items-center justify-center text-amber-400">
              <AlertTriangle className="w-4 h-4" />
            </span>
            <h3 className="text-sm font-bold text-white">
              {t('snapshots.stopModalTitle', { name: session.name })}
            </h3>
          </div>
          <div className="flex items-center gap-2">
            <kbd className="mac-kbd hidden sm:inline-flex">Esc</kbd>
            <button
              type="button"
              onClick={onClose}
              disabled={stopping}
              className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition-colors"
              title={t('common.close')}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="p-5 space-y-4">
          <p className="text-xs text-slate-300 leading-relaxed">{t('snapshots.stopModalDesc')}</p>

          <div className="space-y-2.5">
            <button
              type="button"
              onClick={() => {
                triggerHaptic('light');
                onSaveAndStop();
              }}
              disabled={stopping}
              className="w-full mac-btn-primary px-4 py-2.5 rounded-xl text-xs font-semibold gap-2 disabled:opacity-50"
            >
              <Camera className="w-4 h-4" />
              <span>{t('snapshots.saveAndStopButton')}</span>
            </button>

            <button
              type="button"
              onClick={handleStopDirect}
              disabled={stopping}
              className="w-full px-4 py-2.5 rounded-xl bg-rose-950/60 hover:bg-rose-900/70 active:scale-[0.97] border border-rose-500/40 text-rose-200 text-xs font-semibold flex items-center justify-center space-x-2 transition-all disabled:opacity-50"
            >
              {stopping ? <Loader2 className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
              <span>{t('snapshots.stopDirectlyButton')}</span>
            </button>

            <button
              type="button"
              onClick={onClose}
              disabled={stopping}
              className="w-full mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium disabled:opacity-50"
            >
              {t('common.cancel')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
