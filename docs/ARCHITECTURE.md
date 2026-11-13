# 本地 Colab 资源管理器 — 系统架构与详细设计文档 (docs/ARCHITECTURE.md)

## 1. 系统定位与核心痛点解决

### 1.1 系统定位
本地 Colab 资源管理器（Colab Management Platform）是专为深度学习开发者、算法研究员与重度 Google Colab 用户打造的**单容器轻量化本地工作台与常驻守护系统**。
系统在本地（支持 Docker 单容器部署或宿主机直接运行）提供现代化 Web 控制台、无头 SSH 多路复用网关、跨账号算力池化调度以及全生命周期防烧钱熔断守护。

### 1.2 核心痛点与解决方案
1. **防遗忘烧钱与防断连机制失衡**：原生 Colab 网页版无操作易断连，但放任后台运行又极易耗尽宝贵的 Compute Units (CCU)。系统提供 `interactive`（空闲自动停机）、`keep_alive`（长训防断连保活）与 `ephemeral`（跑完即焚）三档生命周期策略，并具备余额阈值熔断机制（10 CCU 告警，2 CCU 硬停机）。
2. **多 Google 账号算力碎片化**：支持同时接入多个 Google 账号，自动聚合 CCU 余额与小时消耗速率，并在创建会话或排队任务时根据余量、配额和优先级实现跨账号自动选号与故障降级。
3. **原生 CLI 的单实例并发与连接限制**：`google-colab-cli` 原生单例存在状态混乱问题，且 Colab 远端实例严格限制同一时间仅允许 1 条 `/colab/ssh` WebSocket 连接（第 2 条直接返回 `HTTP 429: Already-active SSH session`）。系统重构了无状态多租户适配器，并内置 `asyncssh` 本地端口池（`2220-2229`）多路复用网关，完美支撑 VS Code / Cursor Remote-SSH 多窗口及 SFTP 并发直连。
4. **轻量代码与海量数据分层同步**：本地源码目录增量同步远程 `/content`，自动忽略 Git 元数据与大权重文件；大体积数据集与模型权重走云端直挂向导（Google Drive 网页弹窗授权传播、S3/R2 `rclone` 注入、HuggingFace Token 注入）。

---

## 2. 总体架构与系统拓扑

### 2.1 物理拓扑与端口分配 (Port 1113 Constraint)
- **Web 控制台与 API 端口**：**`1113:1113`**（根据用户严格要求设定为 1113）。
- **本地 SSH 多路复用端口池**：**`2220-2229:2220-2229`**（10 个连续宿主机端口，动态分配映射给活跃会话）。
- **持久化存储卷**：
  - `./data:/data`：存储 SQLite WAL 数据库 (`/data/manager.db`)、主密钥文件 (`/data/.master_key`)、任务执行日志 (`/data/logs/`)。
  - `./workspace:/workspace`：本地代码目录，供增量同步引擎与远程 `/content` 进行双向/单向代码同步。

