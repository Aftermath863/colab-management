import React, { useState, useEffect } from 'react';
import Editor from '@monaco-editor/react';
import { Save, RefreshCw, CheckCircle2 } from 'lucide-react';
import { api } from '../services/api';
import { useI18n } from '../i18n';

interface Props {
  sessionId: string;
  filePath: string;
}

export const MonacoEditorTab: React.FC<Props> = ({ sessionId, filePath }) => {
  const { locale, t, formatActionError } = useI18n();
  const [content, setContent] = useState<string>('');
  const [isLoadFallback, setIsLoadFallback] = useState<boolean>(false);
  const [isDirty, setIsDirty] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [savedNotice, setSavedNotice] = useState<boolean>(false);

  const loadFile = async () => {
    if (!filePath) return;
    setLoading(true);
    try {
      const data = await api.downloadFile(sessionId, filePath);
      if (data.format === 'base64' && data.content) {
        try {
          const binStr = atob(data.content);
          const bytes = Uint8Array.from(binStr, (c) => c.charCodeAt(0));
          setContent(new TextDecoder('utf-8').decode(bytes));
        } catch {
          setContent(atob(data.content));
        }
      } else {
        setContent(data.content || '');
      }
      setIsLoadFallback(false);
      setIsDirty(false);
    } catch {
      setIsLoadFallback(true);
      setContent(t('editor.loadFailedComment'));
      setIsDirty(false);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFile();
  }, [sessionId, filePath]);

  useEffect(() => {
    if (isLoadFallback && !isDirty) {
      setContent(t('editor.loadFailedComment'));
    }
  }, [locale, isLoadFallback, isDirty, t]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const b64 = btoa(unescape(encodeURIComponent(content)));
      const filename = filePath.split('/').pop() || 'file';
      await api.uploadFile(sessionId, filePath, b64, filename);
      setIsLoadFallback(false);
      setIsDirty(false);
      setSavedNotice(true);
      setTimeout(() => setSavedNotice(false), 2000);
    } catch (err: any) {
      alert(formatActionError('editor.saveFailed', err?.message));
    } finally {
      setSaving(false);
    }
  };

  const getLanguage = (path: string) => {
    if (path.endsWith('.py')) return 'python';
    if (path.endsWith('.js')) return 'javascript';
    if (path.endsWith('.ts') || path.endsWith('.tsx')) return 'typescript';
    if (path.endsWith('.json')) return 'json';
    if (path.endsWith('.sh')) return 'shell';
    if (path.endsWith('.md')) return 'markdown';
    return 'plaintext';
  };

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      <div className="h-10 bg-slate-800 border-b border-slate-700 px-4 flex items-center justify-between text-xs">
        <div className="flex items-center gap-2 text-slate-300 font-mono">
          <span>{filePath}</span>
          {isDirty && <span className="text-amber-400 font-bold">{t('editor.unsaved')}</span>}
        </div>
        <div className="flex items-center gap-2">
          {savedNotice && (
            <span className="flex items-center gap-1 text-green-400">
              <CheckCircle2 className="w-3.5 h-3.5" /> {t('common.saved')}
            </span>
          )}
          <button
            type="button"
            onClick={loadFile}
            disabled={loading}
            className="flex items-center gap-1 px-2.5 py-1 bg-slate-700 hover:bg-slate-600 rounded text-slate-200"
          >
            <RefreshCw className="w-3 h-3" /> {t('editor.reload')}
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !isDirty}
            className="flex items-center gap-1 px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded text-white font-medium"
          >
            <Save className="w-3.5 h-3.5" /> {saving ? t('common.saving') : t('common.save')}
          </button>
        </div>
      </div>

      <div className="flex-1">
        <Editor
          height="100%"
          language={getLanguage(filePath)}
          value={content}
          theme="vs-dark"
          onChange={(val) => {
            setContent(val || '');
            setIsDirty(true);
          }}
          options={{
            minimap: { enabled: true },
            fontSize: 13,
            automaticLayout: true,
          }}
        />
      </div>
    </div>
  );
};
