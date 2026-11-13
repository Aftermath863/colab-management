export type Locale = 'en' | 'zh';

const EXACT_ZH_ERRORS: Record<string, string> = {
  // Auth & Security
  'Incorrect admin password': '管理员密码错误',
  'Login failed': '登录失败',
  'Not authenticated': '未登录或会话已过期',
  'Authentication failed': '登录认证失败',
  'Missing authorization header': '缺少认证请求头',
  'Insufficient permissions': '权限不足',
  'Invalid or expired token': '登录凭证无效或已过期',

  // Accounts & OAuth
  'Failed to fetch accounts': '获取账号列表失败',
  'Failed to start OAuth': '启动 OAuth 授权失败',
  'OAuth verification failed': 'OAuth 授权验证失败',
  'Failed to complete OAuth authorization': '完成 OAuth 授权失败',
  'Failed to sync account': '同步账号信息失败',
  'Failed to delete account': '删除账号失败',

  // Sessions & SSH
  'Failed to fetch sessions': '获取实例列表失败',
  'Failed to create session': '创建实例失败',
  'Failed to provision session': '分配计算实例失败',
  'Failed to stop session': '停止实例失败',
  'Failed to get Colab deep link': '获取 Colab 直达链接失败',
  'Failed to fetch SSH config': '获取 SSH 配置失败',
  'Session runtime proxy information unavailable': '实例运行时代理信息暂不可用',
  'Colab runtime has an active SSH session (HTTP 429).': 'Colab 实例已有活跃的 SSH 会话连接（HTTP 429）',

  // Files & Workspace Sync
  'Failed to list contents': '获取目录列表失败',
  'Failed to list directory contents': '获取目录列表失败',
  'Failed to load file': '加载文件失败',
  'Failed to save file': '保存文件失败',
  'Failed to delete path': '删除路径失败',
  'Push sync failed': '工作区推送同步失败',
  'Workspace push sync failed': '工作区推送同步失败',
  'Pull sync failed': '工作区拉取同步失败',
  'Workspace pull sync failed': '工作区拉取同步失败',
  'Failed to fetch sync status': '获取同步状态失败',

  // Templates & Secrets
  'Failed to fetch templates': '获取环境模板失败',
  'Failed to load templates and secrets': '加载环境模板与密钥失败',
  'Failed to create template': '创建环境模板失败',
  'Failed to update template': '更新环境模板失败',
  'Failed to delete template': '删除环境模板失败',
  'Failed to fetch secrets': '获取密钥列表失败',
  'Failed to create secret': '创建密钥失败',
  'Failed to save secret': '保存密钥失败',
  'Failed to delete secret': '删除密钥失败',

  // Storage
  'Failed to fetch storage profiles': '获取存储配置失败',
  'Failed to create storage profile': '创建存储配置失败',
  'Failed to delete storage profile': '删除存储配置失败',
  'Google Drive credential propagation failed': 'Google Drive 凭证传递失败',
  'Google Drive propagation failed.': 'Google Drive 凭证传递失败。',
  'Failed to mount storage profile': '挂载存储配置失败',
  'Failed to mount storage profile.': '挂载存储配置失败。',
  'Storage mount failed': '存储挂载失败',
  'Failed to configure S3 storage.': '配置 S3 存储失败。',
  'Failed to authorize Hugging Face.': 'Hugging Face 授权失败。',
  'Failed to verify mount': '验证挂载点失败',

  // Jobs & Telemetry
  'Failed to fetch jobs': '获取任务列表失败',
  'Failed to fetch job details': '获取任务详情失败',
  'Failed to submit job': '提交任务失败',
  'Failed to cancel job': '取消任务失败',
  'Failed to fetch job logs': '获取任务日志失败',
  "Job is not of type 'notebook'": '该任务不是 Notebook 类型',
  'Failed to fetch telemetry metrics': '获取硬件监控指标失败',

  // Notifications
  'Failed to fetch notification channels': '获取通知渠道失败',
  'Failed to create channel': '创建通知渠道失败',
  'Failed to update channel': '更新通知渠道失败',
  'Failed to delete channel': '删除通知渠道失败',
  'Failed to save channel': '保存通知渠道失败',
  'Failed to test webhook channel': '测试 Webhook 渠道失败',
  'Test delivery failed': '测试发送失败',
  'Notification channel not found': '未找到该通知渠道',

  // Network / Generic
  'Failed to fetch': '网络请求失败，请检查服务连接',
  'NetworkError when attempting to fetch resource.': '网络连接异常，请检查服务连接',
  'API route not found': '未找到对应的 API 接口',
  'All connection attempts failed': '无法连接到目标服务器（所有连接尝试均失败）',

  // Backend Pool, Auth, Runtime & Proxy Errors
  'No eligible active accounts with available CCU quota in pool': '算力池中没有可用的活跃 Google 账号或 CCU 配额不足',
  'No eligible active Google accounts in pool with available CCU quota': '算力池中没有可用的活跃 Google 账号或 CCU 配额不足',
  'Missing access_token in Google OAuth response': 'Google OAuth 响应中缺少访问令牌 (access_token)',
  'Google account does not have an email address': '该 Google 账号未关联邮箱地址',
  'Colab control plane returned incomplete assignment payload': 'Colab 控制面返回的实例分配数据不完整',
  'Missing auth token': '缺少认证令牌',
  'Unauthorized role': '当前角色无访问权限',
  'Missing session proxy credentials': '缺少实例运行时代理凭证',
};

