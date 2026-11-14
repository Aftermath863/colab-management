import React, { useState, useEffect, useRef } from 'react';
import {
  PlaySquare,
  Plus,
  RefreshCw,
  Terminal,
  Download,
  XCircle,
  CheckCircle2,
  AlertCircle,
  Clock,
  Copy,
  Check,
  FileCode,
  X,
} from 'lucide-react';
import { Job, JobCreatePayload, Template } from '../types';
import { api } from '../services/api';
import { LocalizedText, TranslationKey, useI18n } from '../i18n';
import {
  ACCELERATOR_IDS,
  AcceleratorId,
  MachineShapeType,
  isHighMemOnlyAccelerator,
  resolveEffectiveShape,
} from '../utils/accelerators';
import {
  triggerHaptic,
  notifyToast,
  copyToClipboard,
  getAcceleratorBadgeClass,
} from '../utils/macDesktop';

const JOB_ACCELERATOR_KEY_MAP: Record<AcceleratorId, TranslationKey> = {
  CPU: 'jobs.accCPU',
  T4: 'jobs.accT4',
  G4: 'jobs.accG4',
  L4: 'jobs.accL4',
  A100: 'jobs.accA100',
  H100: 'jobs.accH100',
  V5E1: 'jobs.accV5E1',
  V6E1: 'jobs.accV6E1',
};