```
+----------------------------------------------------------------------------------------------------+
|                                    Host Machine / Client Side                                      |
|                                                                                                    |
|   +-----------------------+     +-----------------------------+     +--------------------------+   |
|   |  Browser Web Console  |     |   VS Code / Cursor IDE      |     |  Local Workspace Files   |   |
|   | (React 19, Port 1113) |     | (Remote-SSH, Port 2220-2229)|     |     (./workspace)        |   |
|   +-----------+-----------+     +--------------+--------------+     +------------+-------------+   |
+---------------|--------------------------------|---------------------------------|-----------------+
                | HTTP/WS :1113                  | SSH :2220-2229                  | Local File Watch
+---------------v--------------------------------v---------------------------------v-----------------+
|                               Colab Management Platform (Docker Container)                         |
|                                                                                                    |
|  +----------------------------------------------------------------------------------------------+  |
|  | Frontend: React 19 + TypeScript + Vite + Tailwind CSS + xterm.js + Monaco + Recharts        |  |
|  +----------------------------------------------+-----------------------------------------------+  |
|                                                 | In-Memory / Static SPA Serve                      |
|  +----------------------------------------------v-----------------------------------------------+  |
|  | Backend: FastAPI + Uvicorn Async Core (Port 1113)                                           |  |
|  |                                                                                              |  |
|  |  +--------------------------+  +--------------------------+  +----------------------------+  |  |
|  |  | Auth & AES-256-GCM Vault |  | Multi-Account Pool &     |  | Session Lifecycle Engine   |  |  |
|  |  | (JWT, MASTER_KEY, PBKDF2)|  | Quota Reconciler (CCU)   |  | (Idle, Keepalive, Breaker) |  |  |
|  |  +--------------------------+  +--------------------------+  +----------------------------+  |  |
|  |                                                                                              |  |
|  |  +--------------------------+  +--------------------------+  +----------------------------+  |  |
|  |  | AsyncSSH Gateway Mux     |  | Workspace Sync Engine    |  | Telemetry & Job Runner     |  |  |
|  |  | (Ports 2220-2229 Mux)    |  | (Delta Watcher & Ignore) |  | (GPU/VRAM, Queue, Webhooks)|  |  |
|  |  +--------------------------+  +--------------------------+  +----------------------------+  |  |
|  +----------------------------------------------+-----------------------------------------------+  |
|                                                 |                                                  |
|  +----------------------------------------------v-----------------------------------------------+  |
|  | Database Layer: SQLite WAL Mode (/data/manager.db) - 8 Core Tables                            |  |
|  +----------------------------------------------------------------------------------------------+  |
+-------------------------------------------------|--------------------------------------------------+
                                                  | Upstream HTTPS & WSS
                                                  v
+----------------------------------------------------------------------------------------------------+
|                                    Google Colab Infrastructure                                     |
|                                                                                                    |
|  +---------------------------------------------+  +---------------------------------------------+  |
|  | Control Plane (colab.research.google.com)   |  | Runtime Proxy (*.googleusercontent.com)     |  |
|  | - GET /tun/m/ccu-info                       |  | - /api/contents/{path} (Jupyter REST)       |  |
|  | - GET /tun/m/assignments                    |  | - wss://.../colab/tty (Web Terminal)       |  |
|  | - GET|POST /tun/m/assign (XSRF Token)       |  | - wss://.../colab/ssh (Single WS SSH Mux)   |  |
|  | - GET|POST /tun/m/unassign                  |  | - /tun/m/credentials-propagation/{endpoint} |  |
|  +---------------------------------------------+  +---------------------------------------------+  |
+----------------------------------------------------------------------------------------------------+
```

---

## 3. 核心子模块详细设计

### 3.1 安全与凭据保险箱 (Auth & AES-256-GCM Vault)
1. **管理员鉴权**：
   - 环境变量 `ADMIN_PASSWORD`（默认 `changeme`），使用 PBKDF2-HMAC-SHA256 加盐哈希存储。
   - 登录成功后签发 HS256 JWT Token，后续 REST 及 WebSocket 请求通过 `Authorization: Bearer <token>` 校验。
2. **主密钥与 AES-256-GCM 保险箱**：
   - 主密钥 `MASTER_KEY`：必须为 32 字节（256 位）。系统优先从环境变量 `MASTER_KEY` 读取；若未设置，则从 `/data/.master_key` 加载；若文件不存在，则由 `os.urandom(32)` 生成并保存（权限设为 `0600`）。
   - 数据加密：采用 `cryptography.hazmat.primitives.ciphers.aead.AESGCM`。加密时生成 12 字节随机 `nonce`，密文格式为 `Base64(nonce + ciphertext + tag)`。
   - 保险箱保护对象：Google OAuth Refresh Token、会话 Runtime Proxy Token、环境变量 Secret、云存储访问凭据（S3 Secret Key、HF Token、Webhook 签名秘钥）。

### 3.2 多 Google 账号池化与调度引擎 (Account Pool & Scheduler)
1. **无头 OAuth2 远程授权流程**：
   - 针对 Docker 无头环境，采用 Google Cloud SDK 官方授权流程：
     - Client ID: `764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com`
     - Scopes: `openid`, `userinfo.profile`, `userinfo.email`, `cloud-platform`, `colaboratory`, `drive.file`
     - Redirect URI: `https://sdk.cloud.google.com/applicationdefaultauthcode.html`
     - 参数：`prompt="consent"`, `token_usage="remote"`
     - 用户在浏览器打开授权链接，登录并授权后，Google 页面显示一串 Authorization Code，用户将其粘贴回 Web 控制台完成绑定。
