import React, { useEffect, useState } from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';
import { Activity, Cpu, HardDrive, RefreshCw } from 'lucide-react';
import { TelemetryResponse, TelemetrySample } from '../types';
import { api } from '../services/api';
import { useI18n } from '../i18n';

interface TelemetryChartsProps {
  sessionId: string;
}

export const TelemetryCharts: React.FC<TelemetryChartsProps> = ({ sessionId }) => {
  const { t, te, formatKernelState } = useI18n();
  const [data, setData] = useState<TelemetrySample[]>([]);
  const [latest, setLatest] = useState<TelemetrySample | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTelemetry = async () => {
    try {
      const res: TelemetryResponse = await api.getSessionTelemetry(sessionId, 60);
      setData(res.history || []);
      setLatest(res.latest || (res.history.length > 0 ? res.history[res.history.length - 1] : null));
      setError(null);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch telemetry metrics');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTelemetry();
    const interval = setInterval(fetchTelemetry, 5000);
    return () => clearInterval(interval);
  }, [sessionId]);

  const formattedData = data.map((d, index) => {
    const timeLabel = d.ts ? d.ts.split(' ')[1] || d.ts : `#${index + 1}`;
    const vramPct =
      d.vram_used_mb && d.vram_total_mb && d.vram_total_mb > 0
        ? Math.round((d.vram_used_mb / d.vram_total_mb) * 100)
        : 0;
    const ramPct =
      d.ram_used_mb && d.ram_total_mb && d.ram_total_mb > 0
        ? Math.round((d.ram_used_mb / d.ram_total_mb) * 100)
        : 0;
    const diskPct =
      d.disk_used_gb && d.disk_total_gb && d.disk_total_gb > 0
        ? Math.round((d.disk_used_gb / d.disk_total_gb) * 100)
        : 0;

    return {
      ...d,
      timeLabel,
      vramPct,
      ramPct,
      diskPct,
      gpu_util_pct: d.gpu_util_pct ?? 0,
      cpu_pct: d.cpu_pct ?? 0,
    };
  });

  if (loading && data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-12 text-slate-400">
        <RefreshCw className="w-8 h-8 animate-spin mb-3 text-indigo-400" />
        <p className="text-sm">{t('telemetry.connecting')}</p>
      </div>
    );
  }

  if (error && data.length === 0) {
    return (
      <div className="p-6 bg-rose-950/30 border border-rose-800/40 rounded-xl text-rose-300 text-sm">
        <p className="font-semibold mb-1">{t('telemetry.unavailable')}</p>
        <p>{te(error)}</p>
      </div>
    );
  }

  const na = t('common.na');

  return (
    <div className="space-y-6">
      {/* Overview Stat Badges */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* GPU & VRAM */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1">
            <span className="flex items-center gap-1.5 font-medium">
              <Activity className="w-4 h-4 text-emerald-400" /> {t('telemetry.gpuUtil')}
            </span>
            <span className="text-emerald-400 font-semibold">{latest?.gpu_name || t('telemetry.gpuDefault')}</span>
          </div>
          <div className="text-2xl font-bold text-white mb-1">
            {latest?.gpu_util_pct !== null && latest?.gpu_util_pct !== undefined
              ? `${Math.round(latest.gpu_util_pct)}%`
              : na}
          </div>
          <div className="text-xs text-slate-400 flex justify-between">
            <span>{t('telemetry.vram')}</span>
            <span className="text-slate-300 font-mono">
              {latest?.vram_used_mb !== null && latest?.vram_used_mb !== undefined
                ? `${Math.round(latest.vram_used_mb)} / ${Math.round(latest.vram_total_mb || 0)} MB`
                : na}
            </span>
          </div>
        </div>

        {/* CPU */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1">
            <span className="flex items-center gap-1.5 font-medium">
              <Cpu className="w-4 h-4 text-sky-400" /> {t('telemetry.cpuLoad')}
            </span>
            <span className="text-sky-400 font-semibold">{t('telemetry.remoteVm')}</span>
          </div>
          <div className="text-2xl font-bold text-white mb-1">
            {latest?.cpu_pct !== null && latest?.cpu_pct !== undefined ? `${Math.round(latest.cpu_pct)}%` : '0%'}
          </div>
          <div className="text-xs text-slate-400 flex justify-between">
            <span>{t('telemetry.ram')}</span>
            <span className="text-slate-300 font-mono">
              {latest?.ram_used_mb !== null && latest?.ram_used_mb !== undefined
                ? `${Math.round(latest.ram_used_mb)} / ${Math.round(latest.ram_total_mb || 0)} MB`
                : na}
            </span>
          </div>
        </div>

        {/* Disk */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1">
            <span className="flex items-center gap-1.5 font-medium">
              <HardDrive className="w-4 h-4 text-amber-400" /> {t('telemetry.disk')}
            </span>
            <span className="text-amber-400 font-semibold">{t('telemetry.mounted')}</span>
          </div>
          <div className="text-2xl font-bold text-white mb-1">
            {latest?.disk_used_gb !== null && latest?.disk_used_gb !== undefined
              ? `${latest.disk_used_gb} GB`
              : na}
          </div>
          <div className="text-xs text-slate-400 flex justify-between">
            <span>{t('telemetry.capacity')}</span>
            <span className="text-slate-300 font-mono">
              {latest?.disk_total_gb ? `${latest.disk_total_gb} GB` : na}
            </span>
          </div>
        </div>

        {/* Kernel & Status */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1">
            <span className="flex items-center gap-1.5 font-medium">
              <RefreshCw className="w-4 h-4 text-indigo-400" /> {t('telemetry.kernelState')}
            </span>
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></span>
          </div>
          <div className="text-2xl font-bold text-white mb-1 capitalize">
            {formatKernelState(latest?.kernel_state)}
          </div>
          <div className="text-xs text-slate-400 flex justify-between">
            <span>{t('telemetry.lastProbe')}</span>
            <span className="text-slate-300 font-mono">
              {latest?.ts ? latest.ts.split(' ')[1] : t('telemetry.justNow')}
            </span>
          </div>
        </div>
      </div>

      {/* Real-time Time Series Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* GPU & VRAM Utilization Chart */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-500"></span>
                {t('telemetry.gpuChartTitle')}
              </h3>
              <p className="text-xs text-slate-400">{t('telemetry.gpuChartDesc')}</p>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <span className="flex items-center gap-1 text-emerald-400">
                <span className="w-2 h-2 rounded bg-emerald-500 inline-block"></span> {t('telemetry.gpuPct')}
              </span>
              <span className="flex items-center gap-1 text-indigo-400">
                <span className="w-2 h-2 rounded bg-indigo-500 inline-block"></span> {t('telemetry.vramPct')}
              </span>
            </div>
          </div>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={formattedData}>
                <defs>
                  <linearGradient id="gpuGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0.0} />
                  </linearGradient>
                  <linearGradient id="vramGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.4} />
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="timeLabel" stroke="#64748b" tick={{ fontSize: 11 }} />
                <YAxis domain={[0, 100]} stroke="#64748b" tick={{ fontSize: 11 }} unit="%" />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderRadius: '0.5rem',
                    fontSize: '12px',
                    color: '#f8fafc',
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="gpu_util_pct"
                  name={t('telemetry.gpuPct')}
                  stroke="#10b981"
                  strokeWidth={2}
                  fillOpacity={1}
                  fill="url(#gpuGrad)"
                />
                <Area
                  type="monotone"
                  dataKey="vramPct"
                  name={t('telemetry.vramPct')}
                  stroke="#6366f1"
                  strokeWidth={2}
                  fillOpacity={1}
                  fill="url(#vramGrad)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* CPU & Memory Utilization Chart */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-sky-500"></span>
                {t('telemetry.cpuChartTitle')}
              </h3>
              <p className="text-xs text-slate-400">{t('telemetry.cpuChartDesc')}</p>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <span className="flex items-center gap-1 text-sky-400">
                <span className="w-2 h-2 rounded bg-sky-500 inline-block"></span> {t('telemetry.cpuPct')}
              </span>
              <span className="flex items-center gap-1 text-amber-400">
                <span className="w-2 h-2 rounded bg-amber-500 inline-block"></span> {t('telemetry.ramPct')}
              </span>
            </div>
          </div>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={formattedData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="timeLabel" stroke="#64748b" tick={{ fontSize: 11 }} />
                <YAxis domain={[0, 100]} stroke="#64748b" tick={{ fontSize: 11 }} unit="%" />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#0f172a',
                    borderColor: '#334155',
                    borderRadius: '0.5rem',
                    fontSize: '12px',
                    color: '#f8fafc',
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="cpu_pct"
                  name={t('telemetry.cpuPct')}
                  stroke="#0284c7"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="ramPct"
                  name={t('telemetry.ramPct')}
                  stroke="#f59e0b"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </div>
  );
};
