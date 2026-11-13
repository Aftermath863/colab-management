import React, { useState } from 'react';
import { AlertTriangle, Camera, Loader2, Square, X } from 'lucide-react';
import { Session } from '../types';
import { useI18n } from '../i18n';

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

  const handleStopDirect = async () => {
    setStopping(true);
    try {
      await onStopDirectly();
    } finally {
      setStopping(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/75 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-slate-900 border border-slate-700/80 rounded-2xl shadow-2xl overflow-hidden">
        <div className="px-5 py-4 bg-slate-950/70 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center space-x-2.5">
            <AlertTriangle className="w-5 h-5 text-amber-400" />
            <h3 className="text-sm font-bold text-white">
              {t('snapshots.stopModalTitle', { name: session.name })}
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <p className="text-xs text-slate-300 leading-relaxed">{t('snapshots.stopModalDesc')}</p>

          <div className="space-y-2.5">
            <button
              type="button"
              onClick={onSaveAndStop}
              disabled={stopping}
              className="w-full px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold flex items-center justify-center space-x-2 transition-colors disabled:opacity-50"
            >
              <Camera className="w-4 h-4" />
              <span>{t('snapshots.saveAndStopButton')}</span>
            </button>

            <button
              type="button"
              onClick={handleStopDirect}
              disabled={stopping}
              className="w-full px-4 py-2.5 rounded-xl bg-rose-950/50 hover:bg-rose-900/60 border border-rose-500/40 text-rose-200 text-xs font-semibold flex items-center justify-center space-x-2 transition-colors disabled:opacity-50"
            >
              {stopping ? <Loader2 className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
              <span>{t('snapshots.stopDirectlyButton')}</span>
            </button>

            <button
              type="button"
              onClick={onClose}
              disabled={stopping}
              className="w-full px-4 py-2 rounded-xl text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-800/70 transition-colors"
            >
              {t('common.cancel')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