2. **算力与用量对账守护**：
   - 后台协程每 60 秒轮询一次各账号 `GET /tun/m/ccu-info`，更新 `ccu_balance` 与 `burn_rate_hourly`。
   - 轮询 `GET /tun/m/assignments` 扫描活跃实例。发现数据库未记录的实例时，标记为 `ORPHAN`（孤儿实例），并在 UI 上发出高亮告警及一键销毁按钮，彻底杜绝后台网页端残留实例造成的巨额烧钱。
3. **跨账号自动选号算法**：
   - 当创建会话或提交任务未显式指定账号时，调度器自动进行评分决策：
     `Score = (Priority * 1000) + (ccu_balance * 10) - (active_assignments * 200)`
   - 过滤条件：`status == 'ACTIVE'` 且 `ccu_balance > hard_stop_ccu`。若首选账号报 HTTP 412（并发上限）或 HTTP 400（无指定 GPU 配额），系统自动降级回退到备选账号。

### 3.3 Colab 适配器协议细节与无全局状态重构
1. **协议规范**：
   - 请求 Headers 必须携带：`Accept: application/json` 与 `X-Colab-Client-Agent: colab-cli`。
   - 请求域名为 `colab.research.google.com` 时自动追加 Query 参数 `authuser=0`。
   - **XSSI 防护前缀剥离**：Control Plane 所有 JSON 响应体前缀均带有 `)]}'\n`，解析前必须执行：
     ```python
     if content.startswith(b")]}'\n"):
         content = content[5:]
     ```
   - **两阶段 XSRF 分配握手**：
     - Step 1: `GET /tun/m/assign?nbh=<web_safe_b64_uuid>&variant=GPU&accelerator=T4&shape=hm`。若实例已存在，直接返回 `Assignment`；若为新实例，返回 `GetAssignmentResponse` 包含短期 XSRF `token`。
     - Step 2: 携带 Header `X-Goog-Colab-Token: <token>` 执行 `POST /tun/m/assign?...`。
   - **高显存机型形态特殊规则**：
     - 定义常量集合：`HIGH_MEM_ONLY_ACCELERATORS = frozenset({'L4', 'V5E1', 'V6E1'})`。
     - 若请求加速器为 `L4`、`V5E1` 或 `V6E1`，URL 中**严禁**携带 `shape=hm`（Colab 后端此机型默认且仅有高内存形态，传入会报参数错误）。
   - **两阶段实例释放**：
     - Step 1: `GET /tun/m/unassign/{endpoint}` 获取 XSRF `token`。
     - Step 2: 携带 Header `X-Goog-Colab-Token: <token>` 执行 `POST /tun/m/unassign/{endpoint}`。
   - **Proxy Token 自动续期机制**：
     - Runtime Proxy Token 有效期通常为 7200 秒（2 小时）。系统无需单独调用刷新接口，只需由后台定期调用 `GET /tun/m/assignments`，Google 返回的列表中即会包含自动延期更新的 `runtimeProxyInfo.token` 与 `tokenExpiresInSeconds`，守护进程无缝更新数据库中的加密 Token。

### 3.4 SSH 多路复用网关 (AsyncSSH Multiplexer Gateway)
1. **痛点与突破**：
   - Colab 原生 `/colab/ssh` 接口在同一实例上仅允许 1 条活跃 WebSocket 连接。VS Code Remote-SSH 会并发打开主 Shell 通道、扩展通道与 SFTP 文件通道，若直连 Colab 会立即触发 `HTTP 429` 导致断连。
