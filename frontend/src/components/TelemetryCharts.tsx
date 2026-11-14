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

function handleCardSpotlight(e: React.MouseEvent<HTMLDivElement>) {
  const rect = e.currentTarget.getBoundingClientRect();
  e.currentTarget.style.setProperty('--spot-x', `${e.clientX - rect.left}px`);
  e.currentTarget.style.setProperty('--spot-y', `${e.clientY - rect.top}px`);
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
      <div className="p-6 bg-rose-950/35 border border-rose-500/35 rounded-2xl text-rose-300 text-sm">
        <p className="font-semibold mb-1">{t('telemetry.unavailable')}</p>
        <p>{te(error)}</p>
      </div>
    );
  }

  const na = t('common.na');
  const gpuUtilPct = latest?.gpu_util_pct ?? 0;
  const cpuUtilPct = latest?.cpu_pct ?? 0;
  const diskUtilPct =
    latest?.disk_used_gb && latest?.disk_total_gb && latest.disk_total_gb > 0
      ? Math.min(100, Math.round((latest.disk_used_gb / latest.disk_total_gb) * 100))
      : 0;

  return (
    <div className="space-y-6">
      {/* Overview Stat Badges */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {/* GPU & VRAM */}
        <div className="mac-card mac-card-hover p-4" onMouseMove={handleCardSpotlight}>
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1.5">
            <span className="flex items-center gap-1.5 font-medium">
              <Activity className="w-4 h-4 text-emerald-400" /> {t('telemetry.gpuUtil')}
            </span>
            <span className="text-emerald-300 font-semibold truncate max-w-[120px]">
              {latest?.gpu_name || t('telemetry.gpuDefault')}
            </span>
          </div>
          <div className="text-2xl font-bold text-white mb-2 tabular-nums">
            {latest?.gpu_util_pct !== null && latest?.gpu_util_pct !== undefined
              ? `${Math.round(latest.gpu_util_pct)}%`
              : na}
          </div>
          <div className="h-1.5 w-full rounded-full bg-white/[0.06] overflow-hidden mb-2">
            <div
              className="h-full rounded-full bg-gradient-to-r from-emerald-400 to-teal-400 transition-all duration-500"
              style={{ width: `${Math.min(100, Math.max(0, gpuUtilPct))}%` }}
            />
          </div>
          <div className="text-xs text-slate-400 flex justify-between">
            <span>{t('telemetry.vram')}</span>
            <span className="text-slate-300 font-mono tabular-nums">
              {latest?.vram_used_mb !== null && latest?.vram_used_mb !== undefined
                ? `${Math.round(latest.vram_used_mb)} / ${Math.round(latest.vram_total_mb || 0)} MB`
                : na}
            </span>
          </div>
        </div>

        {/* CPU */}
        <div className="mac-card mac-card-hover p-4" onMouseMove={handleCardSpotlight}>
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1.5">
            <span className="flex items-center gap-1.5 font-medium">
              <Cpu className="w-4 h-4 text-sky-400" /> {t('telemetry.cpuLoad')}
            </span>
            <span className="text-sky-300 font-semibold">{t('telemetry.remoteVm')}</span>
          </div>
          <div className="text-2xl font-bold text-white mb-2 tabular-nums">
            {latest?.cpu_pct !== null && latest?.cpu_pct !== undefined ? `${Math.round(latest.cpu_pct)}%` : '0%'}
          </div>
          <div className="h-1.5 w-full rounded-full bg-white/[0.06] overflow-hidden mb-2">
            <div
              className="h-full rounded-full bg-gradient-to-r from-sky-400 to-indigo-400 transition-all duration-500"
              style={{ width: `${Math.min(100, Math.max(0, cpuUtilPct))}%` }}
            />
          </div>
          <div className="text-xs text-slate-400 flex justify-between">
            <span>{t('telemetry.ram')}</span>
            <span className="text-slate-300 font-mono tabular-nums">
              {latest?.ram_used_mb !== null && latest?.ram_used_mb !== undefined
                ? `${Math.round(latest.ram_used_mb)} / ${Math.round(latest.ram_total_mb || 0)} MB`
                : na}
            </span>
          </div>
        </div>

        {/* Disk */}
        <div className="mac-card mac-card-hover p-4" onMouseMove={handleCardSpotlight}>
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1.5">
            <span className="flex items-center gap-1.5 font-medium">
              <HardDrive className="w-4 h-4 text-amber-400" /> {t('telemetry.disk')}
            </span>
            <span className="text-amber-300 font-semibold">{t('telemetry.mounted')}</span>
          </div>
          <div className="text-2xl font-bold text-white mb-2 tabular-nums">
            {latest?.disk_used_gb !== null && latest?.disk_used_gb !== undefined
              ? `${latest.disk_used_gb} GB`
              : na}
          </div>
          <div className="h-1.5 w-full rounded-full bg-white/[0.06] overflow-hidden mb-2">
            <div
              className="h-full rounded-full bg-gradient-to-r from-amber-400 to-orange-400 transition-all duration-500"
              style={{ width: `${diskUtilPct}%` }}
            />
          </div>
          <div className="text-xs text-slate-400 flex justify-between">
            <span>{t('telemetry.capacity')}</span>
            <span className="text-slate-300 font-mono tabular-nums">
              {latest?.disk_total_gb ? `${latest.disk_total_gb} GB` : na}
            </span>
          </div>
        </div>

        {/* Kernel & Status */}
        <div className="mac-card mac-card-hover p-4" onMouseMove={handleCardSpotlight}>
          <div className="flex items-center justify-between text-xs text-slate-400 mb-1.5">
            <span className="flex items-center gap-1.5 font-medium">
              <RefreshCw className="w-4 h-4 text-indigo-400" /> {t('telemetry.kernelState')}
            </span>
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_10px_rgba(52,211,153,0.7)]" />
          </div>
          <div className="text-2xl font-bold text-white mb-2 capitalize">
            {formatKernelState(latest?.kernel_state)}
          </div>
          <div className="h-1.5 w-full rounded-full bg-white/[0.06] overflow-hidden mb-2">
            <div className="h-full w-full rounded-full bg-gradient-to-r from-indigo-500/70 to-emerald-400/70" />
          </div>
          <div className="text-xs text-slate-400 flex justify-between">
            <span>{t('telemetry.lastProbe')}</span>
            <span className="text-slate-300 font-mono tabular-nums">
              {latest?.ts ? latest.ts.split(' ')[1] : t('telemetry.justNow')}
            </span>
          </div>
        </div>
      </div>

      {/* Real-time Time Series Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* GPU & VRAM Utilization Chart */}
        <div className="mac-card p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
                {t('telemetry.gpuChartTitle')}
              </h3>
              <p className="text-xs text-slate-400">{t('telemetry.gpuChartDesc')}</p>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <span className="flex items-center gap-1 text-emerald-400">
                <span className="w-2 h-2 rounded bg-emerald-500 inline-block" /> {t('telemetry.gpuPct')}
              </span>
              <span className="flex items-center gap-1 text-indigo-400">
                <span className="w-2 h-2 rounded bg-indigo-500 inline-block" /> {t('telemetry.vramPct')}
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
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="timeLabel" stroke="#64748b" tick={{ fontSize: 11 }} />
                <YAxis domain={[0, 100]} stroke="#64748b" tick={{ fontSize: 11 }} unit="%" />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'rgba(15, 23, 42, 0.9)',
                    borderColor: 'rgba(255, 255, 255, 0.12)',
                    borderRadius: '0.75rem',
                    backdropFilter: 'blur(16px)',
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
        <div className="mac-card p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-sky-400" />
                {t('telemetry.cpuChartTitle')}
              </h3>
              <p className="text-xs text-slate-400">{t('telemetry.cpuChartDesc')}</p>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <span className="flex items-center gap-1 text-sky-400">
                <span className="w-2 h-2 rounded bg-sky-500 inline-block" /> {t('telemetry.cpuPct')}
              </span>
              <span className="flex items-center gap-1 text-amber-400">
                <span className="w-2 h-2 rounded bg-amber-500 inline-block" /> {t('telemetry.ramPct')}
              </span>
            </div>
          </div>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={formattedData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="timeLabel" stroke="#64748b" tick={{ fontSize: 11 }} />
                <YAxis domain={[0, 100]} stroke="#64748b" tick={{ fontSize: 11 }} unit="%" />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'rgba(15, 23, 42, 0.9)',
                    borderColor: 'rgba(255, 255, 255, 0.12)',
                    borderRadius: '0.75rem',
                    backdropFilter: 'blur(16px)',
                    fontSize: '12px',
                    color: '#f8fafc',
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="cpu_pct"
                  name={t('telemetry.cpuPct')}
                  stroke="#38bdf8"
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
