import React, { useState, useEffect } from 'react';
import {
  Bell,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  Edit2,
  CheckCircle2,
  AlertCircle,
  Shield,
  MessageSquare,
  Globe,
  Radio,
} from 'lucide-react';
import { NotificationChannel, NotificationChannelCreatePayload } from '../types';
import { api } from '../services/api';

const ALL_EVENTS = [
  { id: 'session.started', label: 'Session Started' },
  { id: 'session.stopped', label: 'Session Stopped' },
  { id: 'quota.warning', label: 'Quota Warning (≤10 CCU)' },
  { id: 'quota.hard_stop', label: 'Quota Hard-Stop (≤2 CCU)' },
  { id: 'job.started', label: 'Job Started' },
  { id: 'job.finished', label: 'Job Finished / Completed' },
  { id: 'job.failed', label: 'Job Failed' },
  { id: 'orphan.discovered', label: 'Orphan Instance Discovered' },
];

export const NotificationsPage: React.FC = () => {
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [loading, setLoading] = useState(true);
  const [testResult, setTestResult] = useState<{ channelId: string; success: boolean; response: string } | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);

  // Modal State
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingChannel, setEditingChannel] = useState<NotificationChannel | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);

  // Form State
  const [name, setName] = useState('');
  const [provider, setProvider] = useState<NotificationChannel['provider']>('feishu');
  const [webhookUrl, setWebhookUrl] = useState('');
  const [secret, setSecret] = useState('');
  const [botToken, setBotToken] = useState('');
  const [chatId, setChatId] = useState('');
  const [deviceKey, setDeviceKey] = useState('');
  const [selectedEvents, setSelectedEvents] = useState<string[]>([
    'quota.warning',
    'quota.hard_stop',
    'job.finished',
    'job.failed',
  ]);
  const [enabled, setEnabled] = useState(true);

  const fetchChannels = async () => {
    try {
      const res = await api.getNotificationChannels();
      setChannels(res);
    } catch (err: any) {
      console.error('Failed to fetch channels:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchChannels();
  }, []);

  const resetForm = () => {
    setName('');
    setProvider('feishu');
    setWebhookUrl('');
    setSecret('');
    setBotToken('');
    setChatId('');
    setDeviceKey('');
    setSelectedEvents(['quota.warning', 'quota.hard_stop', 'job.finished', 'job.failed']);
    setEnabled(true);
    setModalError(null);
    setEditingChannel(null);
  };

  const openCreateModal = () => {
    resetForm();
    setIsModalOpen(true);
  };

  const openEditModal = (ch: NotificationChannel) => {
    setEditingChannel(ch);
    setName(ch.name);
    setProvider(ch.provider);
    setSelectedEvents(ch.events);
    setEnabled(ch.enabled);
    setWebhookUrl('');
    setSecret('');
    setBotToken('');
    setChatId('');
    setDeviceKey('');
    setModalError(null);
    setIsModalOpen(true);
  };

  const handleToggleEvent = (eventId: string) => {
    if (selectedEvents.includes(eventId)) {
      setSelectedEvents(selectedEvents.filter((e) => e !== eventId));
    } else {
      setSelectedEvents([...selectedEvents, eventId]);
    }
  };

  const handleSaveChannel = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setModalError('Please enter a channel name.');
      return;
    }

    const config: Record<string, any> = {};
    if (webhookUrl) config.webhook_url = webhookUrl;
    if (secret) config.secret = secret;
    if (botToken) config.bot_token = botToken;
    if (chatId) config.chat_id = chatId;
    if (deviceKey) config.device_key = deviceKey;

    setSubmitting(true);
    setModalError(null);

    try {
      if (editingChannel) {
        await api.updateNotificationChannel(editingChannel.id, {
          name: name.trim(),
          provider,
          config: Object.keys(config).length > 0 ? config : undefined,
          events: selectedEvents,
          enabled,
        });
      } else {
        await api.createNotificationChannel({
          name: name.trim(),
          provider,
          config,
          events: selectedEvents,
          enabled,
        });
      }

      setIsModalOpen(false);
      resetForm();
      fetchChannels();
    } catch (err: any) {
      setModalError(err.message || 'Failed to save channel');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteChannel = async (id: string, chName: string) => {
    if (!confirm(`Are you sure you want to delete notification channel "${chName}"?`)) return;
    try {
      await api.deleteNotificationChannel(id);
      fetchChannels();
    } catch (err: any) {
      alert(`Failed to delete channel: ${err.message}`);
    }
  };

  const handleTestChannel = async (id: string) => {
    setTestingId(id);
    setTestResult(null);
    try {
      const res = await api.testNotificationChannel(id);
      setTestResult({
        channelId: id,
        success: res.success,
        response: res.response,
      });
    } catch (err: any) {
      setTestResult({
        channelId: id,
        success: false,
        response: err.message || 'Test delivery failed',
      });
    } finally {
      setTestingId(null);
    }
  };

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2.5">
            <Bell className="w-6 h-6 text-amber-400" />
            Multi-Channel Notification Engine
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Dispatch real-time alerts to Feishu, DingTalk, WeChat Work, Telegram, Bark, Slack, and generic webhooks with AES-256-GCM Vault encryption.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={fetchChannels}
            className="p-2 bg-slate-800 hover:bg-slate-700 rounded-lg text-slate-300 transition"
            title="Refresh channels"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={openCreateModal}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition shadow"
          >
            <Plus className="w-4 h-4" />
            Add Channel
          </button>
        </div>
      </div>

      {/* Test Result Toast/Banner */}
      {testResult && (
        <div
          className={`p-4 rounded-xl border flex items-start justify-between gap-3 text-sm ${
            testResult.success
              ? 'bg-emerald-950/40 border-emerald-800/50 text-emerald-300'
              : 'bg-rose-950/40 border-rose-800/50 text-rose-300'
          }`}
        >
          <div className="flex items-start gap-2.5">
            {testResult.success ? (
              <CheckCircle2 className="w-5 h-5 text-emerald-400 flex-shrink-0 mt-0.5" />
            ) : (
              <AlertCircle className="w-5 h-5 text-rose-400 flex-shrink-0 mt-0.5" />
            )}
            <div>
              <p className="font-semibold">
                {testResult.success ? 'Webhook Probe Delivered Successfully' : 'Webhook Probe Failed'}
              </p>
              <p className="text-xs opacity-90 font-mono mt-0.5 break-all">{testResult.response}</p>
            </div>
          </div>
          <button
            onClick={() => setTestResult(null)}
            className="text-slate-400 hover:text-white text-xs font-bold"
          >
            ✕
          </button>
        </div>
      )}

      {/* Channels Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {channels.length === 0 ? (
          <div className="col-span-full bg-slate-900 border border-slate-800 rounded-xl p-12 text-center text-slate-500">
            No notification channels configured yet. Click &quot;Add Channel&quot; to connect a bot or webhook.
          </div>
        ) : (
          channels.map((ch) => (
            <div
              key={ch.id}
              className={`bg-slate-900 border rounded-xl p-5 flex flex-col justify-between transition shadow-sm ${
                ch.enabled ? 'border-slate-800' : 'border-slate-800/50 opacity-60'
              }`}
            >
              <div>
                {/* Card Header */}
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span className="p-2 bg-indigo-950/80 border border-indigo-800/50 rounded-lg text-indigo-400">
                      <MessageSquare className="w-4 h-4" />
                    </span>
                    <div>
                      <h3 className="font-semibold text-white text-sm">{ch.name}</h3>
                      <span className="text-[11px] font-mono uppercase font-bold text-slate-400 tracking-wider">
                        {ch.provider}
                      </span>
                    </div>
                  </div>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
                      ch.enabled
                        ? 'bg-emerald-950 text-emerald-400 border border-emerald-800/50'
                        : 'bg-slate-800 text-slate-400 border border-slate-700'
                    }`}
                  >
                    {ch.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>

                {/* Masked Config Summary */}
                <div className="bg-slate-950 p-2.5 rounded-lg border border-slate-800/80 mb-3 text-xs text-slate-400 font-mono space-y-1">
                  <div className="flex items-center gap-1.5 text-slate-300 font-sans font-medium text-[11px]">
                    <Shield className="w-3.5 h-3.5 text-indigo-400" />
                    AES-256-GCM Vault Protected
                  </div>
                  {ch.config_masked &&
                    Object.entries(ch.config_masked).map(([k, v]) => (
                      <div key={k} className="truncate">
                        <span className="text-slate-500">{k}:</span> <span className="text-slate-300">{String(v)}</span>
                      </div>
                    ))}
                </div>

                {/* Subscribed Events Tags */}
                <div className="mb-4">
                  <span className="text-[11px] text-slate-400 font-medium block mb-1.5">
                    Subscribed Events ({ch.events.length})
                  </span>
                  <div className="flex flex-wrap gap-1">
                    {ch.events.map((ev) => (
                      <span
                        key={ev}
                        className="text-[10px] px-2 py-0.5 bg-slate-800 text-slate-300 rounded border border-slate-700"
                      >
                        {ev}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              {/* Card Actions */}
              <div className="flex items-center justify-between pt-3 border-t border-slate-800 text-xs">
                <button
                  onClick={() => handleTestChannel(ch.id)}
                  disabled={testingId === ch.id}
                  className="flex items-center gap-1 px-3 py-1.5 bg-indigo-950/70 hover:bg-indigo-900 border border-indigo-800/60 rounded text-indigo-300 hover:text-white transition font-medium disabled:opacity-50"
                  title="Send test verification alert"
                >
                  <Send className="w-3.5 h-3.5" />
                  <span>{testingId === ch.id ? 'Sending...' : 'Test Webhook'}</span>
                </button>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => openEditModal(ch)}
                    className="p-1.5 text-slate-400 hover:text-white hover:bg-slate-800 rounded transition"
                    title="Edit Channel"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleDeleteChannel(ch.id, ch.name)}
                    className="p-1.5 text-rose-400 hover:text-rose-300 hover:bg-slate-800 rounded transition"
                    title="Delete Channel"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Add / Edit Channel Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-lg p-6 shadow-xl space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-slate-800">
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <Bell className="w-5 h-5 text-indigo-400" />
                {editingChannel ? 'Edit Notification Channel' : 'Add Notification Channel'}
              </h2>
              <button onClick={() => setIsModalOpen(false)} className="text-slate-400 hover:text-white">
                ✕
              </button>
            </div>

            {modalError && (
              <div className="p-3 bg-rose-950/50 border border-rose-800/60 rounded-lg text-rose-300 text-xs">
                {modalError}
              </div>
            )}

            <form onSubmit={handleSaveChannel} className="space-y-4 text-xs">
              {/* Channel Name */}
              <div>
                <label className="block text-slate-300 font-medium mb-1">Channel Name</label>
                <input
                  type="text"
                  placeholder="e.g. SRE Lark Alerts"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  required
                />
              </div>

              {/* Provider */}
              <div>
                <label className="block text-slate-300 font-medium mb-1">Webhook Provider</label>
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value as any)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
                >
                  <option value="feishu">Feishu / Lark (Interactive Card)</option>
                  <option value="dingtalk">DingTalk / 钉钉 (Markdown + Sign)</option>
                  <option value="wecom">WeChat Work / 企业微信</option>
                  <option value="telegram">Telegram Bot</option>
                  <option value="bark">Bark (iOS Push)</option>
                  <option value="slack">Slack Incoming Webhook</option>
                  <option value="webhook">Generic Webhook (JSON POST)</option>
                </select>
              </div>

              {/* Dynamic Provider Fields */}
              {provider !== 'telegram' && provider !== 'bark' && (
                <div>
                  <label className="block text-slate-300 font-medium mb-1">
                    Webhook URL {editingChannel && '(Leave empty to keep existing)'}
                  </label>
                  <input
                    type="url"
                    placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                    value={webhookUrl}
                    onChange={(e) => setWebhookUrl(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                    required={!editingChannel}
                  />
                </div>
              )}

              {(provider === 'feishu' || provider === 'dingtalk') && (
                <div>
                  <label className="block text-slate-300 font-medium mb-1">
                    Secret (Optional HMAC-SHA256 Signature)
                  </label>
                  <input
                    type="password"
                    placeholder="SEC..."
                    value={secret}
                    onChange={(e) => setSecret(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  />
                </div>
              )}

              {provider === 'telegram' && (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-slate-300 font-medium mb-1">Bot Token</label>
                    <input
                      type="password"
                      placeholder="123456:ABC-DEF..."
                      value={botToken}
                      onChange={(e) => setBotToken(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                      required={!editingChannel}
                    />
                  </div>
                  <div>
                    <label className="block text-slate-300 font-medium mb-1">Chat ID</label>
                    <input
                      type="text"
                      placeholder="-10012345678"
                      value={chatId}
                      onChange={(e) => setChatId(e.target.value)}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                      required={!editingChannel}
                    />
                  </div>
                </div>
              )}

              {provider === 'bark' && (
                <div>
                  <label className="block text-slate-300 font-medium mb-1">Bark Device Key</label>
                  <input
                    type="text"
                    placeholder="YourBarkDeviceKey"
                    value={deviceKey}
                    onChange={(e) => setDeviceKey(e.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-white font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                    required={!editingChannel}
                  />
                </div>
              )}

              {/* Event Subscriptions */}
              <div>
                <label className="block text-slate-300 font-medium mb-2">Subscribe to Events</label>
                <div className="grid grid-cols-2 gap-2 bg-slate-950 p-3 rounded-lg border border-slate-800">
                  {ALL_EVENTS.map((ev) => (
                    <label key={ev.id} className="flex items-center gap-2 text-slate-300 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={selectedEvents.includes(ev.id)}
                        onChange={() => handleToggleEvent(ev.id)}
                        className="rounded bg-slate-900 border-slate-700 text-indigo-600 focus:ring-0"
                      />
                      <span>{ev.label}</span>
                    </label>
                  ))}
                </div>
              </div>

              {/* Enabled Switch */}
              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="enabledCheck"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                  className="rounded bg-slate-950 border-slate-800 text-indigo-600 focus:ring-0"
                />
                <label htmlFor="enabledCheck" className="text-slate-300 font-medium cursor-pointer">
                  Enable notification channel
                </label>
              </div>

              {/* Action Buttons */}
              <div className="flex justify-end gap-3 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition flex items-center gap-1.5 font-medium disabled:opacity-50"
                >
                  {submitting ? 'Saving...' : editingChannel ? 'Update Channel' : 'Create Channel'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
