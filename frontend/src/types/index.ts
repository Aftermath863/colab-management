export interface Account {
  id: string;
  email: string;
  display_name?: string;
  ccu_balance: number;
  burn_rate_hourly: number;
  active_assignments: number;
  warning_ccu: number;
  hard_stop_ccu: number;
  priority: number;
  status: 'ACTIVE' | 'AUTH_EXPIRED' | 'COOLDOWN';
  last_synced_at?: string;
}

export interface Session {
  id: string;
  name: string;
  account_id: string;
  account_email?: string;
  notebook_hash: string;
  endpoint: string;
  proxy_url: string;
  accelerator: string;
  shape: string;
  lifecycle_policy: 'interactive' | 'keep_alive' | 'ephemeral';
  idle_timeout_sec: number;
  ssh_local_port?: number;
  workspace_subpath: string;
  remote_workdir: string;
  template_id?: string;
  snapshot_id?: string;
  status: 'PROVISIONING' | 'BOOTSTRAPPING' | 'READY' | 'BUSY' | 'ORPHAN' | 'STOPPED' | 'LOST' | 'FAILED';
  created_at: string;
  stopped_at?: string;
  colab_web_url?: string;
}

export interface ContentItem {
  name: string;
  path: string;
  type: 'directory' | 'file';
  size?: number;
  mtime?: string;
  format?: 'base64' | 'json' | 'text';
  content?: any;
}

export interface Template {
  id: string;
  name: string;
  packages: string[];
  secret_ids: string[];
  storage_profile_ids: string[];
  startup_script: string;
  updated_at: string;
}

export interface Secret {
  id: string;
  key_name: string;
  description: string;
  created_at: string;
}

export interface StorageProfile {
  id: string;
  name: string;
  type: 'gdrive' | 's3' | 'hf';
  mount_path: string;
  config?: Record<string, any>;
  created_at?: string;
}

export interface SSHConfig {
  session_id: string;
  host: string;
  port?: number;
  username: string;
  connection_command: string;
  vscode_config: string;
}

export interface SyncFileDetail {
  path: string;
  action: 'uploaded' | 'downloaded' | 'skipped' | 'deleted' | 'error';
  size: number;
  reason: string;
  error?: string;
}

export interface SyncResult {
  session_id: string;
  action: 'push' | 'pull';
  dry_run: boolean;
  files_scanned: number;
  files_uploaded?: number;
  files_downloaded?: number;
  files_skipped: number;
  files_deleted?: number;
  total_bytes_transferred: number;
  duration_ms: number;
  details: SyncFileDetail[];
  success: boolean;
  error?: string;
}


export interface Job {
  id: string;
  name: string;
  session_id?: string;
  target_accelerator: string;
  target_shape?: 'standard' | 'hm' | string;
  allow_fallback: boolean;
  template_id?: string;
  job_type: 'python' | 'notebook' | 'shell';
  entry_path_or_cmd: string;
  args: string[];
  status: 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'OOM_KILLED';
  exit_code?: number;
  log_path: string;
  output_notebook_path?: string;
  started_at?: string;
  finished_at?: string;
}

export interface JobCreatePayload {
  name: string;
  target_accelerator: string;
  target_shape?: 'standard' | 'hm';
  allow_fallback?: boolean;
  template_id?: string;
  job_type: 'python' | 'notebook' | 'shell';
  entry_path_or_cmd: string;
  args?: string[];
}

export interface TelemetrySample {
  id?: number;
  session_id: string;
  ts: string;
  kernel_state: string;
  cpu_pct: number;
  ram_used_mb: number;
  ram_total_mb: number;
  disk_used_gb: number;
  disk_total_gb: number;
  gpu_name?: string;
  gpu_util_pct?: number;
  vram_used_mb?: number;
  vram_total_mb?: number;
}

export interface TelemetryResponse {
  session_id: string;
  latest?: TelemetrySample;
  history: TelemetrySample[];
}

export interface NotificationChannel {
  id: string;
  name: string;
  provider: 'feishu' | 'dingtalk' | 'wecom' | 'telegram' | 'bark' | 'slack' | 'webhook';
  events: string[];
  enabled: boolean;
  config_masked?: Record<string, any>;
  created_at?: string;
}

export interface NotificationChannelCreatePayload {
  name: string;
  provider: 'feishu' | 'dingtalk' | 'wecom' | 'telegram' | 'bark' | 'slack' | 'webhook';
  config: Record<string, any>;
  events: string[];
  enabled?: boolean;
}

export type SnapshotMode = 'hybrid' | 'full_binary';
export type SnapshotStatus = 'CREATING' | 'READY' | 'FAILED';

export interface Snapshot {
  id: string;
  name: string;
  description: string;
  account_id: string;
  account_email?: string;
  source_session_id?: string;
  capture_mode: SnapshotMode;
  drive_folder_path: string;
  python_version: string;
  accelerator: string;
  shape: string;
  apt_package_count: number;
  pip_package_count: number;
  workspace_size_bytes: number;
  pylibs_size_bytes: number;
  status: SnapshotStatus;
  error_message?: string;
  created_at?: string;
  updated_at?: string;
}

export interface SnapshotTask {
  task_id?: string;
  session_id: string;
  snapshot_id?: string;
  action?: 'save' | 'restore' | 'sync';
  status: 'IDLE' | 'RUNNING' | 'COMPLETED' | 'FAILED';
  step?: string;
  progress_pct?: number;
  message?: string;
  error_message?: string;
  stop_after_save?: boolean;
  capture_mode?: SnapshotMode;
  started_at?: string;
  finished_at?: string;
}

export interface SnapshotActionResponse {
  action_status: 'AUTH_REQUIRED' | 'STARTED' | 'SYNCED' | 'ERROR';
  redirect_url?: string;
  snapshot?: Snapshot;
  snapshots?: Snapshot[];
  task?: SnapshotTask;
  message?: string;
}
