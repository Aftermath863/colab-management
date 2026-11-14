import React, { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { Terminal as TerminalIcon } from 'lucide-react';
import { safeReadStorage, translateErrorMessage, useI18n } from '../i18n';

interface Props {
  sessionId: string;
}

function formatTerminalOutput(chunk: string, isZh: boolean): string {
  if (!isZh || !chunk.includes('[Colab Terminal Error:')) return chunk;
  return chunk.replace(/\[Colab Terminal Error:\s*([^\]]+)\]/g, (_m, detail: string) => {
    const localized = translateErrorMessage(detail.trim(), 'zh');
    return `[Colab 终端连接错误：${localized}]`;
  });
}

export const TerminalTab: React.FC<Props> = ({ sessionId }) => {
  const { locale } = useI18n();
  const isZh = locale === 'zh';
  const localeRef = useRef(locale);
  localeRef.current = locale;

  const terminalRef = useRef<HTMLDivElement>(null);
  const [connState, setConnState] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');

  useEffect(() => {
    if (!terminalRef.current || typeof window === 'undefined') return;

    setConnState('connecting');

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13.5,
      lineHeight: 1.35,
      fontFamily: '"SF Mono", Menlo, Monaco, "Courier New", monospace',
      theme: {
        background: '#060913',
        foreground: '#e2e8f0',
        cursor: '#38bdf8',
        cursorAccent: '#060913',
        selectionBackground: 'rgba(59, 130, 246, 0.35)',
        black: '#0f172a',
        red: '#fb7185',
        green: '#34d399',
        yellow: '#fbbf24',
        blue: '#60a5fa',
        magenta: '#c084fc',
        cyan: '#22d3ee',
        white: '#f1f5f9',
      },
    });

    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(terminalRef.current);
    fitAddon.fit();

    const token = safeReadStorage('token') || '';
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/sessions/${sessionId}/terminal?token=${token}`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      setConnState('connected');
      fitAddon.fit();
      ws.send(JSON.stringify({ cols: term.cols, rows: term.rows }));
    };

    ws.onclose = () => {
      setConnState('disconnected');
    };

    ws.onerror = () => {
      setConnState('disconnected');
    };

    ws.onmessage = (event) => {
      const zh = localeRef.current === 'zh';
      try {
        const msg = JSON.parse(event.data);
        if (msg.data) {
          term.write(formatTerminalOutput(String(msg.data), zh));
        }
      } catch {
        term.write(formatTerminalOutput(String(event.data), zh));
      }
    };

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ data }));
      }
    });

    const handleResize = () => {
      try {
        fitAddon.fit();
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ cols: term.cols, rows: term.rows }));
        }
      } catch {
        // Ignore transient resize errors while unmounting
      }
    };

    window.addEventListener('resize', handleResize);
    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined' && terminalRef.current) {
      observer = new ResizeObserver(() => {
        handleResize();
      });
      observer.observe(terminalRef.current);
    }

    return () => {
      window.removeEventListener('resize', handleResize);
      observer?.disconnect();
      ws.close();
      term.dispose();
    };
  }, [sessionId]);

  return (
    <div className="h-full w-full flex flex-col bg-[#060913]">
      <div className="h-8 px-3.5 border-b border-white/[0.06] bg-slate-950/70 flex items-center justify-between text-[11px] text-slate-400 select-none shrink-0">
        <div className="flex items-center gap-2 font-mono">
          <TerminalIcon className="w-3.5 h-3.5 text-sky-400" />
          <span className="text-slate-300">/content</span>
          <span className="text-slate-600">·</span>
          <span className="text-slate-500">bash</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              connState === 'connected'
                ? 'bg-emerald-400 animate-pulse'
                : connState === 'connecting'
                  ? 'bg-amber-400 animate-pulse'
                  : 'bg-rose-400'
            }`}
          />
          <span>
            {connState === 'connected'
              ? isZh
                ? 'PTY 已连接'
                : 'PTY Connected'
              : connState === 'connecting'
                ? isZh
                  ? '正在建立终端通道...'
                  : 'Connecting PTY...'
                : isZh
                  ? '终端连接已断开'
                  : 'Disconnected'}
          </span>
        </div>
      </div>
      <div ref={terminalRef} className="flex-1 min-h-0 w-full bg-[#060913] p-2" />
    </div>
  );
};
