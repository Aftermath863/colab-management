import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import {
  en,
  zh,
  I18nProvider,
  useI18n,
  normalizeLocale,
  detectInitialLocale,
  resolvePath,
  interpolate,
  translate,
  translateErrorMessage,
  safeReadStorage,
  safeWriteStorage,
  safeRemoveStorage,
  STORAGE_KEY,
  TranslationKey,
} from '../src/i18n';
import { App } from '../src/App';
import { LanguageSwitcher } from '../src/components/LanguageSwitcher';
import { Navbar } from '../src/components/Navbar';
import { OAuthModal } from '../src/components/OAuthModal';
import { NewSessionModal } from '../src/components/NewSessionModal';
import { StorageWizardModal } from '../src/components/StorageWizardModal';
import { SSHHelperModal } from '../src/components/SSHHelperModal';
import { LoginPage } from '../src/pages/LoginPage';
import { OverviewPage } from '../src/pages/OverviewPage';
import { AccountsPage } from '../src/pages/AccountsPage';
import { SessionsPage } from '../src/pages/SessionsPage';
import { SessionWorkbenchPage } from '../src/pages/SessionWorkbenchPage';
import { TemplatesPage } from '../src/pages/TemplatesPage';
import { JobsPage } from '../src/pages/JobsPage';
import { NotificationsPage } from '../src/pages/NotificationsPage';
import { api } from '../src/services/api';
import { Account, Session } from '../src/types';
import { formatSyncTimestamp, getLatestSyncTimestamp } from '../src/utils/time';

function collectLeafEntries(obj: Record<string, unknown>, prefix = ''): Map<string, string> {
  const result = new Map<string, string>();
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'string') {
      result.set(path, v);
    } else if (v && typeof v === 'object') {
      for (const [subK, subV] of collectLeafEntries(v as Record<string, unknown>, path)) {
        result.set(subK, subV);
      }
    }
  }
  return result;
}

function extractTokens(str: string): string[] {
  const matches = [...str.matchAll(/\{([a-zA-Z0-9_]+)\}/g)].map((m) => m[1]);
  return [...new Set(matches)].sort();
}

test('Dictionary integrity: 100% key parity, non-empty values, and {placeholder} parity between en and zh', () => {
  const enLeaves = collectLeafEntries(en as unknown as Record<string, unknown>);
  const zhLeaves = collectLeafEntries(zh as unknown as Record<string, unknown>);

  assert.ok(enLeaves.size > 200, `Expected >200 translation keys, got ${enLeaves.size}`);
  assert.deepEqual([...enLeaves.keys()].sort(), [...zhLeaves.keys()].sort());

  for (const [key, enVal] of enLeaves.entries()) {
    const zhVal = zhLeaves.get(key);
    assert.ok(zhVal !== undefined, `Missing zh translation for ${key}`);
    assert.ok(enVal.trim().length > 0, `Empty en translation for ${key}`);
    assert.ok(zhVal.trim().length > 0, `Empty zh translation for ${key}`);

    const enTokens = extractTokens(enVal);
    const zhTokens = extractTokens(zhVal);
    assert.deepEqual(
      zhTokens,
      enTokens,
      `Placeholder mismatch for key "${key}": en=${JSON.stringify(enTokens)}, zh=${JSON.stringify(zhTokens)}`
    );
  }
});

test('normalizeLocale handles empty, case-insensitive, regional tags, and invalid inputs', () => {
  assert.equal(normalizeLocale(undefined), null);
  assert.equal(normalizeLocale(null), null);
  assert.equal(normalizeLocale(''), null);
  assert.equal(normalizeLocale('   '), null);

  assert.equal(normalizeLocale('zh'), 'zh');
  assert.equal(normalizeLocale('ZH'), 'zh');
  assert.equal(normalizeLocale('zh-CN'), 'zh');
  assert.equal(normalizeLocale('zh_TW'), 'zh');
  assert.equal(normalizeLocale('zh-Hans-CN'), 'zh');

  assert.equal(normalizeLocale('en'), 'en');
  assert.equal(normalizeLocale('EN'), 'en');
  assert.equal(normalizeLocale('en-US'), 'en');
  assert.equal(normalizeLocale('en_GB'), 'en');

  assert.equal(normalizeLocale('english'), null);
  assert.equal(normalizeLocale('zhx'), null);
  assert.equal(normalizeLocale('fr-FR'), null);
  assert.equal(normalizeLocale('__proto__'), null);
  assert.equal(normalizeLocale('constructor'), null);
});

