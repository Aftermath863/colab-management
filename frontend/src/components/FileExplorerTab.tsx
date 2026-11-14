import React, { useState, useEffect } from 'react';
import {
  Folder,
  File,
  Upload,
  Trash2,
  Edit,
  RefreshCw,
  ArrowUp,
  UploadCloud,
  DownloadCloud,
  CheckCircle2,
  AlertCircle,
  X,
} from 'lucide-react';
import { ContentItem } from '../types';
import { api } from '../services/api';
import { LocalizedText, useI18n } from '../i18n';
import { triggerHaptic } from '../desktop/macBridge';
import { useToast } from '../desktop/ToastContext';

interface Props {
  sessionId: string;
  onEditFile: (path: string) => void;
}

function formatFileSize(bytes?: number): string {
  if (bytes === undefined) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const FileExplorerTab: React.FC<Props> = ({ sessionId, onEditFile }) => {
  const { t, te, tm, locale, formatActionError } = useI18n();
  const isZh = locale === 'zh';
  const toast = useToast();

  const [currentPath, setCurrentPath] = useState('');
  const [items, setItems] = useState<ContentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sync state
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncToast, setSyncToast] = useState<{
    type: 'success' | 'error' | 'info';
    msg: LocalizedText;
  } | null>(null);

  const loadDirectory = async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listContents(sessionId, path);
      setItems(data.content || []);
      setCurrentPath(path);
    } catch (err: any) {
      setError(err.message || 'Failed to list directory contents');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDirectory('');
  }, [sessionId]);

  const handleNavigateUp = () => {
    if (!currentPath) return;
    triggerHaptic('light');
    const parts = currentPath.split('/');
    parts.pop();
    loadDirectory(parts.join('/'));
  };

  const handleItemClick = (item: ContentItem) => {
    triggerHaptic('light');
    if (item.type === 'directory') {
      const newPath = currentPath ? `${currentPath}/${item.name}` : item.name;
      loadDirectory(newPath);
    } else {
      const filePath = currentPath ? `${currentPath}/${item.name}` : item.name;
      onEditFile(filePath);
    }
  };

  const handleDelete = async (e: React.MouseEvent, item: ContentItem) => {
    e.stopPropagation();
    if (!confirm(t('files.confirmDelete', { name: item.name }))) return;

    try {
      const itemPath = currentPath ? `${currentPath}/${item.name}` : item.name;
      await api.deletePath(sessionId, itemPath);
      toast.pushToast({
        variant: 'success',
        title: isZh ? `已删除 ${item.name}` : `Deleted ${item.name}`,
      });
      loadDirectory(currentPath);
    } catch (err: any) {
      toast.pushToast({
        variant: 'error',
        title: formatActionError('files.deleteFailed', err?.message),
      });
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const input = e.target;
    input.value = '';

    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const result = reader.result as string;
        const b64 = result.split(',')[1] || btoa(result);
        const targetPath = currentPath ? `${currentPath}/${file.name}` : file.name;
        await api.uploadFile(sessionId, targetPath, b64, file.name);
        toast.pushToast({
          variant: 'success',
          title: isZh ? `已上传 ${file.name}` : `Uploaded ${file.name}`,
          description: `/content/${targetPath}`,
        });
        loadDirectory(currentPath);
      } catch (err: any) {
        toast.pushToast({
          variant: 'error',
          title: formatActionError('files.uploadFailed', err?.message),
        });
      }
    };
    reader.readAsDataURL(file);
  };

  const handlePushSync = async () => {
    triggerHaptic('light');
    setIsSyncing(true);
    setSyncToast({ type: 'info', msg: { key: 'files.pushSyncing' } });
    try {
      const res = await api.pushWorkspaceSync(sessionId);
      const uploaded = res.files_uploaded ?? 0;
      const skipped = res.files_skipped ?? 0;
      const kb = Math.round(res.total_bytes_transferred / 1024);
      setSyncToast({
        type: 'success',
        msg: { key: 'files.pushComplete', params: { uploaded, kb, skipped } },
      });
      toast.pushToast({
        variant: 'success',
        title: t('files.pushComplete', { uploaded, kb, skipped }),
      });
      loadDirectory(currentPath);
    } catch (err: any) {
      setSyncToast({
        type: 'error',
        msg: err.message ? { raw: err.message } : { key: 'files.pushFailed' },
      });
    } finally {
      setIsSyncing(false);
    }
  };

  const handlePullSync = async () => {
    triggerHaptic('light');
    setIsSyncing(true);
    setSyncToast({ type: 'info', msg: { key: 'files.pullSyncing' } });
    try {
      const res = await api.pullWorkspaceSync(sessionId);
      const downloaded = res.files_downloaded ?? 0;
      const skipped = res.files_skipped ?? 0;
      const kb = Math.round(res.total_bytes_transferred / 1024);
      setSyncToast({
        type: 'success',
        msg: { key: 'files.pullComplete', params: { downloaded, kb, skipped } },
      });
      toast.pushToast({
        variant: 'success',
        title: t('files.pullComplete', { downloaded, kb, skipped }),
      });
      loadDirectory(currentPath);
    } catch (err: any) {
      setSyncToast({
        type: 'error',
        msg: err.message ? { raw: err.message } : { key: 'files.pullFailed' },
      });
    } finally {
      setIsSyncing(false);
    }
  };

  return (
    <div className="h-full flex flex-col bg-[#070b14] text-slate-200">
      {/* Sync Notification Banner */}
      {syncToast && (
        <div
          className={`px-4 py-2 text-xs flex items-center justify-between border-b ${
            syncToast.type === 'success'
              ? 'bg-emerald-950/50 border-emerald-500/30 text-emerald-300'
              : syncToast.type === 'error'
                ? 'bg-rose-950/50 border-rose-500/30 text-rose-300'
                : 'bg-indigo-950/50 border-indigo-500/30 text-indigo-300'
          }`}
        >
          <div className="flex items-center gap-2">
            {syncToast.type === 'success' ? (
              <CheckCircle2 className="w-3.5 h-3.5 shrink-0 text-emerald-400" />
            ) : syncToast.type === 'error' ? (
              <AlertCircle className="w-3.5 h-3.5 shrink-0 text-rose-400" />
            ) : (
              <RefreshCw className="w-3.5 h-3.5 shrink-0 animate-spin text-indigo-400" />
            )}
            <span>{tm(syncToast.msg)}</span>
          </div>
          <button
            type="button"
            onClick={() => setSyncToast(null)}
            className="p-0.5 hover:bg-white/10 rounded"
            title={t('common.close')}
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Toolbar */}
      <div className="min-h-[2.75rem] py-2 bg-slate-950/75 border-b border-white/[0.07] px-3 sm:px-4 flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2 font-mono min-w-0">
          <button
            type="button"
            onClick={handleNavigateUp}
            disabled={!currentPath}
            className="mac-btn-secondary p-1.5 rounded-lg disabled:opacity-30 shrink-0"
            title={t('files.upDirectory')}
          >
            <ArrowUp className="w-3.5 h-3.5" />
          </button>
          <span className="truncate px-2.5 py-1 rounded-lg bg-white/[0.04] border border-white/[0.07] text-slate-300">
            /content/{currentPath}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Push Sync Button */}
          <button
            type="button"
            onClick={handlePushSync}
            disabled={isSyncing}
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 bg-indigo-600/85 hover:bg-indigo-500 active:scale-[0.97] border border-indigo-400/30 disabled:opacity-50 rounded-lg text-white font-medium transition-all"
            title={t('files.pushSyncTooltip')}
          >
            <UploadCloud className="w-3.5 h-3.5" />
            <span>{t('files.pushSync')}</span>
          </button>

          {/* Pull Sync Button */}
          <button
            type="button"
            onClick={handlePullSync}
            disabled={isSyncing}
            className="mac-btn-secondary gap-1.5 px-2.5 py-1.5 disabled:opacity-50 rounded-lg font-medium"
            title={t('files.pullSyncTooltip')}
          >
            <DownloadCloud className="w-3.5 h-3.5" />
            <span>{t('files.pullSync')}</span>
          </button>

          <button
            type="button"
            onClick={() => loadDirectory(currentPath)}
            disabled={loading}
            className="mac-btn-secondary gap-1.5 px-2.5 py-1.5 rounded-lg font-medium"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> {t('common.refresh')}
          </button>

          <label className="mac-btn-primary gap-1.5 px-3 py-1.5 rounded-lg font-medium cursor-pointer">
            <Upload className="w-3.5 h-3.5" /> {t('files.uploadFile')}
            <input type="file" onChange={handleFileUpload} className="hidden" />
          </label>
        </div>
      </div>

      {/* Explorer Content */}
      <div className="flex-1 overflow-y-auto p-3 sm:p-4">
        {error && (
          <div className="mb-4 text-xs text-rose-300 bg-rose-950/40 p-3 rounded-xl border border-rose-500/35">
            {te(error)}
          </div>
        )}

        {loading ? (
          <div className="text-center text-slate-400 py-10 text-xs">{t('files.loadingDir')}</div>
        ) : items.length === 0 ? (
          <div className="text-center text-slate-500 py-10 text-xs">{t('files.emptyDir')}</div>
        ) : (
          <div className="grid grid-cols-1 gap-1">
            {items.map((item) => (
              <div
                key={item.name}
                onClick={() => handleItemClick(item)}
                className="flex items-center justify-between px-3 py-2 rounded-xl border border-transparent hover:border-white/[0.07] hover:bg-white/[0.04] cursor-pointer text-sm group transition-colors"
              >
                <div className="flex items-center gap-2.5 truncate">
                  {item.type === 'directory' ? (
                    <Folder className="w-4 h-4 text-amber-400 shrink-0" />
                  ) : (
                    <File className="w-4 h-4 text-sky-300/75 shrink-0" />
                  )}
                  <span className="truncate text-slate-200 group-hover:text-white transition-colors">
                    {item.name}
                  </span>
                </div>

                <div className="flex items-center gap-3 text-xs text-slate-400 opacity-70 group-hover:opacity-100 transition-opacity">
                  {item.size !== undefined && (
                    <span className="font-mono tabular-nums text-[11px] text-slate-500">
                      {formatFileSize(item.size)}
                    </span>
                  )}
                  {item.type === 'file' && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        const filePath = currentPath ? `${currentPath}/${item.name}` : item.name;
                        onEditFile(filePath);
                      }}
                      className="p-1 rounded-md hover:bg-white/10 hover:text-white transition-colors"
                      title={t('files.editFileTooltip')}
                    >
                      <Edit className="w-3.5 h-3.5" />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={(e) => handleDelete(e, item)}
                    className="p-1 rounded-md hover:bg-rose-500/15 hover:text-rose-400 transition-colors"
                    title={t('files.deleteItemTooltip')}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
