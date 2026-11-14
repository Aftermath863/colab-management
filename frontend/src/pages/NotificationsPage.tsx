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
  X,
} from 'lucide-react';
import { NotificationChannel } from '../types';
import { api } from '../services/api';
import { LocalizedText, useI18n } from '../i18n';
import { MODAL_BACKDROP_CLASSES, ModalPortal } from '../components/ModalPortal';
import {
  triggerHaptic,
  notifyToast,
  updateCardSpotlight,
} from '../utils/macDesktop';

const ALL_EVENT_IDS = [
  'session.started',
  'session.stopped',
  'quota.warning',
  'quota.hard_stop',
  'job.started',
  'job.finished',
  'job.failed',
  'orphan.discovered',
] as const;

export const NotificationsPage: React.FC = () => {
  const { t, tm, locale, formatActionError, formatEventLabel, formatNotificationProvider } =
    useI18n();
  const isZh = locale === 'zh';

  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [loading, setLoading] = useState(true);
  const [testResult, setTestResult] = useState<{
    channelId: string;
    success: boolean;
    response: LocalizedText;
  } | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<NotificationChannel | null>(null);

  // Modal State
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingChannel, setEditingChannel] = useState<NotificationChannel | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [modalError, setModalError] = useState<LocalizedText | null>(null);

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
    void fetchChannels();
  }, []);

  useEffect(() => {
    if ((!isModalOpen && !deleteTarget) || typeof window === 'undefined') return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (deleteTarget) setDeleteTarget(null);
      else if (isModalOpen) setIsModalOpen(false);
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [isModalOpen, deleteTarget]);

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
    triggerHaptic('light');
    resetForm();
    setIsModalOpen(true);
  };

  const openEditModal = (ch: NotificationChannel) => {
    triggerHaptic('light');
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
      setModalError({ key: 'notifications.missingNameError' });
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
    triggerHaptic('light');

    try {
      if (editingChannel) {
        await api.updateNotificationChannel(editingChannel.id, {
          name: name.trim(),
          provider,
          config: Object.keys(config).length > 0 ? config : undefined,
          events: selectedEvents,
          enabled,
        });
        notifyToast(isZh ? '通知渠道已更新' : 'Channel Updated', name.trim(), 'success');
      } else {
        await api.createNotificationChannel({
          name: name.trim(),
          provider,
          config,
          events: selectedEvents,
          enabled,
        });
        notifyToast(isZh ? '通知渠道已创建' : 'Channel Created', name.trim(), 'success');
      }

      triggerHaptic('success');
      setIsModalOpen(false);
      resetForm();
      void fetchChannels();
    } catch (err: any) {
      triggerHaptic('medium');
      setModalError(err.message ? { raw: err.message } : { key: 'notifications.saveFailed' });
    } finally {
      setSubmitting(false);
    }
  };

  const confirmDeleteChannel = async () => {
    if (!deleteTarget) return;
    const ch = deleteTarget;
    setDeleteTarget(null);
    triggerHaptic('medium');
    try {
      await api.deleteNotificationChannel(ch.id);
      triggerHaptic('success');
      notifyToast(isZh ? '通知渠道已删除' : 'Channel Deleted', ch.name, 'success');
      void fetchChannels();
    } catch (err: any) {
      triggerHaptic('medium');
      notifyToast(formatActionError('notifications.deleteFailed', err?.message), undefined, 'error');
    }
  };

  const handleTestChannel = async (id: string) => {
    setTestingId(id);
    setTestResult(null);
    triggerHaptic('light');
    try {
      const res = await api.testNotificationChannel(id);
      setTestResult({
        channelId: id,
        success: res.success,
        response: { raw: res.response },
      });
      triggerHaptic(res.success ? 'success' : 'medium');
      notifyToast(
        res.success ? t('notifications.probeSuccess') : t('notifications.probeFailed'),
        res.response,
        res.success ? 'success' : 'error'
      );
    } catch (err: any) {
      const errText: LocalizedText = err.message
        ? { raw: err.message }
        : { key: 'notifications.testFailedFallback' };
      setTestResult({
        channelId: id,
        success: false,
        response: errText,
      });
      triggerHaptic('medium');
      notifyToast(t('notifications.probeFailed'), tm(errText), 'error');
    } finally {
      setTestingId(null);
    }
  };

  return (
    <div className="p-4 sm:p-8 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2.5">
            <Bell className="w-6 h-6 text-amber-400" />
            <span>{t('notifications.title')}</span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">{t('notifications.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2.5">
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light');
              void fetchChannels();
            }}
            className="mac-btn-secondary p-2.5 rounded-xl"
            title={t('notifications.refreshTooltip')}
            aria-label={t('notifications.refreshTooltip')}
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-sky-400' : ''}`} />
          </button>
          <button
            type="button"
            onClick={openCreateModal}
            className="mac-btn-primary flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium"
          >
            <Plus className="w-4 h-4" />
            <span>{t('notifications.addChannel')}</span>
          </button>
        </div>
      </div>

      {/* Animated Webhook Test Result Banner */}
      {testResult && (
        <div
          className={`mac-card p-4 rounded-2xl border flex items-start justify-between gap-3 text-sm transition-all ${
            testResult.success
              ? 'bg-emerald-500/10 border-emerald-400/35 text-emerald-200 shadow-[0_0_24px_-8px_rgba(16,185,129,0.45)]'
              : 'bg-rose-500/10 border-rose-400/35 text-rose-200 shadow-[0_0_24px_-8px_rgba(244,63,94,0.45)]'
          }`}
        >
          <div className="flex items-start gap-2.5">
            {testResult.success ? (
              <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
            ) : (
              <AlertCircle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
            )}
            <div>
              <p className="font-semibold">
                {testResult.success
                  ? t('notifications.probeSuccess')
                  : t('notifications.probeFailed')}
              </p>
              <p className="text-xs opacity-90 font-mono mt-0.5 break-all">
                {tm(testResult.response)}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setTestResult(null)}
            title={t('common.close')}
            className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Channels Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {channels.length === 0 ? (
          <div className="col-span-full mac-card p-12 text-center space-y-3">
            <div className="w-12 h-12 mx-auto rounded-2xl bg-white/[0.04] border border-white/[0.08] flex items-center justify-center text-amber-400/80">
              <Bell className="w-6 h-6" />
            </div>
            <p className="text-sm text-slate-400 max-w-lg mx-auto">
              {t('notifications.noChannels')}
            </p>
          </div>
        ) : (
          channels.map((ch) => {
            const isTesting = testingId === ch.id;
            const testedThisCard = testResult?.channelId === ch.id;
            return (
              <div
                key={ch.id}
                onMouseMove={updateCardSpotlight}
                className={`mac-card mac-card-hover p-5 flex flex-col justify-between transition ${
                  ch.enabled ? '' : 'opacity-65'
                }`}
              >
                <div>
                  {/* Card Header */}
                  <div className="flex items-center justify-between gap-2 mb-3.5">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <span className="p-2 bg-indigo-500/15 border border-indigo-400/30 rounded-xl text-indigo-300 shrink-0">
                        <MessageSquare className="w-4 h-4" />
                      </span>
                      <div className="min-w-0">
                        <h3 className="font-semibold text-white text-sm truncate">{ch.name}</h3>
                        <span className="text-[11px] font-mono uppercase font-bold text-slate-400 tracking-wider">
                          {formatNotificationProvider(ch.provider)}
                        </span>
                      </div>
                    </div>
                    <span
                      className={`px-2.5 py-0.5 rounded-full text-[10px] font-semibold uppercase border shrink-0 ${
                        ch.enabled
                          ? 'bg-emerald-500/15 text-emerald-300 border-emerald-400/30'
                          : 'bg-white/[0.06] text-slate-400 border-white/10'
                      }`}
                    >
                      {ch.enabled ? t('notifications.enabled') : t('notifications.disabled')}
                    </span>
                  </div>

                  {/* Masked Config Summary */}
                  <div className="bg-white/[0.03] p-3 rounded-xl border border-white/[0.07] mb-3.5 text-xs text-slate-400 font-mono space-y-1">
                    <div className="flex items-center gap-1.5 text-slate-200 font-sans font-medium text-[11px]">
                      <Shield className="w-3.5 h-3.5 text-indigo-400" />
                      <span>{t('notifications.vaultProtected')}</span>
                    </div>
                    {ch.config_masked &&
                      Object.entries(ch.config_masked).map(([k, v]) => (
                        <div key={k} className="truncate">
                          <span className="text-slate-500">{k}:</span>{' '}
                          <span className="text-slate-300">{String(v)}</span>
                        </div>
                      ))}
                  </div>

                  {/* Subscribed Events Tags */}
                  <div className="mb-4">
                    <span className="text-[11px] text-slate-400 font-medium block mb-1.5">
                      {t('notifications.subscribedEventsCount', { count: ch.events.length })}
                    </span>
                    <div className="flex flex-wrap gap-1.5">
                      {ch.events.map((ev) => (
                        <span
                          key={ev}
                          title={ev}
                          className="text-[10px] px-2 py-0.5 bg-white/[0.05] text-slate-200 rounded-md border border-white/[0.08]"
                        >
                          {formatEventLabel(ev)}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Card Actions with Animated Test Feedback */}
                <div className="flex items-center justify-between pt-3 border-t border-white/[0.08] text-xs">
                  <button
                    type="button"
                    onClick={() => void handleTestChannel(ch.id)}
                    disabled={isTesting}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl border transition font-medium disabled:opacity-60 ${
                      testedThisCard && testResult.success
                        ? 'bg-emerald-500/20 border-emerald-400/40 text-emerald-200'
                        : testedThisCard && !testResult.success
                        ? 'bg-rose-500/20 border-rose-400/40 text-rose-200'
                        : 'bg-indigo-500/15 hover:bg-indigo-500/25 border-indigo-400/30 text-indigo-200'
                    }`}
                    title={t('notifications.testTooltip')}
                  >
                    {isTesting ? (
                      <RefreshCw className="w-3.5 h-3.5 animate-spin text-sky-300" />
                    ) : testedThisCard && testResult.success ? (
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-300" />
                    ) : testedThisCard && !testResult.success ? (
                      <AlertCircle className="w-3.5 h-3.5 text-rose-300" />
                    ) : (
                      <Send className="w-3.5 h-3.5" />
                    )}
                    <span>
                      {isTesting ? t('notifications.sending') : t('notifications.testWebhook')}
                    </span>
                  </button>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => openEditModal(ch)}
                      className="p-1.5 text-slate-400 hover:text-white hover:bg-white/[0.08] rounded-lg transition"
                      title={t('notifications.editTooltip')}
                      aria-label={t('notifications.editTooltip')}
                    >
                      <Edit2 className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        triggerHaptic('light');
                        setDeleteTarget(ch);
                      }}
                      className="p-1.5 text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 rounded-lg transition"
                      title={t('notifications.deleteTooltip')}
                      aria-label={t('notifications.deleteTooltip')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Delete Confirmation Modal */}
      {deleteTarget && (
        <ModalPortal>
          <div
            className={MODAL_BACKDROP_CLASSES}
            onClick={() => setDeleteTarget(null)}
          >
            <div
              className="mac-modal-panel mac-card w-full max-w-md p-6 space-y-5 shadow-2xl overflow-y-auto"
              onClick={(e) => e.stopPropagation()}
            >
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-rose-500/15 border border-rose-400/30 text-rose-300">
                  <Trash2 className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-semibold text-white">
                    {t('notifications.deleteTooltip')}
                  </h3>
                  <p className="text-xs text-slate-400 mt-0.5">{deleteTarget.name}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                aria-label={t('common.close')}
                className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <p className="text-sm text-slate-300 leading-relaxed">
              {t('notifications.confirmDelete', { name: deleteTarget.name })}
            </p>
            <div className="flex justify-end gap-2.5 pt-2 border-t border-white/[0.08]">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="mac-btn-secondary px-4 py-2 rounded-xl text-xs font-medium"
              >
                {t('common.cancel')}
              </button>
              <button
                type="button"
                onClick={() => void confirmDeleteChannel()}
                className="px-4 py-2 rounded-xl text-xs font-semibold bg-rose-600 hover:bg-rose-500 text-white transition"
              >
                {t('common.delete')}
              </button>
            </div>
          </div>
        </div>
        </ModalPortal>
      )}

      {/* Add / Edit Channel Modal */}
      {isModalOpen && (
        <ModalPortal>
          <div
            className={MODAL_BACKDROP_CLASSES}
            onClick={() => setIsModalOpen(false)}
          >
            <div
              className="mac-modal-panel mac-card w-full max-w-lg p-6 shadow-2xl space-y-4 overflow-y-auto"
              onClick={(e) => e.stopPropagation()}
            >
            <div className="flex items-center justify-between pb-3 border-b border-white/[0.08]">
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <Bell className="w-5 h-5 text-indigo-400" />
                <span>
                  {editingChannel
                    ? t('notifications.modalEditTitle')
                    : t('notifications.modalAddTitle')}
                </span>
              </h2>
              <button
                type="button"
                onClick={() => setIsModalOpen(false)}
                title={t('common.close')}
                className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.08] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {modalError && (
              <div className="p-3 bg-rose-500/10 border border-rose-400/30 rounded-xl text-rose-200 text-xs">
                {tm(modalError)}
              </div>
            )}

            <form onSubmit={handleSaveChannel} className="space-y-4 text-xs">
              <div>
                <label className="block text-slate-300 font-medium mb-1">
                  {t('notifications.channelNameLabel')}
                </label>
                <input
                  type="text"
                  placeholder={t('notifications.channelNamePlaceholder')}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="mac-input w-full rounded-xl px-3 py-2 text-white placeholder-slate-500"
                  required
                />
              </div>

              <div>
                <label className="block text-slate-300 font-medium mb-1">
                  {t('notifications.providerLabel')}
                </label>
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value as any)}
                  className="mac-input w-full rounded-xl px-3 py-2 text-white"
                >
                  <option value="feishu">{t('notifications.providerFeishu')}</option>
                  <option value="dingtalk">{t('notifications.providerDingtalk')}</option>
                  <option value="wecom">{t('notifications.providerWecom')}</option>
                  <option value="telegram">{t('notifications.providerTelegram')}</option>
                  <option value="bark">{t('notifications.providerBark')}</option>
                  <option value="slack">{t('notifications.providerSlack')}</option>
                  <option value="webhook">{t('notifications.providerWebhook')}</option>
                </select>
              </div>

              {provider !== 'telegram' && provider !== 'bark' && (
                <div>
                  <label className="block text-slate-300 font-medium mb-1">
                    {t('notifications.webhookUrlLabel')}{' '}
                    {editingChannel && t('notifications.keepExistingSuffix')}
                  </label>
                  <input
                    type="url"
                    placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                    value={webhookUrl}
                    onChange={(e) => setWebhookUrl(e.target.value)}
                    className="mac-input w-full rounded-xl px-3 py-2 text-white font-mono placeholder-slate-500"
                    required={!editingChannel}
                  />
                </div>
              )}

              {(provider === 'feishu' || provider === 'dingtalk') && (
                <div>
                  <label className="block text-slate-300 font-medium mb-1">
                    {t('notifications.secretLabel')}
                  </label>
                  <input
                    type="password"
                    placeholder="SEC..."
                    value={secret}
                    onChange={(e) => setSecret(e.target.value)}
                    className="mac-input w-full rounded-xl px-3 py-2 text-white font-mono placeholder-slate-500"
                  />
                </div>
              )}

              {provider === 'telegram' && (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-slate-300 font-medium mb-1">
                      {t('notifications.botTokenLabel')}
                    </label>
                    <input
                      type="password"
                      placeholder="123456:ABC-DEF..."
                      value={botToken}
                      onChange={(e) => setBotToken(e.target.value)}
                      className="mac-input w-full rounded-xl px-3 py-2 text-white font-mono placeholder-slate-500"
                      required={!editingChannel}
                    />
                  </div>
                  <div>
                    <label className="block text-slate-300 font-medium mb-1">
                      {t('notifications.chatIdLabel')}
                    </label>
                    <input
                      type="text"
                      placeholder="-10012345678"
                      value={chatId}
                      onChange={(e) => setChatId(e.target.value)}
                      className="mac-input w-full rounded-xl px-3 py-2 text-white font-mono placeholder-slate-500"
                      required={!editingChannel}
                    />
                  </div>
                </div>
              )}

              {provider === 'bark' && (
                <div>
                  <label className="block text-slate-300 font-medium mb-1">
                    {t('notifications.barkDeviceKeyLabel')}
                  </label>
                  <input
                    type="text"
                    placeholder="YourBarkDeviceKey"
                    value={deviceKey}
                    onChange={(e) => setDeviceKey(e.target.value)}
                    className="mac-input w-full rounded-xl px-3 py-2 text-white font-mono placeholder-slate-500"
                    required={!editingChannel}
                  />
                </div>
              )}

              <div>
                <label className="block text-slate-300 font-medium mb-2">
                  {t('notifications.subscribeEventsLabel')}
                </label>
                <div className="grid grid-cols-2 gap-2 bg-white/[0.03] p-3 rounded-xl border border-white/[0.08]">
                  {ALL_EVENT_IDS.map((eventId) => (
                    <label
                      key={eventId}
                      className="flex items-center gap-2 text-slate-300 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedEvents.includes(eventId)}
                        onChange={() => handleToggleEvent(eventId)}
                        className="rounded bg-slate-950 border-white/20 text-indigo-500 focus:ring-0"
                      />
                      <span>{formatEventLabel(eventId)}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="enabledCheck"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                  className="rounded bg-slate-950 border-white/20 text-indigo-500 focus:ring-0"
                />
                <label
                  htmlFor="enabledCheck"
                  className="text-slate-300 font-medium cursor-pointer"
                >
                  {t('notifications.enableChannelLabel')}
                </label>
              </div>

              <div className="flex justify-end gap-2.5 pt-3 border-t border-white/[0.08]">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="mac-btn-secondary px-4 py-2 rounded-xl font-medium"
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="mac-btn-primary px-4 py-2 rounded-xl flex items-center gap-1.5 font-medium disabled:opacity-50"
                >
                  {submitting
                    ? t('common.saving')
                    : editingChannel
                    ? t('notifications.updatingChannel')
                    : t('notifications.creatingChannel')}
                </button>
              </div>
            </form>
          </div>
        </div>
        </ModalPortal>
      )}
    </div>
  );
};