test('detectInitialLocale prioritizes valid storage over navigator languages and falls back cleanly', () => {
  assert.equal(detectInitialLocale('zh', ['en-US']), 'zh');
  assert.equal(detectInitialLocale('en', ['zh-CN']), 'en');
  assert.equal(detectInitialLocale('zh-TW', ['en-US']), 'zh');

  // Corrupted storage falls back to browser languages in order
  assert.equal(detectInitialLocale('corrupted', ['fr-FR', 'zh-HK', 'en-US']), 'zh');
  assert.equal(detectInitialLocale('', ['de-DE', 'en-GB', 'zh-CN']), 'en');
  assert.equal(detectInitialLocale(null, 'zh-CN'), 'zh');
  assert.equal(detectInitialLocale(null, ['ja-JP', 'ko-KR']), 'en');
  assert.equal(detectInitialLocale(undefined, undefined), 'en');
});

test('interpolate & resolvePath resist prototype pollution, replacement pattern injection, and falsy 0', () => {
  assert.equal(resolvePath(en as unknown as Record<string, unknown>, '__proto__.polluted'), undefined);
  assert.equal(resolvePath(en as unknown as Record<string, unknown>, 'constructor.name'), undefined);
  assert.equal(resolvePath(en as unknown as Record<string, unknown>, 'toString'), undefined);
  assert.equal(resolvePath(en as unknown as Record<string, unknown>, ''), undefined);

  // Zero must render as "0", not empty string
  assert.equal(interpolate('Count: {count}', { count: 0 }), 'Count: 0');
  assert.equal(interpolate('Empty: {val}', { val: '' }), 'Empty: ');
  assert.equal(interpolate('Nullish: {a}{b}', { a: null, b: undefined }), 'Nullish: ');

  // Must not expand Object.prototype properties
  assert.equal(
    interpolate('{__proto__} {toString} {constructor} {ok}', { ok: 'safe' }),
    '{__proto__} {toString} {constructor} safe'
  );

  // Must not suffer from String.replace $& / $$ injection or recursive expansion
  assert.equal(
    interpolate('Deleting {name} ({other})', { name: '$& $$ $1 {other}', other: 'kept' }),
    'Deleting $& $$ $1 {other} (kept)'
  );

  assert.equal(
    translate('zh', 'overview.acrossAccounts', { count: 0 }),
    '汇总自 0 个已绑定账号'
  );
  assert.equal(
    translate('en', 'overview.acrossAccounts', { count: 3 }),
    'Across 3 linked accounts'
  );
  // Fallback for unknown dynamic key cast
  assert.equal(translate('zh', 'nonexistent.key' as TranslationKey), 'nonexistent.key');
});

test('safeReadStorage, safeWriteStorage, and safeRemoveStorage survive throwing or malformed localStorage', () => {
  const origWindow = (globalThis as any).window;
  try {
    (globalThis as any).window = {
      localStorage: {
        getItem() {
          throw new Error('SecurityError: Access is denied');
        },
        setItem() {
          throw new Error('QuotaExceededError');
        },
        removeItem() {
          throw new Error('SecurityError');
        },
      },
    };
    assert.equal(safeReadStorage(STORAGE_KEY), null);
    assert.doesNotThrow(() => safeWriteStorage(STORAGE_KEY, 'zh'));
    assert.doesNotThrow(() => safeRemoveStorage(STORAGE_KEY));

    // Malformed localStorage object without methods
    (globalThis as any).window = { localStorage: {} };
    assert.equal(safeReadStorage(STORAGE_KEY), null);
    assert.doesNotThrow(() => safeWriteStorage(STORAGE_KEY, 'zh'));
    assert.doesNotThrow(() => safeRemoveStorage(STORAGE_KEY));
  } finally {
    (globalThis as any).window = origWindow;
  }
});

