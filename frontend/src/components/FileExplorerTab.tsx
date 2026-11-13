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

interface Props {
  sessionId: string;
  onEditFile: (path: string) => void;
}

export const FileExplorerTab: React.FC<Props> = ({ sessionId, onEditFile }) => {
  const [currentPath, setCurrentPath] = useState('');
  const [items, setItems] = useState<ContentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sync state
  const [isSyncing, setIsSyncing] = useState(false);
  const [syncToast, setSyncToast] = useState<{
    type: 'success' | 'error' | 'info';
    text: string;
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
    const parts = currentPath.split('/');
    parts.pop();
    loadDirectory(parts.join('/'));
  };

  const handleItemClick = (item: ContentItem) => {
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
    if (!confirm(`Are you sure you want to delete ${item.name}?`)) return;

    try {
      const itemPath = currentPath ? `${currentPath}/${item.name}` : item.name;
      await api.deletePath(sessionId, itemPath);
      loadDirectory(currentPath);
    } catch (err: any) {
      alert(`Delete failed: ${err.message}`);
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
        loadDirectory(currentPath);
      } catch (err: any) {
        alert(`Upload failed: ${err.message}`);
      }
    };
    reader.readAsDataURL(file);
  };

  const handlePushSync = async () => {
    setIsSyncing(true);
    setSyncToast({ type: 'info', text: 'Syncing local ./workspace to remote /content...' });
    try {
      const res = await api.pushWorkspaceSync(sessionId);
      const uploaded = res.files_uploaded ?? 0;
      const skipped = res.files_skipped ?? 0;
      const kb = Math.round(res.total_bytes_transferred / 1024);
      setSyncToast({
        type: 'success',
        text: `Push complete: ${uploaded} files uploaded (${kb} KB), ${skipped} files skipped (large models / git ignored).`,
      });
      loadDirectory(currentPath);
    } catch (err: any) {
      setSyncToast({ type: 'error', text: err.message || 'Workspace push sync failed' });
    } finally {
      setIsSyncing(false);
    }
  };

  const handlePullSync = async () => {
    setIsSyncing(true);
    setSyncToast({ type: 'info', text: 'Pulling output files from remote /content to ./workspace...' });
    try {
      const res = await api.pullWorkspaceSync(sessionId);
      const downloaded = res.files_downloaded ?? 0;
      const skipped = res.files_skipped ?? 0;
      const kb = Math.round(res.total_bytes_transferred / 1024);
      setSyncToast({
        type: 'success',
        text: `Pull complete: ${downloaded} files downloaded (${kb} KB), ${skipped} skipped.`,
      });
      loadDirectory(currentPath);
    } catch (err: any) {
      setSyncToast({ type: 'error', text: err.message || 'Workspace pull sync failed' });
    } finally {
      setIsSyncing(false);
    }
  };

  return (
    <div className="h-full flex flex-col bg-slate-900 text-slate-200">
      {/* Sync Notification Toast */}
      {syncToast && (
        <div
          className={`px-4 py-2 text-xs flex items-center justify-between border-b ${
            syncToast.type === 'success'
              ? 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
              : syncToast.type === 'error'
              ? 'bg-rose-950/60 border-rose-800 text-rose-300'
              : 'bg-indigo-950/60 border-indigo-800 text-indigo-300'
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
            <span>{syncToast.text}</span>
          </div>
          <button onClick={() => setSyncToast(null)} className="p-0.5 hover:bg-white/10 rounded">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Toolbar */}
      <div className="h-10 bg-slate-800 border-b border-slate-700 px-4 flex items-center justify-between text-xs">
        <div className="flex items-center gap-2 font-mono">
          <button
            onClick={handleNavigateUp}
            disabled={!currentPath}
            className="p-1 hover:bg-slate-700 rounded disabled:opacity-30"
            title="Up directory"
          >
            <ArrowUp className="w-4 h-4" />
          </button>
          <span>/content/{currentPath}</span>
        </div>

        <div className="flex items-center gap-2">
          {/* Push Sync Button */}
          <button
            onClick={handlePushSync}
            disabled={isSyncing}
            className="flex items-center gap-1.5 px-2.5 py-1 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-white font-medium transition-colors"
            title="Push local ./workspace changes to remote /content (skips large models and .git)"
          >
            <UploadCloud className="w-3.5 h-3.5" />
            <span>Push Sync</span>
          </button>

          {/* Pull Sync Button */}
          <button
            onClick={handlePullSync}
            disabled={isSyncing}
            className="flex items-center gap-1.5 px-2.5 py-1 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded text-slate-200 transition-colors"
            title="Pull remote /content changes to local ./workspace"
          >
            <DownloadCloud className="w-3.5 h-3.5" />
            <span>Pull Sync</span>
          </button>

          <button
            onClick={() => loadDirectory(currentPath)}
            disabled={loading}
            className="flex items-center gap-1 px-2.5 py-1 bg-slate-700 hover:bg-slate-600 rounded text-slate-200"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>

          <label className="flex items-center gap-1 px-3 py-1 bg-blue-600 hover:bg-blue-500 rounded text-white font-medium cursor-pointer">
            <Upload className="w-3.5 h-3.5" /> Upload File
            <input type="file" onChange={handleFileUpload} className="hidden" />
          </label>
        </div>
      </div>

      {/* Explorer Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {error && (
          <div className="mb-4 text-xs text-red-400 bg-red-950/40 p-2.5 rounded border border-red-800">
            {error}
          </div>
        )}

        {loading ? (
          <div className="text-center text-slate-500 py-8">Loading directory contents...</div>
        ) : items.length === 0 ? (
          <div className="text-center text-slate-500 py-8">Empty directory</div>
        ) : (
          <div className="grid grid-cols-1 gap-1">
            {items.map((item) => (
              <div
                key={item.name}
                onClick={() => handleItemClick(item)}
                className="flex items-center justify-between p-2 rounded hover:bg-slate-800 cursor-pointer text-sm group"
              >
                <div className="flex items-center gap-2.5 truncate">
                  {item.type === 'directory' ? (
                    <Folder className="w-4 h-4 text-amber-400 shrink-0" />
                  ) : (
                    <File className="w-4 h-4 text-slate-400 shrink-0" />
                  )}
                  <span className="truncate">{item.name}</span>
                </div>

                <div className="flex items-center gap-3 text-xs text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity">
                  {item.size !== undefined && <span>{Math.round(item.size / 1024)} KB</span>}
                  {item.type === 'file' && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        const filePath = currentPath ? `${currentPath}/${item.name}` : item.name;
                        onEditFile(filePath);
                      }}
                      className="p-1 hover:text-white"
                      title="Edit file"
                    >
                      <Edit className="w-3.5 h-3.5" />
                    </button>
                  )}
                  <button
                    onClick={(e) => handleDelete(e, item)}
                    className="p-1 hover:text-red-400"
                    title="Delete item"
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
