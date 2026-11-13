import React, { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
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
  const localeRef = useRef(locale);
  localeRef.current = locale;
  const terminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!terminalRef.current) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      theme: {
        background: '#0a0f1d',
        foreground: '#e2e8f0',
        cursor: '#38bdf8',
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
      ws.send(JSON.stringify({ cols: term.cols, rows: term.rows }));
    };

    ws.onmessage = (event) => {
      const isZh = localeRef.current === 'zh';
      try {
        const msg = JSON.parse(event.data);
        if (msg.data) {
          term.write(formatTerminalOutput(String(msg.data), isZh));
        }
      } catch {
        term.write(formatTerminalOutput(String(event.data), isZh));
      }
    };

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ data }));
      }
    });

    const handleResize = () => {
      fitAddon.fit();
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ cols: term.cols, rows: term.rows }));
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      ws.close();
      term.dispose();
    };
  }, [sessionId]);

  return <div ref={terminalRef} className="h-full w-full bg-[#0a0f1d] p-2" />;
};
