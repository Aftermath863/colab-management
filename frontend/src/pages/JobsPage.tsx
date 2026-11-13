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
  Cpu,
  FileCode,
} from 'lucide-react';
import { Job, JobCreatePayload, Template } from '../types';
import { api } from '../services/api';

export const JobsPage: React.FC = () => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>('');

  // Submit Modal
  const [isSubmitModalOpen, setIsSubmitModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formName, setFormName] = useState('');
  const [formAccelerator, setFormAccelerator] = useState('T4');
  const [formAllowFallback, setFormAllowFallback] = useState(true);
  const [formTemplateId, setFormTemplateId] = useState('');
  const [formJobType, setFormJobType] = useState<'python' | 'notebook' | 'shell'>('python');
  const [formEntry, setFormEntry] = useState('');
  const [formArgs, setFormArgs] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  // Log Viewer Modal
  const [activeLogJob, setActiveLogJob] = useState<Job | null>(null);
  const [logContent, setLogContent] = useState<string>('');
  const [logLoading, setLogLoading] = useState(false);
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
      setFormError('Please fill in job name and entry command/path.');
      return;
    }

    setSubmitting(true);
    setFormError(null);

    try {
      const argsArray = formArgs
        .trim()
        .split(/\s+/)
        .filter((a) => a.length > 0);

      const payload: JobCreatePayload = {
        name: formName.trim(),
        target_accelerator: formAccelerator,
        allow_fallback: formAllowFallback,
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
      setFormError(err.message || 'Failed to submit job');
    } finally {
      setSubmitting(false);
    }
  };

  const resetForm = () => {
    setFormName('');
    setFormAccelerator('T4');
    setFormAllowFallback(true);
    setFormTemplateId('');
    setFormJobType('python');
    setFormEntry('');
    setFormArgs('');
    setFormError(null);
  };

  const handleCancelJob = async (jobId: string) => {
    if (!confirm('Are you sure you want to cancel this job?')) return;
    try {
      await api.cancelJob(jobId);
      fetchJobs();
    } catch (err: any) {
      alert(`Failed to cancel job: ${err.message}`);
    }
  };

  const copyLogsToClipboard = () => {
    navigator.clipboard.writeText(logContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const renderStatusBadge = (status: Job['status']) => {
    switch (status) {
      case 'QUEUED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-950/60 text-amber-400 border border-amber-800/50">
            <Clock className="w-3 h-3" /> Queued
          </span>
        );
      case 'RUNNING':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-sky-950/70 text-sky-400 border border-sky-800/60 animate-pulse">
            <span className="w-2 h-2 rounded-full bg-sky-400"></span> Running
          </span>
        );
      case 'COMPLETED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-950/60 text-emerald-400 border border-emerald-800/50">
            <CheckCircle2 className="w-3 h-3" /> Completed
          </span>
        );
      case 'FAILED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-rose-950/60 text-rose-400 border border-rose-800/50">
            <AlertCircle className="w-3 h-3" /> Failed
          </span>
        );
      case 'OOM_KILLED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-950/80 text-red-300 border border-red-700/60 font-mono">
            <AlertCircle className="w-3 h-3" /> OOM (137)
          </span>
        );
      case 'CANCELLED':
        return (
          <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-slate-800 text-slate-400 border border-slate-700">
            <XCircle className="w-3 h-3" /> Cancelled
          </span>
        );
      default:
        return <span className="text-xs text-slate-400">{status}</span>;
    }
  };

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      {/* Top Banner & Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2.5">
            <PlaySquare className="w-6 h-6 text-indigo-400" />
            Async Training & Batch Jobs
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Dispatch asynchronous Python scripts, Jupyter notebooks, and shell tasks across available Colab GPU/TPU accelerators.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={fetchJobs}
            className="p-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-slate-300 transition"
            title="Refresh jobs"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={() => setIsSubmitModalOpen(true)}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition shadow"
          >
            <Plus className="w-4 h-4" />
            Submit Job
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="flex items-center gap-3 bg-slate-900 p-3 rounded-xl border border-slate-800 text-xs text-slate-300">
        <span className="text-slate-400 font-medium">Filter Status:</span>
        {['', 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'OOM_KILLED', 'CANCELLED'].map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1 rounded-lg transition font-medium ${
              statusFilter === s
                ? 'bg-indigo-600 text-white'
                : 'bg-slate-800 text-slate-400 hover:text-white'
            }`}
          >
            {s || 'All Jobs'}
          </button>
        ))}
      </div>

      {/* Jobs Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-slate-300">
            <thead className="bg-slate-950 text-xs uppercase text-slate-400 font-semibold border-b border-slate-800">
              <tr>
                <th className="px-6 py-3.5">Job Name & ID</th>
                <th className="px-6 py-3.5">Type</th>
                <th className="px-6 py-3.5">Accelerator</th>
                <th className="px-6 py-3.5">Status</th>
                <th className="px-6 py-3.5">Exit Code</th>
                <th className="px-6 py-3.5">Session / Timestamps</th>
                <th className="px-6 py-3.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {jobs.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-6 py-12 text-center text-slate-500">
                    No jobs found. Click &quot;Submit Job&quot; to queue an asynchronous training or notebook run.
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
                      <span className="inline-flex items-center gap-1 font-mono text-xs px-2 py-0.5 rounded bg-slate-800 text-indigo-300 border border-slate-700">
                        <FileCode className="w-3 h-3 text-indigo-400" />
                        {job.job_type}
                      </span>
                    </td>

                    {/* Accelerator */}
                    <td className="px-6 py-4">
                      <div className="flex items-center gap-1.5">
                        <span className="px-2 py-0.5 text-xs font-mono bg-blue-950 text-blue-300 border border-blue-800/50 rounded font-semibold">
                          {job.target_accelerator}
                        </span>
                        {job.allow_fallback && (
                          <span className="text-[10px] text-slate-400" title="Automatic fallback enabled">
                            (fallback)
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
                        <div className="text-slate-500 mb-0.5">Dynamic ephemeral</div>
                      )}
                      <div>{job.started_at || 'Pending start'}</div>
                    </td>

                    {/* Actions */}
                    <td className="px-6 py-4 text-right space-x-2">
                      {/* Log stream view */}
                      <button
                        onClick={() => {
                          setActiveLogJob(job);
                          setLogContent('');
                        }}
                        className="p-1.5 bg-slate-800 hover:bg-slate-700 rounded text-slate-300 hover:text-white transition inline-flex items-center gap-1 text-xs"
                        title="View live run.log"
                      >
                        <Terminal className="w-3.5 h-3.5 text-emerald-400" />
                        <span>Logs</span>
                      </button>

                      {/* Download notebook if output available */}
                      {job.job_type === 'notebook' && (job.status === 'COMPLETED' || job.output_notebook_path) && (
                        <a
                          href={api.getJobOutputUrl(job.id)}
                          download
                          className="p-1.5 bg-indigo-950/60 hover:bg-indigo-900/80 border border-indigo-800/50 rounded text-indigo-300 hover:text-white transition inline-flex items-center gap-1 text-xs"
                          title="Download executed .ipynb"
                        >
                          <Download className="w-3.5 h-3.5 text-indigo-400" />
                          <span>.ipynb</span>
                        </a>
                      )}

                      {/* Cancel button */}
                      {(job.status === 'QUEUED' || job.status === 'RUNNING') && (
                        <button
                          onClick={() => handleCancelJob(job.id)}
                          className="p-1.5 bg-rose-950/50 hover:bg-rose-900/80 border border-rose-800/50 rounded text-rose-300 hover:text-white transition inline-flex items-center gap-1 text-xs"
                          title="Cancel Job"
                        >
                          <XCircle className="w-3.5 h-3.5 text-rose-400" />
                          <span>Cancel</span>
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
                Submit Async Training Job
              </h2>
              <button
                onClick={() => setIsSubmitModalOpen(false)}
                className="text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>

            {formError && (
              <div className="p-3 bg-rose-950/50 border border-rose-800/60 rounded-lg text-rose-300 text-xs">
                {formError}
              </div>
            )}

            <form onSubmit={handleSubmitJob} className="space-y-4 text-xs">
              {/* Job Name */}
              <div>
                <label className="block text-slate-300 font-medium mb-1">Job Name</label>
                <input
                  type="text"
                  placeholder="e.g. Llama-3-LoRA-FineTune"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  required
                />
              </div>

              {/* Accelerator & Fallback */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-300 font-medium mb-1">Target Accelerator</label>
                  <select
                    value={formAccelerator}
                    onChange={(e) => setFormAccelerator(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
                  >
                    <option value="T4">Tesla T4 (Standard)</option>
                    <option value="L4">NVIDIA L4 (High-Mem)</option>
                    <option value="A100">NVIDIA A100 (40GB)</option>
                    <option value="H100">NVIDIA H100 (80GB)</option>
                    <option value="V5E1">TPU v5e</option>
                  </select>
                </div>
                <div className="flex items-center pt-5">
                  <label className="flex items-center gap-2 text-slate-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={formAllowFallback}
                      onChange={(e) => setFormAllowFallback(e.target.checked)}
                      className="rounded bg-slate-950 border-slate-800 text-indigo-600 focus:ring-0"
                    />
                    <span>Allow Quota Fallback</span>
                  </label>
                </div>
              </div>

              {/* Job Type */}
              <div>
                <label className="block text-slate-300 font-medium mb-1.5">Execution Type</label>
                <div className="grid grid-cols-3 gap-2">
                  {(['python', 'notebook', 'shell'] as const).map((type) => (
                    <button
                      type="button"
                      key={type}
                      onClick={() => setFormJobType(type)}
                      className={`py-2 px-3 rounded-lg border text-center transition capitalize font-medium ${
                        formJobType === type
                          ? 'bg-indigo-600/30 border-indigo-500 text-indigo-300'
                          : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-white'
                      }`}
                    >
                      {type === 'python' ? 'Python (.py)' : type === 'notebook' ? 'Notebook (.ipynb)' : 'Shell Command'}
                    </button>
                  ))}
                </div>
              </div>

              {/* Entry Path or Command */}
              <div>
                <label className="block text-slate-300 font-medium mb-1">
                  {formJobType === 'python'
                    ? 'Python Script Path (in /content or ./workspace)'
                    : formJobType === 'notebook'
                    ? 'Notebook File Path (.ipynb)'
                    : 'Shell Command String'}
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
                <label className="block text-slate-300 font-medium mb-1">CLI Arguments (Optional)</label>
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
                <label className="block text-slate-300 font-medium mb-1">Environment Template (Optional)</label>
                <select
                  value={formTemplateId}
                  onChange={(e) => setFormTemplateId(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
                >
                  <option value="">None (Standard Base Runtime)</option>
                  {templates.map((tpl) => (
                    <option key={tpl.id} value={tpl.id}>
                      {tpl.name} ({tpl.packages.length} packages, {tpl.secret_ids.length} secrets)
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
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition flex items-center gap-1.5 font-medium disabled:opacity-50"
                >
                  {submitting ? 'Submitting...' : 'Queue Job'}
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
                  <span>Auto-scroll</span>
                </label>
                <button
                  onClick={copyLogsToClipboard}
                  className="p-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded transition flex items-center gap-1 text-xs"
                  title="Copy log text"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>{copied ? 'Copied' : 'Copy'}</span>
                </button>
                <button
                  onClick={() => setActiveLogJob(null)}
                  className="p-1 text-slate-400 hover:text-white"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Monospace Log Viewer */}
            <div className="flex-1 bg-black p-4 overflow-y-auto font-mono text-xs text-emerald-400 whitespace-pre-wrap leading-relaxed select-text">
              {logContent || 'Waiting for log stream...'}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