const ZH_STATUS_MAP: Record<string, string> = {
  ACTIVE: '正常',
  AUTH_EXPIRED: '授权过期',
  COOLDOWN: '冷却中',
  QUEUED: '排队中',
  RUNNING: '运行中',
  COMPLETED: '已完成',
  FAILED: '已失败',
  OOM_KILLED: '内存溢出 (137)',
  CANCELLED: '已取消',
  PROVISIONING: '分配中',
  BOOTSTRAPPING: '初始化中',
  READY: '就绪',
  BUSY: '运行中',
  ORPHAN: '孤儿实例',
  STOPPED: '已停止',
  LOST: '已失联',
};

function translateStatusToken(token: string): string {
  const upper = token.trim().toUpperCase();
  return Object.prototype.hasOwnProperty.call(ZH_STATUS_MAP, upper) ? ZH_STATUS_MAP[upper] : token;
}

function translateNestedZh(detail: string, depth: number): string {
  return translateErrorMessageInternal(detail, 'zh', depth + 1);
}

const PATTERN_ZH_ERRORS: Array<[RegExp, (matches: RegExpMatchArray, depth: number) => string]> = [
  [
    /^Colab quota exceeded or unsupported accelerator:\s*(.+)$/i,
    (m, d) => `Colab 配额不足或不支持该加速器：${translateNestedZh(m[1], d)}`,
  ],
  [
    /^Too many active assignments on Google account:\s*(.+)$/i,
    (m, d) => `Google 账号活跃实例数已达上限：${translateNestedZh(m[1], d)}`,
  ],
  [/^Session provisioning failed:\s*(.+)$/i, (m, d) => `实例分配失败：${translateNestedZh(m[1], d)}`],
  [/^Failed to stop session:\s*(.+)$/i, (m, d) => `停止实例失败：${translateNestedZh(m[1], d)}`],
  [/^Session\s+(\S+)\s+not found$/i, (m) => `未找到实例 ${m[1]}`],
  [/^Account\s+(\S+)\s+not found$/i, (m) => `未找到账号 ${m[1]}`],
  [/^Template\s+(\S+)\s+not found$/i, (m) => `未找到环境模板 ${m[1]}`],
  [/^Secret\s+(\S+)\s+not found$/i, (m) => `未找到密钥 ${m[1]}`],
  [/^Job\s+(\S+)\s+not found$/i, (m) => `未找到任务 ${m[1]}`],
  [/^Storage profile\s+(\S+)\s+not found$/i, (m) => `未找到存储配置 ${m[1]}`],
  [/^Notification channel\s+(\S+)\s+not found$/i, (m) => `未找到通知渠道 ${m[1]}`],
  [/^Specified account\s+(\S+)\s+does not exist$/i, (m) => `指定的账号 ${m[1]} 不存在`],
  [
    /^Account\s+(\S+)\s+is in status '(.+)' or below hard stop CCU \((.+)\)$/i,
    (m) => `账号 ${m[1]} 当前状态不可用（${translateStatusToken(m[2])}）或已低于熔断阈值（${m[3]}）`,
  ],
  [
    /^OAuth refresh token expired or revoked for account\s+(\S+)$/i,
    (m) => `账号 ${m[1]} 的 OAuth 刷新令牌已过期或被撤销`,
  ],
  [/^Channel with name '(.+)' already exists$/i, (m) => `名为“${m[1]}”的通知渠道已存在`],
  [/^Secret with key_name '(.+)' already exists$/i, (m) => `密钥名称“${m[1]}”已存在`],
  [/^Template with name '(.+)' already exists$/i, (m) => `名为“${m[1]}”的环境模板已存在`],
  [/^Storage profile with name '(.+)' already exists$/i, (m) => `名为“${m[1]}”的存储配置已存在`],
  [
    /^Cannot cancel job in state '(.+)'$/i,
    (m) => `当前状态（${translateStatusToken(m[1])}）下的任务无法取消`,
  ],
  [
    /^Output notebook for job\s+(\S+)\s+is not yet available \(status:\s*(.+)\)$/i,
    (m) => `任务 ${m[1]} 的输出 Notebook 尚未生成（当前状态：${translateStatusToken(m[2])}）`,
  ],
  [/^Cannot generate Colab URL for session\s+(\S+)$/i, (m) => `无法为实例 ${m[1]} 生成 Colab 链接`],
  [/^Failed to connect upstream WebSocket:\s*(.+)$/i, (m, d) => `连接上游 WebSocket 失败：${translateNestedZh(m[1], d)}`],
  [/^Failed AsyncSSH handshake over WebSocket bridge:\s*(.+)$/i, (m, d) => `WebSocket 隧道 SSH 握手失败：${translateNestedZh(m[1], d)}`],
  [/^Failed to create template:\s*(.+)$/i, (m, d) => `创建环境模板失败：${translateNestedZh(m[1], d)}`],
  [/^Failed to update template:\s*(.+)$/i, (m, d) => `更新环境模板失败：${translateNestedZh(m[1], d)}`],
  [/^Failed to store secret:\s*(.+)$/i, (m, d) => `保存密钥失败：${translateNestedZh(m[1], d)}`],
  [/^Failed to update secret:\s*(.+)$/i, (m, d) => `更新密钥失败：${translateNestedZh(m[1], d)}`],
  [/^Failed to sync with Google Colab:\s*(.+)$/i, (m, d) => `与 Google Colab 同步失败：${translateNestedZh(m[1], d)}`],
  [/^Failed to exchange authorization code:\s*(.+)$/i, (m, d) => `授权码换取令牌失败：${translateNestedZh(m[1], d)}`],
  [/^Failed to fetch userinfo from Google:\s*(.+)$/i, (m, d) => `获取 Google 账号信息失败：${translateNestedZh(m[1], d)}`],
  [/^Workspace push sync failed:\s*(.+)$/i, (m, d) => `工作区推送同步失败：${translateNestedZh(m[1], d)}`],
  [/^Workspace pull sync failed:\s*(.+)$/i, (m, d) => `工作区拉取同步失败：${translateNestedZh(m[1], d)}`],
  [/^Failed to create storage profile:\s*(.+)$/i, (m, d) => `创建存储配置失败：${translateNestedZh(m[1], d)}`],
  [/^Failed to update storage profile:\s*(.+)$/i, (m, d) => `更新存储配置失败：${translateNestedZh(m[1], d)}`],
  [
    /^Google Drive credential propagation failed:\s*(.+)$/i,
    (m, d) => `Google Drive 凭证传递失败：${translateNestedZh(m[1], d)}`,
  ],
  [/^Failed to mount storage:\s*(.+)$/i, (m, d) => `挂载存储失败：${translateNestedZh(m[1], d)}`],
  [/^Storage profile mount failed:\s*(.+)$/i, (m, d) => `挂载存储配置失败：${translateNestedZh(m[1], d)}`],
  [/^Storage preflight check failed:\s*(.+)$/i, (m, d) => `存储挂载预检失败：${translateNestedZh(m[1], d)}`],
  [/^Failed to provision job:\s*(.+)$/i, (m, d) => `分配任务运行实例失败：${translateNestedZh(m[1], d)}`],
  [/^Unsupported job_type:\s*(.+)$/i, (m) => `不支持的任务类型：${m[1]}`],
  [/^Unsupported storage profile type:\s*(.+)$/i, (m) => `不支持的存储配置类型：${m[1]}`],
  [/^Invalid notebook JSON format:\s*(.+)$/i, (m) => `Notebook JSON 格式无效：${m[1]}`],
  [/^Invalid workspace subpath '(.+)':\s*(.+)$/i, (m) => `无效的工作区子路径“${m[1]}”：${m[2]}`],
  [/^Network Error:\s*(.+)$/i, (m, d) => `网络错误：${translateNestedZh(m[1], d)}`],
  [
    /^Delivery failed after (\d+) attempts:\s*(.+)$/i,
    (m, d) => `重试 ${m[1]} 次后发送仍失败：${translateNestedZh(m[2], d)}`,
  ],
  [/^Error:\s*(.+)$/i, (m, d) => `错误：${translateNestedZh(m[1], d)}`],
  [
    /^No eligible (?:active )?(?:Google )?accounts(.*)$/i,
    () => '算力池中没有可用的活跃 Google 账号或 CCU 配额不足',
  ],
];

function translateErrorMessageInternal(
  message: string | null | undefined,
  locale: Locale,
  depth: number
): string {
  if (!message) return '';
  if (locale !== 'zh' || depth > 3) return message;

  const trimmed = message.trim();
  if (!trimmed) return message;

  if (Object.prototype.hasOwnProperty.call(EXACT_ZH_ERRORS, trimmed)) {
    return EXACT_ZH_ERRORS[trimmed];
  }

  for (const [pattern, formatter] of PATTERN_ZH_ERRORS) {
    const match = trimmed.match(pattern);
    if (match) {
      return formatter(match, depth);
    }
  }

  return message;
}

export function translateErrorMessage(message: string | null | undefined, locale: Locale): string {
  return translateErrorMessageInternal(message, locale, 0);
}