2. **网关架构实现**：
   - 守护进程内置 `asyncssh` 服务器，监听本地端口池 `0.0.0.0:2220-2229`。
   - 针对每个活跃会话分配固定本地端口（例如 Session 1 分配 `2220`）。
   - 当第一个外部 SSH 连接到达 `localhost:2220` 时，守护进程与远端建立唯一的 `wss://{netloc}/colab/ssh?colab-runtime-proxy-token={token}` WebSocket（Header 注入 `X-Colab-Ssh-Pubkey`，仅允许 Ed25519 或 ECDSA，RSA 报 400）。
   - 外部客户端的所有并发 SSH 通道全部通过本地 `asyncssh` 网关透明转发至该单条上行 WebSocket，彻底规避 HTTP 429。
   - 会话销毁时，本地 SSH 监听端口立即关闭并归还端口池。

### 3.5 会话工作台三件套
1. **Web `xterm.js` 实时终端**：
   - 前端集成 `@xterm/xterm`，通过 WebSocket 连接 `/api/sessions/{id}/terminal`。
   - 后端桥接至远端 `wss://{netloc}/colab/tty?colab-runtime-proxy-token={token}`。
   - 双向传输 JSON 文本帧：输入输出为 `{"data": "..."}`，终端尺寸调整为 `{"cols": C, "rows": R}`。
2. **远程 `/content` 文件管理器与 Monaco 编辑器**：
   - 封装 Jupyter Contents REST API（`GET/PUT/DELETE /api/contents/{quoted_path}`）。
   - 支持树状目录浏览、大文件分片 Base64 上传、文件下载。
   - 深度集成 `@monaco-editor/react`，支持在线查看、语法高亮与直接修改保存远端代码。
3. **官方 Colab 网页直连 URL**：
   - 一键生成并打开直连页面：
     `https://colab.research.google.com/notebooks/empty.ipynb?authuser={email}&dbu=%2Ftun%2Fm%2F{endpoint}#datalabBackendUrl=https://colab.research.google.com/tun/m/{endpoint}`
   - 规则：`dbu` 参数必须进行 URL 编码（`%2Ftun%2Fm%2F...`），而 `#datalabBackendUrl` Hash Fragment **严禁** URL 编码。

### 3.6 生命周期守护与熔断引擎 (Lifecycle Engine)
- **`interactive`（交互式）**：监控 Web Terminal 与 SSH 端口数据流量。若连续处于无流量状态超过 `idle_timeout_sec`（默认 1800 秒），自动执行 `unassign` 并释放 SSH 端口。
- **`keep_alive`（长训保活）**：后台定时向 Runtime 代理发送轻量心跳探针，防止 Google 空闲回收。
- **`ephemeral`（跑完即焚）**：异步任务完成后，系统自动抓取输出物并立即调用销毁接口。
- **额度熔断保护**：
  - 当账号 CCU 余额 ≤ `warning_ccu`（默认 10.0）：向所有配置的通知渠道推送黄色预警。
  - 当账号 CCU 余额 ≤ `hard_stop_ccu`（默认 2.0）：触发硬熔断，自动安全停机并阻止新建实例。

### 3.7 侧信道无阻塞遥测采集 (Telemetry Collector)
- 独立异步采集协程，每 15 秒通过非阻塞执行通道执行微型脚本采集性能指标：
  - GPU 型号、利用率 %、已用/总显存（`nvidia-smi --query-gpu=... --format=csv`）。
  - CPU 使用率、RAM 已用/总量（`/proc/meminfo` 与 `/proc/stat`）。
  - 磁盘已用/总容量（`df -h /content`）。
- 历史数据存入 SQLite `telemetry_samples` 表，前端通过 Recharts 绘制实时动态趋势图。

### 3.8 异步任务队列与多渠道通知 (Job Runner & Webhooks)
- 支持排队投递 Python 脚本（`.py`）、Jupyter Notebook（`.ipynb`）及 Bash 命令。
- 日志实时落盘至 `/data/logs/{job_id}.log`，支持前端 WebSocket / SSE 实时滚动查看。
- Notebook 任务执行完成后，自动导出附带执行结果与图形绘制的输出 `.ipynb` 供本地下载。
- 多渠道 Webhook 推送适配：飞书（Feishu）、企业微信（WeCom）、钉钉（DingTalk）、Telegram、Bark、Slack。

---

## 4. 数据库核心表结构 (SQLite WAL Mode -> `/data/manager.db`)

