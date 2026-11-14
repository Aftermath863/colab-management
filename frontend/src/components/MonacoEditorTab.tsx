import React, { useState, useEffect, useCallback } from 'react';
import Editor from '@monaco-editor/react';
import { Save, RefreshCw, CheckCircle2, FileCode2 } from 'lucide-react';
import { api } from '../services/api';
import { useI18n } from '../i18n';
import { triggerHaptic } from '../desktop/macBridge';
import { useToast } from '../desktop/ToastContext';

interface Props {
  sessionId: string;
  filePath: string;
}

export const MonacoEditorTab: React.FC<Props> = ({ sessionId, filePath }) => {
  const { locale, t, formatActionError } = useI18n();
  const isZh = locale === 'zh';
  const toast = useToast();

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

  const handleSave = useCallback(async () => {
    if (saving || !isDirty) return;
    setSaving(true);
    try {
      const b64 = btoa(unescape(encodeURIComponent(content)));
      const filename = filePath.split('/').pop() || 'file';
      await api.uploadFile(sessionId, filePath, b64, filename);
      setIsLoadFallback(false);
      setIsDirty(false);
      setSavedNotice(true);
      triggerHaptic('success');
      toast.pushToast({
        variant: 'success',
        title: isZh ? `已保存 ${filename}` : `Saved ${filename}`,
      });
      setTimeout(() => setSavedNotice(false), 2000);
    } catch (err: any) {
      triggerHaptic('medium');
      toast.pushToast({
        variant: 'error',
        title: formatActionError('editor.saveFailed', err?.message),
      });
    } finally {
      setSaving(false);
    }
  }, [saving, isDirty, content, filePath, sessionId, isZh, toast, formatActionError]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      void handleSave();
    }
  };

  const getLanguage = (path: string) => {
    const lower = path.toLowerCase();
    if (lower.endsWith('.py')) return 'python';
    if (lower.endsWith('.js') || lower.endsWith('.mjs') || lower.endsWith('.cjs')) return 'javascript';
    if (lower.endsWith('.ts') || lower.endsWith('.tsx')) return 'typescript';
    if (lower.endsWith('.json') || lower.endsWith('.ipynb')) return 'json';
    if (lower.endsWith('.sh') || lower.endsWith('.bash') || lower.endsWith('.zsh')) return 'shell';
    if (lower.endsWith('.md')) return 'markdown';
    if (lower.endsWith('.yaml') || lower.endsWith('.yml')) return 'yaml';
    if (lower.endsWith('.sql')) return 'sql';
    if (lower.endsWith('.css')) return 'css';
    if (lower.endsWith('.html')) return 'html';
    return 'plaintext';
  };

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]" onKeyDown={handleKeyDown}>
      <div className="h-10 bg-slate-950/85 border-b border-white/[0.08] px-4 flex items-center justify-between text-xs gap-3">
        <div className="flex items-center gap-2 text-slate-300 font-mono min-w-0">
          <FileCode2 className="w-3.5 h-3.5 text-sky-400 shrink-0" />
          <span className="truncate">/content/{filePath}</span>
          {isDirty && (
            <span className="px-1.5 py-0.5 rounded bg-amber-500/15 border border-amber-400/30 text-amber-300 font-sans font-semibold text-[11px] shrink-0">
              {t('editor.unsaved')}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {savedNotice && (
            <span className="flex items-center gap-1 text-emerald-400 font-medium">
              <CheckCircle2 className="w-3.5 h-3.5" /> {t('common.saved')}
            </span>
          )}
          <button
            type="button"
            onClick={loadFile}
            disabled={loading}
            className="mac-btn-secondary gap-1.5 px-2.5 py-1 rounded-lg"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> {t('editor.reload')}
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || !isDirty}
            className="mac-btn-primary gap-1.5 px-3 py-1 disabled:opacity-45 rounded-lg font-medium"
          >
            <Save className="w-3.5 h-3.5" />
            <span>{saving ? t('common.saving') : t('common.save')}</span>
            <kbd className="hidden sm:inline-flex px-1 py-0.5 rounded bg-white/15 border border-white/20 font-mono text-[9px] leading-none">
              ⌘S
            </kbd>
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0">
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
            fontFamily: '"SF Mono", Menlo, Monaco, "Courier New", monospace',
            smoothScrolling: true,
            automaticLayout: true,
          }}
        />
      </div>
    </div>
  );
};
