import React, { useState, useEffect } from 'react';
import { Copy, Check, Terminal, ExternalLink, X, ShieldCheck, Laptop } from 'lucide-react';
import { api } from '../services/api';
import { SSHConfig } from '../types';

interface SSHHelperModalProps {
  sessionId: string;
  sessionName: string;
  isOpen: boolean;
  onClose: () => void;
}

export const SSHHelperModal: React.FC<SSHHelperModalProps> = ({
  sessionId,
  sessionName,
  isOpen,
  onClose,
}) => {
  const [config, setConfig] = useState<SSHConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedCmd, setCopiedCmd] = useState(false);
  const [copiedCfg, setCopiedCfg] = useState(false);

  useEffect(() => {
    if (isOpen) {
      loadSSHConfig();
    }
  }, [isOpen, sessionId]);

  const loadSSHConfig = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getSSHConfig(sessionId);
      setConfig(data);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch SSH config');
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = async (text: string, isCmd: boolean) => {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    if (isCmd) {
      setCopiedCmd(true);
      setTimeout(() => setCopiedCmd(false), 2000);
    } else {
      setCopiedCfg(true);
      setTimeout(() => setCopiedCfg(false), 2000);
    }
  };

  if (!isOpen) return null;

  const hostAlias = `colab-${sessionName || sessionId}`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-2xl w-full shadow-2xl overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <Terminal className="w-5 h-5 text-indigo-400" />
            <h2 className="text-lg font-semibold text-white">SSH Connection & IDE Bridge</h2>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white transition-colors p-1 rounded-lg hover:bg-slate-800"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6 overflow-y-auto max-h-[80vh]">
          {loading ? (
            <div className="py-12 text-center text-slate-400">Loading SSH connection details...</div>
          ) : error ? (
            <div className="p-4 bg-rose-500/10 border border-rose-500/20 rounded-lg text-rose-400 text-sm">
              {error}
            </div>
          ) : config ? (
            <>
              {/* Multiplexer Banner */}
              <div className="flex items-start gap-3 p-3.5 bg-indigo-500/10 border border-indigo-500/20 rounded-lg text-xs text-indigo-300">
                <ShieldCheck className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" />
                <div>
                  <strong className="font-semibold text-indigo-200">AsyncSSH Multiplexer Active:</strong> Bound to local port{' '}
                  <span className="font-mono text-white font-bold">{config.port || 'N/A'}</span>. Transparently multiplexes
                  unlimited concurrent VS Code windows, SFTP streams, and terminal sessions over a single upstream tunnel without triggering Colab's HTTP 429 lock.
                </div>
              </div>

              {/* Quick Terminal Command */}
              <div>
                <label className="block text-xs font-medium text-slate-400 uppercase tracking-wider mb-2">
                  Direct Terminal Command
                </label>
                <div className="relative group">
                  <pre className="bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm font-mono text-emerald-400 overflow-x-auto select-all">
                    {config.connection_command}
                  </pre>
                  <button
                    onClick={() => copyToClipboard(config.connection_command, true)}
                    className="absolute right-2 top-2 p-1.5 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors flex items-center gap-1.5 text-xs"
                  >
                    {copiedCmd ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copiedCmd ? 'Copied' : 'Copy'}</span>
                  </button>
                </div>
              </div>

              {/* VS Code / Cursor ~/.ssh/config Snippet */}
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="block text-xs font-medium text-slate-400 uppercase tracking-wider">
                    VS Code / Cursor Remote-SSH Config Snippet (~/.ssh/config)
                  </label>
                  <button
                    onClick={() => copyToClipboard(config.vscode_config, false)}
                    className="text-xs text-indigo-400 hover:text-indigo-300 flex items-center gap-1"
                  >
                    {copiedCfg ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                    {copiedCfg ? 'Copied Snippet' : 'Copy Snippet'}
                  </button>
                </div>
                <pre className="bg-slate-950 border border-slate-800 rounded-lg p-3 text-xs font-mono text-slate-300 overflow-x-auto select-all">
                  {config.vscode_config}
                </pre>
              </div>

              {/* IDE Deep Links & Quick Connect */}
              <div>
                <label className="block text-xs font-medium text-slate-400 uppercase tracking-wider mb-2">
                  One-Click IDE Launch
                </label>
                <div className="flex flex-wrap gap-3">
                  <a
                    href={`vscode://vscode-remote/ssh-remote+${hostAlias}/content`}
                    className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-medium flex items-center gap-2 transition-colors shadow-sm"
                  >
                    <Laptop className="w-3.5 h-3.5" />
                    Open in VS Code
                    <ExternalLink className="w-3 h-3 opacity-70" />
                  </a>
                  <a
                    href={`cursor://vscode-remote/ssh-remote+${hostAlias}/content`}
                    className="px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-white rounded-lg text-xs font-medium flex items-center gap-2 transition-colors"
                  >
                    <Laptop className="w-3.5 h-3.5 text-purple-400" />
                    Open in Cursor
                    <ExternalLink className="w-3 h-3 opacity-70" />
                  </a>
                </div>
                <p className="text-[11px] text-slate-500 mt-2">
                  Tip: Ensure the snippet above is saved into your local <code className="text-slate-400 font-mono">~/.ssh/config</code> before launching the IDE deep link.
                </p>
              </div>
            </>
          ) : null}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 bg-slate-950/60 border-t border-slate-800 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-sm font-medium transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