数据库启用 WAL 模式（`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;`）。以下为 8 张核心表的完整 DDL 定义：

```sql
-- 1. accounts: Google 账号池与额度状态
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT,
    oauth_creds_enc TEXT NOT NULL,
    ccu_balance REAL NOT NULL DEFAULT 0.0,
    burn_rate_hourly REAL NOT NULL DEFAULT 0.0,
    active_assignments INTEGER NOT NULL DEFAULT 0,
    warning_ccu REAL NOT NULL DEFAULT 10.0,
    hard_stop_ccu REAL NOT NULL DEFAULT 2.0,
    priority INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE', -- ACTIVE, AUTH_EXPIRED, COOLDOWN
    last_synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. templates: 环境配置与启动脚本模板
CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    packages_json TEXT NOT NULL DEFAULT '[]', -- ["torch", "transformers"]
    secret_ids_json TEXT NOT NULL DEFAULT '[]', -- ["secret_hf", "secret_wandb"]
    storage_profile_ids_json TEXT NOT NULL DEFAULT '[]',
    startup_script TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. secrets: 凭据保险箱
CREATE TABLE IF NOT EXISTS secrets (
    id TEXT PRIMARY KEY,
    key_name TEXT UNIQUE NOT NULL,
    value_enc TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. storage_profiles: 云端存储挂载配置
CREATE TABLE IF NOT EXISTS storage_profiles (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL, -- gdrive, s3, hf
    config_enc TEXT NOT NULL,
    mount_path TEXT NOT NULL DEFAULT '/content/data'
);

-- 5. sessions: 活跃实例与生命周期
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    account_id TEXT NOT NULL,
    notebook_hash TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    proxy_url TEXT NOT NULL,
    proxy_token_enc TEXT NOT NULL,
    token_expires_at TIMESTAMP NOT NULL,
    accelerator TEXT NOT NULL, -- T4, L4, G4, A100, H100, V5E1, V6E1
    shape TEXT NOT NULL DEFAULT 'standard', -- standard, hm
    lifecycle_policy TEXT NOT NULL DEFAULT 'interactive', -- interactive, keep_alive, ephemeral
    idle_timeout_sec INTEGER NOT NULL DEFAULT 1800,
    ssh_local_port INTEGER UNIQUE, -- 2220..2229
    workspace_subpath TEXT NOT NULL DEFAULT '',
    remote_workdir TEXT NOT NULL DEFAULT '/content',
    template_id TEXT,
    status TEXT NOT NULL DEFAULT 'PROVISIONING', -- PROVISIONING, BOOTSTRAPPING, READY, BUSY, ORPHAN, STOPPED, LOST, FAILED
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    stopped_at TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE,
    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE SET NULL
);

-- 6. jobs: 异步任务队列
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    session_id TEXT,
    target_accelerator TEXT NOT NULL,
    allow_fallback BOOLEAN NOT NULL DEFAULT 1,
    template_id TEXT,
    job_type TEXT NOT NULL, -- python, notebook, shell
    entry_path_or_cmd TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'QUEUED', -- QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED, OOM_KILLED
    exit_code INTEGER,
    log_path TEXT NOT NULL,
    output_notebook_path TEXT,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL,
    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE SET NULL
);

-- 7. telemetry_samples: 遥测监控时序数据
CREATE TABLE IF NOT EXISTS telemetry_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    kernel_state TEXT NOT NULL DEFAULT 'idle',
    cpu_pct REAL NOT NULL DEFAULT 0.0,
    ram_used_mb REAL NOT NULL DEFAULT 0.0,
    ram_total_mb REAL NOT NULL DEFAULT 0.0,
    disk_used_gb REAL NOT NULL DEFAULT 0.0,
    disk_total_gb REAL NOT NULL DEFAULT 0.0,
    gpu_name TEXT,
    gpu_util_pct REAL,
    vram_used_mb REAL,
    vram_total_mb REAL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- 8. notification_channels: 告警推送渠道
CREATE TABLE IF NOT EXISTS notification_channels (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    provider TEXT NOT NULL, -- feishu, wecom, telegram, bark, slack, webhook
    config_enc TEXT NOT NULL,
    events_json TEXT NOT NULL DEFAULT '["job_completed","job_failed","quota_warning","orphan_discovered"]',
    enabled BOOLEAN NOT NULL DEFAULT 1
);
```