export const JobsPage: React.FC = () => {
  const {
    t,
    tm,
    locale,
    formatActionError,
    formatJobStatus,
    formatJobFilter,
    formatJobType,
    formatShape,
  } = useI18n();
  const isZh = locale === 'zh';

  const [jobs, setJobs] = useState<Job[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>('');

  // Submit Modal
  const [isSubmitModalOpen, setIsSubmitModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formName, setFormName] = useState('');
  const [formAccelerator, setFormAccelerator] = useState('T4');
  const [formPreferredShape, setFormPreferredShape] = useState<MachineShapeType>('standard');
  const [formAllowFallback, setFormAllowFallback] = useState(true);
  const [formTemplateId, setFormTemplateId] = useState('');
  const [formJobType, setFormJobType] = useState<'python' | 'notebook' | 'shell'>('python');
  const [formEntry, setFormEntry] = useState('');
  const [formArgs, setFormArgs] = useState('');
  const [formError, setFormError] = useState<LocalizedText | null>(null);

  // Cancel Confirm Modal
  const [cancelTarget, setCancelTarget] = useState<Job | null>(null);

  // Log Viewer Modal
  const [activeLogJob, setActiveLogJob] = useState<Job | null>(null);
  const [logContent, setLogContent] = useState<string>('');
  const [copied, setCopied] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const logEndRef = useRef<HTMLDivElement>(null);

  const fetchJobs = async () => {
    try {
      const res = await api.getJobs(statusFilter || undefined);
      setJobs(res);
    } catch (err: any) {
      console.error('Failed to fetch jobs:', err);
    } finally {
      setLoading(false);
    }
  };

  const fetchTemplates = async () => {
    try {
      const tpls = await api.getTemplates();
      setTemplates(tpls);
    } catch (err) {
      console.error('Failed to fetch templates:', err);
    }
  };

  useEffect(() => {
    fetchJobs();
    fetchTemplates();
    const interval = setInterval(fetchJobs, 4000);
    return () => clearInterval(interval);
  }, [statusFilter]);

  // Close open modals with Esc
  useEffect(() => {
    if ((!isSubmitModalOpen && !activeLogJob && !cancelTarget) || typeof window === 'undefined') {
      return;
    }
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (cancelTarget) setCancelTarget(null);
      else if (activeLogJob) setActiveLogJob(null);
      else if (isSubmitModalOpen) setIsSubmitModalOpen(false);
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [isSubmitModalOpen, activeLogJob, cancelTarget]);

  // Handle Log Viewer Polling
  useEffect(() => {
    if (!activeLogJob) return;

    let isMounted = true;
    const fetchLog = async () => {
      try {
        const text = await api.getJobLogs(activeLogJob.id);
        if (isMounted) {
          setLogContent(text);
          if (autoScroll && logEndRef.current) {
            logEndRef.current.scrollIntoView({ behavior: 'smooth' });
          }
        }
      } catch (err) {
        console.error('Failed to fetch logs:', err);
      }
    };

    fetchLog();
    let logInterval: any = null;
    if (activeLogJob.status === 'RUNNING' || activeLogJob.status === 'QUEUED') {
      logInterval = setInterval(fetchLog, 2000);
    }

    return () => {
      isMounted = false;
      if (logInterval) clearInterval(logInterval);
    };
  }, [activeLogJob, autoScroll]);

  const resetForm = () => {
    setFormName('');
    setFormAccelerator('T4');
    setFormPreferredShape('standard');
    setFormAllowFallback(true);
    setFormTemplateId('');
    setFormJobType('python');
    setFormEntry('');
    setFormArgs('');
    setFormError(null);
  };

  const handleSubmitJob = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim() || !formEntry.trim()) {
      setFormError({ key: 'jobs.missingFieldsError' });
      return;
    }

    setSubmitting(true);
    setFormError(null);
    triggerHaptic('light');

    try {
      const argsArray = formArgs
        .trim()
        .split(/\s+/)
        .filter((a) => a.length > 0);

      const jobShape = resolveEffectiveShape(formAccelerator, formPreferredShape);
      const isCpu = formAccelerator === 'CPU';

      const payload: JobCreatePayload = {
        name: formName.trim(),
        target_accelerator: formAccelerator,
        target_shape: jobShape,
        allow_fallback: isCpu ? false : formAllowFallback,
        template_id: formTemplateId || undefined,
        job_type: formJobType,
        entry_path_or_cmd: formEntry.trim(),
        args: argsArray,
      };

      const created = await api.createJob(payload);
      setIsSubmitModalOpen(false);
      resetForm();
      triggerHaptic('success');
      notifyToast(
        isZh ? '任务已加入队列' : 'Job Queued',
        `${created.name} · ${created.target_accelerator}`,
        'success'
      );
      void fetchJobs();
    } catch (err: any) {
      triggerHaptic('medium');
      setFormError(err.message ? { raw: err.message } : { key: 'jobs.submitFailed' });
    } finally {
      setSubmitting(false);
    }
  };

  const confirmCancelJob = async () => {
    if (!cancelTarget) return;
    const job = cancelTarget;
    setCancelTarget(null);
    triggerHaptic('medium');
    try {
      await api.cancelJob(job.id);
      triggerHaptic('success');
      notifyToast(isZh ? '任务已取消' : 'Job Cancelled', job.name, 'info');
      void fetchJobs();
    } catch (err: any) {
      triggerHaptic('medium');
      notifyToast(formatActionError('jobs.cancelFailed', err?.message), undefined, 'error');
    }
  };

  const copyLogsToClipboard = async () => {
    const ok = await copyToClipboard(logContent);
    if (ok) {
      triggerHaptic('success');
      setCopied(true);
      notifyToast(isZh ? '日志已复制' : 'Logs Copied', activeLogJob?.name, 'success');
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const renderStatusBadge = (status: Job['status']) => {
    const label = formatJobStatus(status);
    switch (status) {
      case 'QUEUED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-500/15 text-amber-300 border border-amber-400/30">
            <Clock className="w-3 h-3" /> {label}
          </span>
        );
      case 'RUNNING':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-sky-500/15 text-sky-300 border border-sky-400/35">
            <span className="w-2 h-2 rounded-full bg-sky-400 animate-pulse" /> {label}
          </span>
        );
      case 'COMPLETED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/15 text-emerald-300 border border-emerald-400/30">
            <CheckCircle2 className="w-3 h-3" /> {label}
          </span>
        );
      case 'FAILED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-rose-500/15 text-rose-300 border border-rose-400/30">
            <AlertCircle className="w-3 h-3" /> {label}
          </span>
        );
      case 'OOM_KILLED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-500/20 text-red-300 border border-red-400/40 font-mono">
            <AlertCircle className="w-3 h-3" /> {label}
          </span>
        );
      case 'CANCELLED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-white/[0.06] text-slate-400 border border-white/10">
            <XCircle className="w-3 h-3" /> {label}
          </span>
        );
      default:
        return <span className="text-xs text-slate-400">{label}</span>;
    }
  };

  return (
    <div className="mac-page-enter p-4 sm:p-8 max-w-7xl mx-auto space-y-6">
      {/* Top Banner & Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2.5">
            <PlaySquare className="w-6 h-6 text-indigo-400" />
            <span>{t('jobs.title')}</span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">{t('jobs.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2.5">
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              void fetchJobs();
            }}
            className="mac-btn-secondary p-2.5 rounded-xl"
            title={t('jobs.refreshTooltip')}
            aria-label={t('jobs.refreshTooltip')}
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-sky-400' : ''}`} />
          </button>
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              setIsSubmitModalOpen(true);
            }}
            className="mac-btn-primary flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium"
          >
            <Plus className="w-4 h-4" />
            <span>{t('jobs.submitJob')}</span>
          </button>
        </div>
      </div>

      {/* Segmented Filter Bar */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs text-slate-400 font-medium">{t('jobs.filterStatus')}</span>
        <div className="mac-pill-group inline-flex flex-wrap items-center gap-1 p-1 rounded-xl text-xs">
          {['', 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'OOM_KILLED', 'CANCELLED'].map((s) => {
            const active = statusFilter === s;
            return (
              <button
                type="button"
                key={s}
                onClick={() => {
                  triggerHaptic('alignment');
                  setStatusFilter(s);
                }}
                className={`px-3 py-1.5 rounded-lg transition font-medium ${
                  active
                    ? 'mac-pill-active text-white'
                    : 'text-slate-400 hover:text-slate-100 hover:bg-white/[0.04]'
                }`}
              >
                {formatJobFilter(s)}
              </button>
            );
          })}
        </div>
      </div>

      {/* Jobs Glass Table */}
      <div className="mac-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-300">
            <thead className="bg-white/[0.02] text-[11px] uppercase tracking-wider text-slate-400 font-semibold border-b border-white/[0.08]">
              <tr>
                <th className="px-6 py-3.5">{t('jobs.colNameAndId')}</th>
                <th className="px-6 py-3.5">{t('jobs.colType')}</th>
                <th className="px-6 py-3.5">{t('jobs.colAccelerator')}</th>
                <th className="px-6 py-3.5">{t('jobs.colStatus')}</th>
                <th className="px-6 py-3.5">{t('jobs.colExitCode')}</th>
                <th className="px-6 py-3.5">{t('jobs.colSessionTimestamps')}</th>
                <th className="px-6 py-3.5 text-right">{t('jobs.colActions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.06]">
              {jobs.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-6 py-12 text-center text-slate-400">
                    {t('jobs.noJobs')}
                  </td>
                </tr>
              ) : (
                jobs.map((job) => (
                  <tr key={job.id} className="hover:bg-white/[0.03] transition-colors">
                    {/* Name & ID */}
                    <td className="px-6 py-4">
                      <div className="font-semibold text-white">{job.name}</div>
                      <div className="text-xs font-mono text-slate-500 mt-0.5">{job.id}</div>
                    </td>

                    {/* Type badge */}
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center gap-1.5 font-mono text-xs px-2.5 py-0.5 rounded-lg bg-white/[0.05] text-indigo-300 border border-white/[0.08] whitespace-nowrap">
                        <FileCode className="w-3 h-3 text-indigo-400 shrink-0" />
                        {formatJobType(job.job_type)}
                      </span>
                    </td>

                    {/* Accelerator */}
                    <td className="px-6 py-4">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span
                          className={`px-2.5 py-0.5 text-xs font-mono border rounded-lg font-semibold ${getAcceleratorBadgeClass(
                            job.target_accelerator
                          )}`}
                        >
                          {job.target_accelerator} (
                          {formatShape(
                            resolveEffectiveShape(
                              job.target_accelerator,
                              job.target_shape || 'standard'
                            )
                          )}
                          )
                        </span>
                        {job.allow_fallback && job.target_accelerator !== 'CPU' && (
                          <span className="text-[10px] text-slate-400" title={t('jobs.fallbackTooltip')}>
                            {t('jobs.fallbackBadge')}
                          </span>
                        )}
                      </div>
                    </td>

                    {/* Status */}
                    <td className="px-6 py-4">{renderStatusBadge(job.status)}</td>

                    {/* Exit code */}
                    <td className="px-6 py-4 font-mono text-xs tabular-nums">
                      {job.exit_code !== null && job.exit_code !== undefined ? (
                        <span
                          className={
                            job.exit_code === 0
                              ? 'text-emerald-400'
                              : job.exit_code === 137
                              ? 'text-rose-400 font-bold'
                              : 'text-rose-300'
                          }
                        >
                          {job.exit_code}
                        </span>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>

                    {/* Session / Timestamps */}
                    <td className="px-6 py-4 text-xs text-slate-400 tabular-nums">
                      {job.session_id ? (
                        <div className="font-mono text-slate-300 mb-0.5 truncate max-w-[140px]">
                          {job.session_id}
                        </div>
                      ) : (
                        <div className="text-slate-500 mb-0.5">{t('jobs.dynamicEphemeral')}</div>
                      )}
                      <div>{job.started_at || t('jobs.pendingStart')}</div>
                    </td>

                    {/* Actions */}
                    <td className="px-6 py-4 text-right">
                      <div className="inline-flex items-center justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            triggerHaptic('light');
                            setActiveLogJob(job);
                            setLogContent('');
                          }}
                          className="mac-btn-secondary px-2.5 py-1.5 rounded-lg inline-flex items-center gap-1.5 text-xs"
                          title={t('jobs.logsTooltip')}
                        >
                          <Terminal className="w-3.5 h-3.5 text-emerald-400" />
                          <span>{t('jobs.logsBtn')}</span>
                        </button>

                        {job.job_type === 'notebook' &&
                          (job.status === 'COMPLETED' || job.output_notebook_path) && (
                            <a
                              href={api.getJobOutputUrl(job.id)}
                              download
                              className="px-2.5 py-1.5 bg-indigo-500/15 hover:bg-indigo-500/25 border border-indigo-400/30 rounded-lg text-indigo-200 transition inline-flex items-center gap-1 text-xs"
                              title={t('jobs.ipynbTooltip')}
                            >
                              <Download className="w-3.5 h-3.5 text-indigo-300" />
                              <span>.ipynb</span>
                            </a>
                          )}

                        {(job.status === 'QUEUED' || job.status === 'RUNNING') && (
                          <button
                            type="button"
                            onClick={() => {
                              triggerHaptic('light');
                              setCancelTarget(job);
                            }}
                            className="px-2.5 py-1.5 bg-rose-500/15 hover:bg-rose-500/25 border border-rose-400/30 rounded-lg text-rose-200 transition inline-flex items-center gap-1 text-xs"
                            title={t('jobs.cancelTooltip')}
                          >
                            <XCircle className="w-3.5 h-3.5 text-rose-300" />
                            <span>{t('jobs.cancelBtn')}</span>
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Submit Job Modal */}
      {isSubmitModalOpen && (
        <div
          className="mac-modal-backdrop fixed inset-0 z-50 flex items-center justify-center p-4"
          onClick={() => setIsSubmitModalOpen(false)}
        >
          <div
            className="mac-modal-panel mac-card w-full max-w-lg p-6 space-y-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between pb-3 border-b border-white/[0.08]">
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <Plus className="w-5 h-5 text-indigo-400" />
                <span>{t('jobs.modalSubmitTitle')}</span>
              </h2>
              <button
                type="button"
                onClick={() => setIsSubmitModalOpen(false)}
                title={t('common.close')}
                className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {formError && (
              <div className="p-3 bg-rose-500/10 border border-rose-400/30 rounded-xl text-rose-200 text-xs">
                {tm(formError)}
              </div>
            )}

            <form onSubmit={handleSubmitJob} className="space-y-4 text-xs">
              <div>
                <label className="block text-slate-300 font-medium mb-1">
                  {t('jobs.jobNameLabel')}
                </label>
                <input
                  type="text"
                  placeholder={t('jobs.jobNamePlaceholder')}
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  className="mac-input w-full rounded-xl px-3 py-2 text-white placeholder-slate-500"
                  required
                />
              </div>

              {(() => {
                const jobHighMemOnly = isHighMemOnlyAccelerator(formAccelerator);
                const effectiveJobShape = resolveEffectiveShape(
                  formAccelerator,
                  formPreferredShape
                );
                const isCpuJob = formAccelerator === 'CPU';
                return (
                  <>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-slate-300 font-medium mb-1">
                          {t('jobs.targetAcceleratorLabel')}
                        </label>
                        <select
                          value={formAccelerator}
                          onChange={(e) => setFormAccelerator(e.target.value)}
                          className="mac-input w-full rounded-xl px-3 py-2 text-white"
                        >
                          {ACCELERATOR_IDS.map((accId) => (
                            <option key={accId} value={accId}>
                              {accId} — {t(JOB_ACCELERATOR_KEY_MAP[accId])}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label className="block text-slate-300 font-medium mb-1">
                          {t('jobs.targetShapeLabel')}
                        </label>
                        <select
                          value={effectiveJobShape}
                          onChange={(e) =>
                            setFormPreferredShape(e.target.value as MachineShapeType)
                          }
                          disabled={jobHighMemOnly}
                          className="mac-input w-full rounded-xl px-3 py-2 text-white disabled:opacity-60 disabled:cursor-not-allowed"
                        >
                          <option value="standard">{t('jobs.shapeStandard')}</option>
                          <option value="hm">{t('jobs.shapeHighRam')}</option>
                        </select>
                      </div>
                    </div>

                    {jobHighMemOnly && (
                      <p className="-mt-2 text-[11px] text-amber-300/90">
                        {t('jobs.highRamOnlyHint')}
                      </p>
                    )}

                    <div className="flex flex-wrap items-center gap-2 pt-0.5">
                      <label
                        className={`flex items-center gap-2 text-slate-300 ${
                          isCpuJob ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={isCpuJob ? false : formAllowFallback}
                          disabled={isCpuJob}
                          onChange={(e) => setFormAllowFallback(e.target.checked)}
                          className="rounded bg-slate-950 border-white/20 text-indigo-500 focus:ring-0 disabled:cursor-not-allowed"
                        />
                        <span>{t('jobs.allowFallbackLabel')}</span>
                      </label>
                      {isCpuJob && (
                        <span className="text-[11px] text-slate-500">
                          {t('jobs.cpuNoFallbackHint')}
                        </span>
                      )}
                    </div>
                  </>
                );
              })()}

              {/* Job Type */}
              <div>
                <label className="block text-slate-300 font-medium mb-1.5">
                  {t('jobs.executionTypeLabel')}
                </label>
                <div className="mac-pill-group grid grid-cols-3 gap-1 p-1 rounded-xl">
                  {(['python', 'notebook', 'shell'] as const).map((type) => (
                    <button
                      type="button"
                      key={type}
                      onClick={() => {
                        triggerHaptic('alignment');
                        setFormJobType(type);
                      }}
                      className={`py-2 px-3 rounded-lg text-center transition font-medium ${
                        formJobType === type
                          ? 'mac-pill-active text-white'
                          : 'text-slate-400 hover:text-white'
                      }`}
                    >
                      {type === 'python'
                        ? t('jobs.typePython')
                        : type === 'notebook'
                        ? t('jobs.typeNotebook')
                        : t('jobs.typeShell')}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-slate-300 font-medium mb-1">
                  {formJobType === 'python'
                    ? t('jobs.entryPythonLabel')
                    : formJobType === 'notebook'
                    ? t('jobs.entryNotebookLabel')
                    : t('jobs.entryShellLabel')}
                </label>
                <input
                  type="text"
                  placeholder={
                    formJobType === 'python'
                      ? 'train_lora.py'
                      : formJobType === 'notebook'
                      ? 'notebooks/train.ipynb'
                      : 'bash run_experiment.sh'
                  }
                  value={formEntry}
                  onChange={(e) => setFormEntry(e.target.value)}
                  className="mac-input w-full rounded-xl px-3 py-2 text-white font-mono placeholder-slate-500"
                  required
                />
              </div>

              <div>
                <label className="block text-slate-300 font-medium mb-1">
                  {t('jobs.cliArgsLabel')}
                </label>
                <input
                  type="text"
                  placeholder="--epochs 5 --lr 2e-4 --batch-size 8"
                  value={formArgs}
                  onChange={(e) => setFormArgs(e.target.value)}
                  className="mac-input w-full rounded-xl px-3 py-2 text-white font-mono placeholder-slate-500"
                />
              </div>

              <div>
                <label className="block text-slate-300 font-medium mb-1">
                  {t('jobs.envTemplateLabel')}
                </label>
                <select
                  value={formTemplateId}
                  onChange={(e) => setFormTemplateId(e.target.value)}
                  className="mac-input w-full rounded-xl px-3 py-2 text-white"
                >
                  <option value="">{t('jobs.envTemplateNone')}</option>
                  {templates.map((tpl) => (
                    <option key={tpl.id} value={tpl.id}>
                      {t('jobs.envTemplateOption', {
                        name: tpl.name,
                        packages: tpl.packages.length,
                        secrets: tpl.secret_ids.length,
                      })}
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex justify-end gap-2.5 pt-3 border-t border-white/[0.08]">
                <button
                  type="button"
                  onClick={() => setIsSubmitModalOpen(false)}
                  className="mac-btn-secondary px-4 py-2 rounded-xl font-medium"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="mac-btn-primary px-4 py-2 rounded-xl flex items-center gap-1.5 font-medium disabled:opacity-50"
                >
                  {submitting ? t('jobs.submitting') : t('jobs.queueJob')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Cancel Job Confirmation Modal */}
      {cancelTarget && (
        <div
          className="mac-modal-backdrop fixed inset-0 z-50 flex items-center justify-center p-4"
          onClick={() => setCancelTarget(null)}
        >
          <div
            className="mac-modal-panel mac-card w-full max-w-md p-6 space-y-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-rose-500/15 border border-rose-400/30 text-rose-300">
                  <XCircle className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-semibold text-white">{t('jobs.cancelTooltip')}</h3>
                  <p className="text-xs text-slate-400 font-mono mt-0.5">{cancelTarget.name}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setCancelTarget(null)}
                aria-label={t('common.close')}
                className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <p className="text-sm text-slate-300">{t('jobs.confirmCancel')}</p>
            <div className="flex justify-end gap-2.5 pt-2 border-t border-white/[0.08]">
              <button
                type="button"
                onClick={() => setCancelTarget(null)}
                className="mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium"
              >
                {t('common.cancel')}
              </button>
              <button
                type="button"
                onClick={() => void confirmCancelJob()}
                className="px-4 py-2 rounded-xl text-xs font-semibold bg-rose-600 hover:bg-rose-500 text-white transition"
              >
                {t('jobs.cancelBtn')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Live Streaming Job Log Viewer Modal */}
      {activeLogJob && (
        <div
          className="mac-modal-backdrop fixed inset-0 z-50 flex items-center justify-center p-4"
          onClick={() => setActiveLogJob(null)}
        >
          <div
            className="mac-modal-panel mac-card w-full max-w-4xl h-[80vh] flex flex-col shadow-2xl overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="mac-glass-heavy h-14 border-b border-white/[0.08] flex items-center justify-between px-5 shrink-0">
              <div className="flex items-center gap-3 min-w-0">
                <div className="p-1.5 rounded-lg bg-emerald-500/15 border border-emerald-400/25">
                  <Terminal className="w-4 h-4 text-emerald-400" />
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-bold text-white flex items-center gap-2 truncate">
                    <span className="truncate">{activeLogJob.name}</span>
                    {renderStatusBadge(activeLogJob.status)}
                  </h3>
                  <p className="text-[11px] font-mono text-slate-400 truncate">
                    {activeLogJob.log_path}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <label className="flex items-center gap-1.5 text-xs text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoScroll}
                    onChange={(e) => setAutoScroll(e.target.checked)}
                    className="rounded bg-slate-950 border-white/20 text-indigo-500"
                  />
                  <span>{t('jobs.autoScroll')}</span>
                </label>
                <button
                  type="button"
                  onClick={() => void copyLogsToClipboard()}
                  className="mac-btn-secondary px-2.5 py-1.5 rounded-lg flex items-center gap-1.5 text-xs"
                  title={t('jobs.copyLogTooltip')}
                >
                  {copied ? (
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                  ) : (
                    <Copy className="w-3.5 h-3.5" />
                  )}
                  <span>{copied ? t('common.copied') : t('common.copy')}</span>
                </button>
                <button
                  type="button"
                  onClick={() => setActiveLogJob(null)}
                  title={t('common.close')}
                  className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* Monospace Streaming Log Body */}
            <div className="flex-1 bg-slate-950/90 p-5 overflow-y-auto font-mono text-xs text-emerald-300/95 whitespace-pre-wrap leading-relaxed select-text">
              {logContent || t('jobs.waitingForLogs')}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