test('translateErrorMessage localizes backend and client errors in zh and preserves en', () => {
  assert.equal(translateErrorMessage('', 'zh'), '');
  assert.equal(translateErrorMessage(null, 'zh'), '');
  assert.equal(translateErrorMessage('Incorrect admin password', 'en'), 'Incorrect admin password');
  assert.equal(translateErrorMessage('Incorrect admin password', 'zh'), '管理员密码错误');
  assert.equal(
    translateErrorMessage('Session sess_abc123 not found', 'zh'),
    '未找到实例 sess_abc123'
  );
  assert.equal(
    translateErrorMessage('Colab quota exceeded or unsupported accelerator: A100', 'zh'),
    'Colab 配额不足或不支持该加速器：A100'
  );
  assert.equal(
    translateErrorMessage("Channel with name 'ops-feishu' already exists", 'zh'),
    '名为“ops-feishu”的通知渠道已存在'
  );
  assert.equal(
    translateErrorMessage("Secret with key_name 'HF_TOKEN' already exists", 'zh'),
    '密钥名称“HF_TOKEN”已存在'
  );
  assert.equal(
    translateErrorMessage('No eligible active accounts with available CCU quota in pool', 'zh'),
    '算力池中没有可用的活跃 Google 账号或 CCU 配额不足'
  );
  assert.equal(
    translateErrorMessage(
      'Session provisioning failed: No eligible active accounts with available CCU quota in pool',
      'zh'
    ),
    '实例分配失败：算力池中没有可用的活跃 Google 账号或 CCU 配额不足'
  );
  assert.equal(
    translateErrorMessage('Network Error: All connection attempts failed', 'zh'),
    '网络错误：无法连接到目标服务器（所有连接尝试均失败）'
  );
  assert.equal(
    translateErrorMessage("Cannot cancel job in state 'COMPLETED'", 'zh'),
    '当前状态（已完成）下的任务无法取消'
  );
  // Unknown custom error message is preserved verbatim
  assert.equal(translateErrorMessage('Custom upstream error 502', 'zh'), 'Custom upstream error 502');
});

