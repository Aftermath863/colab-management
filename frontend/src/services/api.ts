import {
  Account,
  Session,
  ContentItem,
  Template,
  Secret,
  StorageProfile,
  SSHConfig,
  SyncResult,
  Job,
  JobCreatePayload,
  TelemetrySample,
  TelemetryResponse,
  NotificationChannel,
  NotificationChannelCreatePayload,
} from '../types';

const API_BASE = '/api';

function getHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = localStorage.getItem('token');
  const headers: Record<string, string> = { ...extra };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

function checkResponse(res: Response): Response {
  if (res.status === 401) {
    localStorage.removeItem('token');
    window.dispatchEvent(new Event('auth:unauthorized'));
  }
  return res;
}

export const api = {
  // Auth
  async login(password: string): Promise<string> {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Login failed');
    }
    const data = await res.json();
    localStorage.setItem('token', data.token);
    return data.token;
  },

  async getMe(): Promise<{ authenticated: boolean; role: string; is_default_password: boolean }> {
    const res = checkResponse(await fetch(`${API_BASE}/auth/me`, {
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Not authenticated');
    return res.json();
  },

  logout() {
    localStorage.removeItem('token');
  },

  // Accounts
  async getAccounts(): Promise<Account[]> {
    const res = checkResponse(await fetch(`${API_BASE}/accounts`, {
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to fetch accounts');
    return res.json();
  },

  async startOAuth(): Promise<string> {
    const res = checkResponse(await fetch(`${API_BASE}/accounts/oauth/start`, {
      method: 'POST',
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to start OAuth');
    const data = await res.json();
    return data.auth_url;
  },

  async completeOAuth(code: string): Promise<Account> {
    const res = checkResponse(await fetch(`${API_BASE}/accounts/oauth/complete`, {
      method: 'POST',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ code }),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'OAuth verification failed');
    }
    return res.json();
  },

  async syncAccount(accountId: string): Promise<Account> {
    const res = checkResponse(await fetch(`${API_BASE}/accounts/${accountId}/sync`, {
      method: 'POST',
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to sync account');
    return res.json();
  },

  async deleteAccount(accountId: string): Promise<void> {
    const res = checkResponse(await fetch(`${API_BASE}/accounts/${accountId}`, {
      method: 'DELETE',
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to delete account');
  },

  // Sessions
  async getSessions(statusFilter?: string): Promise<Session[]> {
    const url = statusFilter
      ? `${API_BASE}/sessions?status=${encodeURIComponent(statusFilter)}`
      : `${API_BASE}/sessions`;
    const res = checkResponse(await fetch(url, {
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to fetch sessions');
    return res.json();
  },

  async createSession(params: {
    name: string;
    accelerator: string;
    shape: string;
    lifecycle_policy: string;
    account_id?: string;
    template_id?: string;
  }): Promise<Session> {
    const res = checkResponse(await fetch(`${API_BASE}/sessions`, {
      method: 'POST',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(params),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to create session');
    }
    return res.json();
  },

  async stopSession(sessionId: string): Promise<void> {
    const res = checkResponse(await fetch(`${API_BASE}/sessions/${sessionId}`, {
      method: 'DELETE',
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to stop session');
  },

  async getColabUrl(sessionId: string): Promise<string> {
    const res = checkResponse(await fetch(`${API_BASE}/sessions/${sessionId}/colab-url`, {
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to get Colab deep link');
    const data = await res.json();
    return data.url;
  },

  async getSSHConfig(sessionId: string): Promise<SSHConfig> {
    const res = checkResponse(await fetch(`${API_BASE}/sessions/${sessionId}/ssh-config`, {
      headers: getHeaders(),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to fetch SSH config');
    }
    return res.json();
  },

  // Files
  async listContents(sessionId: string, path: string = ''): Promise<{ content: ContentItem[] }> {
    const clean = path.replace(/^\/+/, '');
    const url = clean
      ? `${API_BASE}/sessions/${sessionId}/contents/${encodeURIComponent(clean).replace(/%2F/g, '/')}`
      : `${API_BASE}/sessions/${sessionId}/contents`;
    const res = checkResponse(await fetch(url, {
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to list contents');
    return res.json();
  },

  async downloadFile(sessionId: string, path: string): Promise<ContentItem> {
    const clean = path.replace(/^\/+/, '');
    const url = `${API_BASE}/sessions/${sessionId}/contents/${encodeURIComponent(clean).replace(/%2F/g, '/')}?content=1`;
    const res = checkResponse(await fetch(url, {
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to load file');
    return res.json();
  },

  async uploadFile(
    sessionId: string,
    path: string,
    contentBase64: string,
    filename?: string
  ): Promise<any> {
    const clean = path.replace(/^\/+/, '');
    const url = `${API_BASE}/sessions/${sessionId}/contents/${encodeURIComponent(clean).replace(/%2F/g, '/')}`;
    const name = filename || clean.split('/').pop() || 'file';
    const res = checkResponse(await fetch(url, {
      method: 'PUT',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        name,
        path: clean,
        type: 'file',
        format: 'base64',
        content: contentBase64,
        chunk: 1,
      }),
    }));
    if (!res.ok) throw new Error('Failed to save file');
    return res.json();
  },

  async deletePath(sessionId: string, path: string): Promise<void> {
    const clean = path.replace(/^\/+/, '');
    const url = `${API_BASE}/sessions/${sessionId}/contents/${encodeURIComponent(clean).replace(/%2F/g, '/')}`;
    const res = checkResponse(await fetch(url, {
      method: 'DELETE',
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to delete path');
  },

  // Workspace Sync
  async pushWorkspaceSync(
    sessionId: string,
    options?: { dry_run?: boolean; delete_missing?: boolean; max_file_size_mb?: number; exclude_patterns?: string[] }
  ): Promise<SyncResult> {
    const res = checkResponse(await fetch(`${API_BASE}/sessions/${sessionId}/sync/push`, {
      method: 'POST',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(options || {}),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Push sync failed');
    }
    return res.json();
  },

  async pullWorkspaceSync(
    sessionId: string,
    options?: { dry_run?: boolean; max_file_size_mb?: number; exclude_patterns?: string[] }
  ): Promise<SyncResult> {
    const res = checkResponse(await fetch(`${API_BASE}/sessions/${sessionId}/sync/pull`, {
      method: 'POST',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(options || {}),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Pull sync failed');
    }
    return res.json();
  },

  async getSyncStatus(sessionId: string): Promise<any> {
    const res = checkResponse(await fetch(`${API_BASE}/sessions/${sessionId}/sync/status`, {
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to fetch sync status');
    return res.json();
  },

  // Templates
  async getTemplates(): Promise<Template[]> {
    const res = checkResponse(await fetch(`${API_BASE}/templates`, {
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to fetch templates');
    return res.json();
  },

  async createTemplate(template: Partial<Template>): Promise<Template> {
    const res = checkResponse(await fetch(`${API_BASE}/templates`, {
      method: 'POST',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(template),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to create template');
    }
    return res.json();
  },

  async updateTemplate(templateId: string, template: Partial<Template>): Promise<Template> {
    const res = checkResponse(await fetch(`${API_BASE}/templates/${templateId}`, {
      method: 'PUT',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(template),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update template');
    }
    return res.json();
  },

  async deleteTemplate(templateId: string): Promise<void> {
    const res = checkResponse(await fetch(`${API_BASE}/templates/${templateId}`, {
      method: 'DELETE',
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to delete template');
  },

  // Secrets Vault
  async getSecrets(): Promise<Secret[]> {
    const res = checkResponse(await fetch(`${API_BASE}/secrets`, {
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to fetch secrets');
    return res.json();
  },

  async createSecret(secret: { key_name: string; value: string; description?: string }): Promise<Secret> {
    const res = checkResponse(await fetch(`${API_BASE}/secrets`, {
      method: 'POST',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(secret),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to create secret');
    }
    return res.json();
  },

  async deleteSecret(secretId: string): Promise<void> {
    const res = checkResponse(await fetch(`${API_BASE}/secrets/${secretId}`, {
      method: 'DELETE',
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to delete secret');
  },

  // Cloud Storage Profiles & Mounting
  async getStorageProfiles(): Promise<StorageProfile[]> {
    const res = checkResponse(await fetch(`${API_BASE}/storage/profiles`, {
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to fetch storage profiles');
    return res.json();
  },

  async createStorageProfile(profile: {
    name: string;
    type: string;
    config: Record<string, any>;
    mount_path?: string;
  }): Promise<StorageProfile> {
    const res = checkResponse(await fetch(`${API_BASE}/storage/profiles`, {
      method: 'POST',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(profile),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to create storage profile');
    }
    return res.json();
  },

  async deleteStorageProfile(profileId: string): Promise<void> {
    const res = checkResponse(await fetch(`${API_BASE}/storage/profiles/${profileId}`, {
      method: 'DELETE',
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to delete storage profile');
  },

  async propagateGDrive(sessionId: string, dryrun: boolean = false): Promise<any> {
    const res = checkResponse(await fetch(`${API_BASE}/storage/gdrive/propagate/${sessionId}`, {
      method: 'POST',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ dryrun }),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Google Drive credential propagation failed');
    }
    return res.json();
  },

  async mountStorage(sessionId: string, profileId: string): Promise<any> {
    const res = checkResponse(await fetch(`${API_BASE}/storage/mount/${sessionId}`, {
      method: 'POST',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ profile_id: profileId }),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to mount storage profile');
    }
    return res.json();
  },

  async verifyMount(sessionId: string, profileId: string): Promise<{ accessible: boolean; mount_path: string }> {
    const res = checkResponse(await fetch(`${API_BASE}/storage/verify/${sessionId}/${profileId}`, {
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to verify mount');
    return res.json();
  },

  // Jobs API
  async getJobs(status?: string): Promise<Job[]> {
    const url = status ? `${API_BASE}/jobs?status=${encodeURIComponent(status)}` : `${API_BASE}/jobs`;
    const res = checkResponse(await fetch(url, { headers: getHeaders() }));
    if (!res.ok) throw new Error('Failed to fetch jobs');
    return res.json();
  },

  async getJob(jobId: string): Promise<Job> {
    const res = checkResponse(await fetch(`${API_BASE}/jobs/${jobId}`, { headers: getHeaders() }));
    if (!res.ok) throw new Error('Failed to fetch job details');
    return res.json();
  },

  async createJob(payload: JobCreatePayload): Promise<Job> {
    const res = checkResponse(await fetch(`${API_BASE}/jobs`, {
      method: 'POST',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to submit job');
    }
    return res.json();
  },

  async cancelJob(jobId: string): Promise<Job> {
    const res = checkResponse(await fetch(`${API_BASE}/jobs/${jobId}/cancel`, {
      method: 'POST',
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to cancel job');
    return res.json();
  },

  async getJobLogs(jobId: string): Promise<string> {
    const res = checkResponse(await fetch(`${API_BASE}/jobs/${jobId}/logs`, { headers: getHeaders() }));
    if (!res.ok) throw new Error('Failed to fetch job logs');
    return res.text();
  },

  getJobOutputUrl(jobId: string): string {
    return `${API_BASE}/jobs/${jobId}/output`;
  },

  // Telemetry API
  async getSessionTelemetry(sessionId: string, limit: number = 100): Promise<TelemetryResponse> {
    const res = checkResponse(await fetch(`${API_BASE}/sessions/${sessionId}/telemetry?limit=${limit}`, { headers: getHeaders() }));
    if (!res.ok) throw new Error('Failed to fetch telemetry metrics');
    return res.json();
  },

  // Notifications API
  async getNotificationChannels(): Promise<NotificationChannel[]> {
    const res = checkResponse(await fetch(`${API_BASE}/notifications/channels`, { headers: getHeaders() }));
    if (!res.ok) throw new Error('Failed to fetch notification channels');
    return res.json();
  },

  async createNotificationChannel(payload: NotificationChannelCreatePayload): Promise<NotificationChannel> {
    const res = checkResponse(await fetch(`${API_BASE}/notifications/channels`, {
      method: 'POST',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    }));
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to create channel');
    }
    return res.json();
  },

  async updateNotificationChannel(id: string, payload: Partial<NotificationChannelCreatePayload>): Promise<NotificationChannel> {
    const res = checkResponse(await fetch(`${API_BASE}/notifications/channels/${id}`, {
      method: 'PUT',
      headers: getHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    }));
    if (!res.ok) throw new Error('Failed to update channel');
    return res.json();
  },

  async deleteNotificationChannel(id: string): Promise<void> {
    const res = checkResponse(await fetch(`${API_BASE}/notifications/channels/${id}`, {
      method: 'DELETE',
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to delete channel');
  },

  async testNotificationChannel(id: string): Promise<{ success: boolean; response: string }> {
    const res = checkResponse(await fetch(`${API_BASE}/notifications/channels/${id}/test`, {
      method: 'POST',
      headers: getHeaders(),
    }));
    if (!res.ok) throw new Error('Failed to test webhook channel');
    return res.json();
  },
};