---

## 5. Docker 容器化与部署规范 (Port 1113)

### 5.1 多阶段构建 Dockerfile
- **Stage 1 (Frontend Builder)**: Node 22 Alpine，构建 React 19 前端工程生成静态文件至 `/app/frontend/dist`。
- **Stage 2 (Runtime Container)**: Python 3.12-slim，安装 `rsync`、`openssh-client`、`curl` 以及后端 Python 依赖；拷贝 Stage 1 静态资源至 `/app/static`；FastAPI 托管 SPA 前端页面并暴露端口 `1113` 与 `2220-2229`。

### 5.2 docker-compose.yml 标准定义 (严格约束 1113:1113)
```yaml
services:
  colab-manager:
    build: .
    container_name: colab-manager
    restart: unless-stopped
    ports:
      - "1113:1113"           # Web 控制台与 REST/WebSocket API (用户强制约束)
      - "2220-2229:2220-2229" # 本地 SSH 多路复用直连端口池 (供 VS Code / Cursor 连接)
    environment:
      - PORT=1113
      - ADMIN_PASSWORD=${ADMIN_PASSWORD:-changeme}
      - MASTER_KEY=${MASTER_KEY}
      - TZ=Asia/Shanghai
    volumes:
      - ./data:/data
      - ./workspace:/workspace
```

---

## 6. 分阶段实现路线图 (Phased Implementation Roadmap)

### 6.1 Phase 0: 架构冻结与项目底座脚手架 (Milestone 0)
- 交付权威 `docs/ARCHITECTURE.md`（统一端口 `1113` 与 `2220-2229`）。
- 初始化 Git 仓库、`.gitignore`、`README.md`、`pyproject.toml`、`package.json`、多阶段 `Dockerfile` 与 `docker-compose.yml`。

### 6.2 Phase 1: MVP 核心底座闭环 (Milestone 1)
- 后端基础架构：FastAPI、JWT 认证、AES-256-GCM 密钥保险箱、SQLite WAL 数据库。
- 多 Google 账号远程 OAuth2 授权码绑定、CCU 轮询、孤儿实例自动发现。
- 实例核心操作：两阶段 `assign` 与 `unassign`、自动选号调度。
- Web 控制台工作台三件套：`xterm.js` 实时 Web 终端 + 远程 `/content` 文件树/Monaco 编辑 + 官方 Colab 网页直连 URL。

### 6.3 Phase 2: 高级连接、云端存储与增量同步 (Milestone 2)
- `asyncssh` 本地端口池 (`2220-2229`) 多路复用网关，彻底突破 Colab 单 WebSocket 的 HTTP 429 限制，实现 VS Code / Cursor Remote-SSH 稳定直连。
- 环境配置模板、保险箱秘钥安全注入、Google Drive 网页弹窗授权挂载、S3/R2/HF 存储向导。
- 本地 `./workspace` 增量同步引擎（自动跳过大模型权重与 Git 元数据）。

### 6.4 Phase 3: 生命周期自动化、遥测采集与异步任务 (Milestone 3)
- 三档生命周期引擎（`interactive` 空闲自动关机、`keep_alive` 长训保活、`ephemeral` 跑完即焚）与额度熔断。
- 侧信道非阻塞 GPU/RAM/Disk 实时遥测采集与前端 Recharts 动态曲线展示。
- 异步任务执行队列（`.py`/`.ipynb`/shell 排队执行、实时日志流、执行后 `.ipynb` 自动导出）。
- 多渠道 Webhook 告警通知（飞书、企业微信、Telegram 等）。

### 6.5 Phase 4: 全量 E2E 测试验收与防御性加固 (Milestone 4)
- 零外部依赖 Mock Colab 服务器驱动的 4 级全量测试套件（Tier 1 功能覆盖 36 题、Tier 2 边界异常 35 题、Tier 3 成对交互 10 题、Tier 4 真实全流程场景 4 套）及对抗性注入加固。