test('Domain enum formatters, formatActionError, and reactive tm() localize known values without double-prefixing', () => {
  const Probe: React.FC = () => {
    const i18n = useI18n();
    return (
      <div>
        <span id="acc">{i18n.formatAccountStatus('ACTIVE')}|{i18n.formatAccountStatus('CUSTOM_STATE')}</span>
        <span id="sess">{i18n.formatSessionStatus('BOOTSTRAPPING')}|{i18n.formatSessionStatus('UNKNOWN')}</span>
        <span id="shape">{i18n.formatShape('hm')}|{i18n.formatShape('custom')}</span>
        <span id="life">{i18n.formatLifecycle('ephemeral')}|{i18n.formatLifecycle('other')}</span>
        <span id="job">{i18n.formatJobStatus('OOM_KILLED')}|{i18n.formatJobStatus('NEW')}</span>
        <span id="filter">{i18n.formatJobFilter('')}|{i18n.formatJobFilter('QUEUED')}</span>
        <span id="jobtype">{i18n.formatJobType('python')}|{i18n.formatJobType('custom_t')}</span>
        <span id="provider">{i18n.formatNotificationProvider('feishu')}|{i18n.formatNotificationProvider('custom_p')}</span>
        <span id="storage">{i18n.formatStorageType('gdrive')}|{i18n.formatStorageType('custom_s')}</span>
        <span id="kernel">{i18n.formatKernelState(null)}|{i18n.formatKernelState('Busy')}|{i18n.formatKernelState('custom_k')}</span>
        <span id="event">{i18n.formatEventLabel('quota.warning')}|{i18n.formatEventLabel('custom.event')}</span>
        <span id="act-dup">{i18n.formatActionError('sessions.stopFailed', 'Failed to stop session')}</span>
        <span id="act-det">{i18n.formatActionError('sessions.stopFailed', 'Session s1 not found')}</span>
        <span id="act-up">{i18n.formatActionError('files.uploadFailed', 'Failed to save file')}</span>
        <span id="tm-key">{i18n.tm({ key: 'templates.templateCreated', params: { name: 'demo' } })}</span>
        <span id="tm-raw">{i18n.tm({ raw: 'Incorrect admin password' })}</span>
        <span id="tm-null">{i18n.tm(null)}</span>
      </div>
    );
  };

  const zhOut = renderToStaticMarkup(
    <I18nProvider initialLocale="zh">
      <Probe />
    </I18nProvider>
  );
  assert.ok(zhOut.includes('正常|CUSTOM_STATE'));
  assert.ok(zhOut.includes('初始化中|UNKNOWN'));
  assert.ok(zhOut.includes('高内存|custom'));
  assert.ok(zhOut.includes('跑完即焚|other'));
  assert.ok(zhOut.includes('内存溢出 (137)|NEW'));
  assert.ok(zhOut.includes('全部任务|排队中'));
  assert.ok(zhOut.includes('Python 脚本|custom_t'));
  assert.ok(zhOut.includes('飞书 / Lark|custom_p'));
  assert.ok(zhOut.includes('Google 云端硬盘|custom_s'));
  assert.ok(zhOut.includes('空闲|繁忙|custom_k'));
  assert.ok(zhOut.includes('算力配额预警 (≤10 CCU)|custom.event'));
  assert.ok(zhOut.includes('id="act-dup">停止实例失败</span>'));
  assert.ok(!zhOut.includes('停止实例失败：停止实例失败'));
  assert.ok(zhOut.includes('id="act-det">停止实例失败：未找到实例 s1</span>'));
  assert.ok(zhOut.includes('id="act-up">上传失败</span>'));
  assert.ok(zhOut.includes('环境模板“demo”创建成功！'));
  assert.ok(zhOut.includes('管理员密码错误'));

  const enOut = renderToStaticMarkup(
    <I18nProvider initialLocale="en">
      <Probe />
    </I18nProvider>
  );
  assert.ok(enOut.includes('ACTIVE|CUSTOM_STATE'));
  assert.ok(enOut.includes('All Jobs|QUEUED'));
  assert.ok(enOut.includes('Python|custom_t'));
  assert.ok(enOut.includes('Feishu / Lark|custom_p'));
  assert.ok(enOut.includes('Google Drive|custom_s'));
  assert.ok(enOut.includes('Idle|Busy|custom_k'));
  assert.ok(enOut.includes('id="act-dup">Failed to stop session</span>'));
  assert.ok(!enOut.includes('Failed to stop session: Failed to stop session'));
  assert.ok(enOut.includes('id="act-det">Failed to stop session: Session s1 not found</span>'));
  assert.ok(enOut.includes('id="act-up">Upload failed</span>'));
  assert.ok(enOut.includes('Incorrect admin password'));

  // Top-level <App /> renders cleanly in both zh and en
  const appZh = renderToStaticMarkup(<App initialLocale="zh" />);
  assert.ok(appZh.includes('Colab 资源管理平台'));
  const appEn = renderToStaticMarkup(<App initialLocale="en" />);
  assert.ok(appEn.includes('Colab Management'));
});

