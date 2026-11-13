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
  const { t, tm, formatActionError, formatJobStatus, formatJobFilter, formatJobType, formatShape } = useI18n();
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

  const handleSubmitJob = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim() || !formEntry.trim()) {
      setFormError({ key: 'jobs.missingFieldsError' });
      return;
    }

    setSubmitting(true);
    setFormError(null);

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

      await api.createJob(payload);
      setIsSubmitModalOpen(false);
      resetForm();
      fetchJobs();
    } catch (err: any) {
      setFormError(err.message ? { raw: err.message } : { key: 'jobs.submitFailed' });
    } finally {
      setSubmitting(false);
    }
  };

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

  const handleCancelJob = async (jobId: string) => {
    if (!confirm(t('jobs.confirmCancel'))) return;
    try {
      await api.cancelJob(jobId);
      fetchJobs();
    } catch (err: any) {
      alert(formatActionError('jobs.cancelFailed', err?.message));
    }
  };

  const copyLogsToClipboard = () => {
    navigator.clipboard.writeText(logContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const renderStatusBadge = (status: Job['status']) => {
    const label = formatJobStatus(status);
    switch (status) {
      case 'QUEUED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-950/60 text-amber-400 border border-amber-800/50">
            <Clock className="w-3 h-3" /> {label}
          </span>
        );
      case 'RUNNING':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-sky-950/70 text-sky-400 border border-sky-800/60 animate-pulse">
            <span className="w-2 h-2 rounded-full bg-sky-400"></span> {label}
          </span>
        );
      case 'COMPLETED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-950/60 text-emerald-400 border border-emerald-800/50">
            <CheckCircle2 className="w-3 h-3" /> {label}
          </span>
        );
      case 'FAILED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-rose-950/60 text-rose-400 border border-rose-800/50">
            <AlertCircle className="w-3 h-3" /> {label}
          </span>
        );
      case 'OOM_KILLED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-950/80 text-red-300 border border-red-700/60 font-mono">
            <AlertCircle className="w-3 h-3" /> {label}
          </span>
        );
      case 'CANCELLED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-400 border border-slate-700">
            <XCircle className="w-3 h-3" /> {label}
          </span>
        );
      default:
        return <span className="text-xs text-slate-400">{label}</span>;
    }
  };

  return (
    <div className="p-4 sm:p-8 max-w-7xl mx-auto space-y-6">
      {/* Top Banner & Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2.5">
            <PlaySquare className="w-6 h-6 text-indigo-400" />
            {t('jobs.title')}
          </h1>
          <p className="text-sm text-slate-400 mt-1">{t('jobs.subtitle')}</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={fetchJobs}
            className="p-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-slate-300 transition"
            title={t('jobs.refreshTooltip')}
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            type="button"
            onClick={() => setIsSubmitModalOpen(true)}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition shadow"
          >
            <Plus className="w-4 h-4" />
            {t('jobs.submitJob')}
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center gap-3 bg-slate-900 p-3 rounded-xl border border-slate-800 text-xs text-slate-300">
        <span className="text-slate-400 font-medium">{t('jobs.filterStatus')}</span>
        {['', 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'OOM_KILLED', 'CANCELLED'].map((s) => (
          <button
            type="button"
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1 rounded-lg transition font-medium ${
              statusFilter === s
                ? 'bg-indigo-600 text-white'
                : 'bg-slate-800 text-slate-400 hover:text-white'
            }`}
          >
            {formatJobFilter(s)}
          </button>
        ))}
      </div>

      {/* Jobs Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-300">
            <thead className="bg-slate-950 text-xs uppercase text-slate-400 font-semibold border-b border-slate-800">
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
            <tbody className="divide-y divide-slate-800">
              {jobs.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-6 py-12 text-center text-slate-500">
                    {t('jobs.noJobs')}
                  </td>
                </tr>
              ) : (
                jobs.map((job) => (
                  <tr key={job.id} className="hover:bg-slate-800/40 transition">
                    {/* Name & ID */}
                    <td className="px-6 py-4">
                      <div className="font-semibold text-white">{job.name}</div>
                      <div className="text-xs font-mono text-slate-500 mt-0.5">{job.id}</div>
                    </td>

                    {/* Type badge */}
                    <td className="px-6 py-4">
                      <span className="inline-flex items-center gap-1 font-mono text-xs px-2 py-0.5 rounded bg-slate-800 text-indigo-300 border border-slate-700 whitespace-nowrap">
                        <FileCode className="w-3 h-3 text-indigo-400 shrink-0" />
                        {formatJobType(job.job_type)}
                      </span>
                    </td>

                    {/* Accelerator */}
                    <td className="px-6 py-4">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="px-2 py-0.5 text-xs font-mono bg-blue-950 text-blue-300 border border-blue-800/50 rounded font-semibold">
                          {job.target_accelerator} ({formatShape(resolveEffectiveShape(job.target_accelerator, job.target_shape || 'standard'))})
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
                    <td className="px-6 py-4 font-mono text-xs">
                      {job.exit_code !== null && job.exit_code !== undefined ? (
                        <span
                          className={
                            job.exit_code === 0
                              ? 'text-emerald-400'
                              : job.exit_code === 137
                              ? 'text-red-400 font-bold'
                              : 'text-rose-400'
                          }
                        >
                          {job.exit_code}
                        </span>
                      ) : (
                        <span className="text-slate-600">-</span>
                      )}
                    </td>

                    {/* Session / Timestamps */}
                    <td className="px-6 py-4 text-xs text-slate-400">
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
                    <td className="px-6 py-4 text-right space-x-2">
                      {/* Log stream view */}
                      <button
                        type="button"
                        onClick={() => {
                          setActiveLogJob(job);
                          setLogContent('');
                        }}
                        className="p-1.5 bg-slate-800 hover:bg-slate-700 rounded text-slate-300 hover:text-white transition inline-flex items-center gap-1 text-xs"
                        title={t('jobs.logsTooltip')}
                      >
                        <Terminal className="w-3.5 h-3.5 text-emerald-400" />
                        <span>{t('jobs.logsBtn')}</span>
                      </button>

                      {/* Download notebook if output available */}
                      {job.job_type === 'notebook' && (job.status === 'COMPLETED' || job.output_notebook_path) && (
                        <a
                          href={api.getJobOutputUrl(job.id)}
                          download
                          className="p-1.5 bg-indigo-950/60 hover:bg-indigo-900/80 border border-indigo-800/50 rounded text-indigo-300 hover:text-white transition inline-flex items-center gap-1 text-xs"
                          title={t('jobs.ipynbTooltip')}
                        >
                          <Download className="w-3.5 h-3.5 text-indigo-400" />
                          <span>.ipynb</span>
                        </a>
                      )}

                      {/* Cancel button */}
                      {(job.status === 'QUEUED' || job.status === 'RUNNING') && (
                        <button
                          type="button"
                          onClick={() => handleCancelJob(job.id)}
                          className="p-1.5 bg-rose-950/50 hover:bg-rose-900/80 border border-rose-800/50 rounded text-rose-300 hover:text-white transition inline-flex items-center gap-1 text-xs"
                          title={t('jobs.cancelTooltip')}
                        >
                          <XCircle className="w-3.5 h-3.5 text-rose-400" />
                          <span>{t('jobs.cancelBtn')}</span>
                        </button>
                      )}
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
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-lg p-6 shadow-xl space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800">
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <Plus className="w-5 h-5 text-indigo-400" />
                {t('jobs.modalSubmitTitle')}
              </h2>
              <button
                type="button"
                onClick={() => setIsSubmitModalOpen(false)}
                title={t('common.close')}
                className="text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>

            {formError && (
              <div className="p-3 bg-rose-950/50 border border-rose-800/60 rounded-lg text-rose-300 text-xs">
                {tm(formError)}
              </div>
            )}

            <form onSubmit={handleSubmitJob} className="space-y-4 text-xs">
              {/* Job Name */}
              <div>
                <label className="block text-slate-300 font-medium mb-1">{t('jobs.jobNameLabel')}</label>
                <input
                  type="text"
                  placeholder={t('jobs.jobNamePlaceholder')}
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  required
                />
              </div>

              {/* Accelerator & Memory Shape */}
              {(() => {
                const jobHighMemOnly = isHighMemOnlyAccelerator(formAccelerator);
                const effectiveJobShape = resolveEffectiveShape(formAccelerator, formPreferredShape);
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
                          className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
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
                          onChange={(e) => setFormPreferredShape(e.target.value as MachineShapeType)}
                          disabled={jobHighMemOnly}
                          className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500 disabled:opacity-60 disabled:cursor-not-allowed"
                        >
                          <option value="standard">{t('jobs.shapeStandard')}</option>
                          <option value="hm">{t('jobs.shapeHighRam')}</option>
                        </select>
                      </div>
                    </div>

                    {jobHighMemOnly && (
                      <p className="-mt-2 text-[11px] text-amber-400/90">
                        {t('jobs.highRamOnlyHint')}
                      </p>
                    )}

                    <div className="flex flex-wrap items-center gap-2 pt-0.5">
                      <label className={`flex items-center gap-2 text-slate-300 ${isCpuJob ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}>
                        <input
                          type="checkbox"
                          checked={isCpuJob ? false : formAllowFallback}
                          disabled={isCpuJob}
                          onChange={(e) => setFormAllowFallback(e.target.checked)}
                          className="rounded bg-slate-950 border-slate-800 text-indigo-600 focus:ring-0 disabled:cursor-not-allowed"
                        />
                        <span>{t('jobs.allowFallbackLabel')}</span>
                      </label>
                      {isCpuJob && (
                        <span className="text-[11px] text-slate-500">{t('jobs.cpuNoFallbackHint')}</span>
                      )}
                    </div>
                  </>
                );
              })()}

              {/* Job Type */}
              <div>
                <label className="block text-slate-300 font-medium mb-1.5">{t('jobs.executionTypeLabel')}</label>
                <div className="grid grid-cols-3 gap-2">
                  {(['python', 'notebook', 'shell'] as const).map((type) => (
                    <button
                      type="button"
                      key={type}
                      onClick={() => setFormJobType(type)}
                      className={`py-2 px-3 rounded-lg border text-center transition font-medium ${
                        formJobType === type
                          ? 'bg-indigo-600/30 border-indigo-500 text-indigo-300'
                          : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-white'
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

              {/* Entry Path or Command */}
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
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  required
                />
              </div>

              {/* Arguments */}
              <div>
                <label className="block text-slate-300 font-medium mb-1">{t('jobs.cliArgsLabel')}</label>
                <input
                  type="text"
                  placeholder="--epochs 5 --lr 2e-4 --batch-size 8"
                  value={formArgs}
                  onChange={(e) => setFormArgs(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
              </div>

              {/* Environment Template */}
              <div>
                <label className="block text-slate-300 font-medium mb-1">{t('jobs.envTemplateLabel')}</label>
                <select
                  value={formTemplateId}
                  onChange={(e) => setFormTemplateId(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
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

              {/* Modal Buttons */}
              <div className="flex justify-end gap-3 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setIsSubmitModalOpen(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition flex items-center gap-1.5 font-medium disabled:opacity-50"
                >
                  {submitting ? t('jobs.submitting') : t('jobs.queueJob')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Live Log Viewer Modal */}
      {activeLogJob && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-4xl h-[80vh] flex flex-col shadow-2xl overflow-hidden">
            {/* Header */}
            <div className="h-14 bg-slate-950 border-b border-slate-800 flex items-center justify-between px-6">
              <div className="flex items-center gap-3">
                <Terminal className="w-5 h-5 text-emerald-400" />
                <div>
                  <h3 className="text-sm font-bold text-white flex items-center gap-2">
                    {activeLogJob.name}
                    {renderStatusBadge(activeLogJob.status)}
                  </h3>
                  <p className="text-xs font-mono text-slate-500">{activeLogJob.log_path}</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoScroll}
                    onChange={(e) => setAutoScroll(e.target.checked)}
                    className="rounded bg-slate-950 border-slate-800 text-indigo-600"
                  />
                  <span>{t('jobs.autoScroll')}</span>
                </label>
                <button
                  type="button"
                  onClick={copyLogsToClipboard}
                  className="p-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded transition flex items-center gap-1 text-xs"
                  title={t('jobs.copyLogTooltip')}
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>{copied ? t('common.copied') : t('common.copy')}</span>
                </button>
                <button
                  type="button"
                  onClick={() => setActiveLogJob(null)}
                  title={t('common.close')}
                  className="p-1 text-slate-400 hover:text-white"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Monospace Log Viewer */}
            <div className="flex-1 bg-black p-4 overflow-y-auto font-mono text-xs text-emerald-400 whitespace-pre-wrap leading-relaxed select-text">
              {logContent || t('jobs.waitingForLogs')}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