test('API client sends Accept-Language header matching stored locale', async () => {
  const origWindow = (globalThis as any).window;
  const origFetch = globalThis.fetch;

  const store = new Map<string, string>();
  const mockStorage = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => store.set(k, String(v)),
    removeItem: (k: string) => store.delete(k),
  };

  let capturedHeaders: Record<string, string> = {};

  try {
    (globalThis as any).window = {
      localStorage: mockStorage,
      dispatchEvent: () => true,
    };
    globalThis.fetch = (async (_url: any, init?: RequestInit) => {
      capturedHeaders = (init?.headers as Record<string, string>) || {};
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof fetch;

    store.set(STORAGE_KEY, 'zh');
    await api.getAccounts();
    assert.ok(capturedHeaders['Accept-Language']?.startsWith('zh-CN'));

    store.set(STORAGE_KEY, 'en');
    await api.syncAllAccounts();
    assert.ok(capturedHeaders['Accept-Language']?.startsWith('en-US'));
  } finally {
    (globalThis as any).window = origWindow;
    globalThis.fetch = origFetch;
  }
});

test('All pages and modals render localized UI and LanguageSwitcher in both zh and en', () => {
  const sampleAccounts: Account[] = [
    {
      id: 'acc_1',
      email: 'researcher@example.com',
      display_name: 'Researcher',
      ccu_balance: 1.5,
      burn_rate_hourly: 2.0,
      active_assignments: 1,
      warning_ccu: 10,
      hard_stop_ccu: 2,
      priority: 10,
      status: 'ACTIVE',
      last_synced_at: '2026-11-13 12:00:00',
    },
    {
      id: 'acc_2',
      email: 'backup@example.com',
      ccu_balance: 45.0,
      burn_rate_hourly: 0,
      active_assignments: 0,
      warning_ccu: 10,
      hard_stop_ccu: 2,
      priority: 5,
      status: 'AUTH_EXPIRED',
    },
  ];

  const sampleSessions: Session[] = [
    {
      id: 'sess_1',
      name: 'llm-train-01',
      account_id: 'acc_1',
      account_email: 'researcher@example.com',
      notebook_hash: 'nb1',
      endpoint: 'ep1',
      proxy_url: 'https://proxy.example.com',
      accelerator: 'A100',
      shape: 'hm',
      lifecycle_policy: 'keep_alive',
      idle_timeout_sec: 3600,
      ssh_local_port: 2220,
      workspace_subpath: 'llm',
      remote_workdir: '/content',
      status: 'READY',
      created_at: '2026-11-13 10:00:00',
      colab_web_url: 'https://colab.research.google.com/drive/nb1',
    },
    {
      id: 'sess_orphan',
      name: 'unmanaged-vm',
      account_id: 'acc_1',
      notebook_hash: 'nb2',
      endpoint: 'ep2',
      proxy_url: '',
      accelerator: 'T4',
      shape: 'standard',
      lifecycle_policy: 'interactive',
      idle_timeout_sec: 1800,
      workspace_subpath: '',
      remote_workdir: '/content',
      status: 'ORPHAN',
      created_at: '2026-11-13 09:00:00',
    },
  ];

  const noop = () => {};
  const asyncNoop = async () => {};

  // 1. LoginPage has LanguageSwitcher + localized text
  const loginZh = renderToStaticMarkup(
    <I18nProvider initialLocale="zh">
      <LoginPage onLoginSuccess={noop} />
    </I18nProvider>
  );
  assert.ok(loginZh.includes('data-testid="language-switcher"'));
  assert.ok(loginZh.includes('Colab 资源管理平台'));
  assert.ok(loginZh.includes('管理员密码'));
  assert.ok(loginZh.includes('登录控制台'));
  assert.ok(!loginZh.includes('Sign In to Console'));

  const loginEn = renderToStaticMarkup(
    <I18nProvider initialLocale="en">
      <LoginPage onLoginSuccess={noop} />
    </I18nProvider>
  );
  assert.ok(loginEn.includes('data-testid="language-switcher"'));
  assert.ok(loginEn.includes('Sign In to Console'));

  // 2. Navbar has LanguageSwitcher + localized navigation
  const navZh = renderToStaticMarkup(
    <I18nProvider initialLocale="zh">
      <Navbar activePage="overview" onNavigate={noop} onLogout={noop} />
    </I18nProvider>
  );
  assert.ok(navZh.includes('data-testid="language-switcher"'));
  for (const label of ['Colab 控制台', '端口 1113', '总览', '算力实例', '异步任务', '账号池', '模板与密钥', '告警通知', '退出登录']) {
    assert.ok(navZh.includes(label), `Missing "${label}" in Chinese Navbar`);
  }
  assert.ok(!navZh.includes('Sign Out'));

  // 3. OverviewPage (including orphan alert & status/shape badges)
  const overviewZh = renderToStaticMarkup(
    <I18nProvider initialLocale="zh">
      <OverviewPage
        accounts={sampleAccounts}
        sessions={sampleSessions}
        onOpenSession={noop}
        onNewSession={noop}
        onLinkAccount={noop}
      />
    </I18nProvider>
  );
  for (const label of ['算力池总览', '刷新配额', '每 60 秒自动同步', '上次同步：', '发现 1 个孤儿实例', '就绪', '高内存', '孤儿实例', '工作台']) {
    assert.ok(overviewZh.includes(label), `Missing "${label}" in Chinese OverviewPage`);
  }
  assert.ok(!overviewZh.includes('Fleet Overview'));

  // 4. AccountsPage (populated + empty)
  const accountsZh = renderToStaticMarkup(
    <I18nProvider initialLocale="zh">
      <AccountsPage accounts={sampleAccounts} onRefresh={asyncNoop} onLinkAccount={noop} />
    </I18nProvider>
  );
  for (const label of ['Google 账号池', '全部同步', '每 60 秒自动同步', '正常', '授权过期', '已触及熔断停机阈值', '尚未同步']) {
    assert.ok(accountsZh.includes(label), `Missing "${label}" in Chinese AccountsPage`);
  }
  const formattedTs = formatSyncTimestamp('2026-11-13 12:00:00');
  assert.match(formattedTs, /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
  assert.ok(accountsZh.includes(formattedTs));
  assert.equal(getLatestSyncTimestamp([{ last_synced_at: '2026-11-13 12:00:00' }, {}]), formattedTs);
  assert.equal(getLatestSyncTimestamp([{}]), null);

  const accountsEmptyZh = renderToStaticMarkup(
    <I18nProvider initialLocale="zh">
      <AccountsPage accounts={[]} onRefresh={asyncNoop} onLinkAccount={noop} />
    </I18nProvider>
  );
  assert.ok(accountsEmptyZh.includes('暂未绑定 Google 账号'));

  // 5. SessionsPage
  const sessionsZh = renderToStaticMarkup(
    <I18nProvider initialLocale="zh">
      <SessionsPage
        sessions={sampleSessions}
        onOpenSession={noop}
        onNewSession={noop}
        onStopSession={asyncNoop}
        onRefresh={asyncNoop}
      />
    </I18nProvider>
  );
  for (const label of ['计算实例列表', '全部', '运行中', '已停止', '创建时间']) {
    assert.ok(sessionsZh.includes(label), `Missing "${label}" in Chinese SessionsPage`);
  }

  // 6. SessionWorkbenchPage has LanguageSwitcher in header + localized tabs
  const workbenchZh = renderToStaticMarkup(
    <I18nProvider initialLocale="zh">
      <SessionWorkbenchPage session={sampleSessions[0]} onBack={noop} onStop={asyncNoop} />
    </I18nProvider>
  );
  assert.ok(workbenchZh.includes('data-testid="language-switcher"'));
  for (const label of ['云端存储', '在 Colab 中打开', '停止实例', 'Web 终端', '文件管理 (/content)', '代码编辑器', '硬件监控']) {
    assert.ok(workbenchZh.includes(label), `Missing "${label}" in Chinese SessionWorkbenchPage`);
  }
  assert.ok(!workbenchZh.includes('Stop Session'));

  // 7. Modals & Remaining Pages
  const modalsAndPagesZh = renderToStaticMarkup(
    <I18nProvider initialLocale="zh">
      <div>
        <LanguageSwitcher />
        <OAuthModal authUrl="https://accounts.google.com/o/oauth2" onComplete={asyncNoop} onClose={noop} />
        <NewSessionModal accounts={sampleAccounts} onLaunch={asyncNoop} onClose={noop} />
        <StorageWizardModal sessionId="sess_1" isOpen={true} onClose={noop} />
        <SSHHelperModal sessionId="sess_1" sessionName="llm" isOpen={true} onClose={noop} />
        <TemplatesPage />
        <JobsPage />
        <NotificationsPage />
      </div>
    </I18nProvider>
  );
  for (const label of [
    '绑定 Google 账号（无头远程授权流程）',
    '分配 Colab 计算实例',
    '自动选择最优账号（均衡配额）',
    '云端存储与挂载向导',
    'SSH 连接与 IDE 桥接',
    '环境模板与密钥保险箱',
    '异步训练与批处理任务',
    '全部任务',
    '多渠道告警通知中心',
  ]) {
    assert.ok(modalsAndPagesZh.includes(label), `Missing "${label}" in Chinese modals/pages`);
  }
});
