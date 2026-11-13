# Colab Management Platform (本地 Colab 资源管理器)

> **可单容器 Docker 部署的一站式本地工作台与常驻守护进程**  
> 专为 Google Colab 重度用户打造，解决多账号算力池化、断连防烧钱、VS Code / Cursor 稳定直连、代码/大模型权重分层同步与任务排队。

---

## 核心特性

- **多账号算力池化与自动选号**：多 Google 账号 OAuth2 集中接入，自动汇总 Compute Units (CCU) 余额与实时消耗速率，创建实例时智能选号与自动熔断降级。
- **孤儿实例自动发现与对账**：定时扫描网页端残留未关的 Colab 实例，提供一键纳管或停机，杜绝后台隐蔽扣费。
- **VS Code / Cursor Remote-SSH 端口池复用网关**：内置 AsyncSSH 代理网关监听本地 `2220-2229` 端口池，对上游维护单条 `/colab/ssh` WebSocket 隧道，完美破解 Colab 官方单实例仅允许 1 条 SSH 连接（并发报 `HTTP 429`）的硬限制。
- **生命周期三档守护引擎**：
  - `interactive`（交互开发）：检测空闲终端自动停机。
  - `keep_alive`（长训保活）：自动化后台心跳，防止意外挂断。
  - `ephemeral`（跑完即焚）：脚本任务执行完毕自动回收算力。
- **实例工作台核心工具**：
  - 基于 `xterm.js` 的低延迟 Web TTY 终端（直连 `/colab/tty`）。
  - 远程 `/content` 文件管理器（实时目录树、分片上传、下载、Monaco 在线代码编辑）。
  - 一键生成无缝嵌入官方 Web Notebook 的免密直达链接。
- **轻量代码与海量权重分层同步**：本地 `./workspace` 源码增量同步（自动忽略模型大文件），配合云端大文件挂载向导（Google Drive 网页弹窗授权挂载、S3/R2 `rclone` 注入、HuggingFace Token 预载）。
- **异步任务队列与非阻塞遥测**：支持 `.py` / `.ipynb` / `shell` 任务排队执行、实时日志流式推送与执行后 Notebook 自动导出；非阻塞侧信道采集 GPU 利用率、显存、CPU、内存及磁盘指标。

---

## 端口与卷映射架构

系统统一将 Web 控制台与 REST / WebSocket API 绑定至端口 **`1113`**，并通过固定本地端口池支持最多 10 个并发 SSH 直连实例：

| 宿主机端口 / 挂载卷 | 容器内部路径 / 目标 | 用途说明 |
|---|---|---|
| **`1113:1113`** | `0.0.0.0:1113` | Web 控制台 UI、FastAPI REST API 与 WebSocket 终端 |
| **`2220-2229:2220-2229`** | `0.0.0.0:2220-2229` | 本地 SSH 多路复用网关端口池（供 VS Code / Cursor 直连） |
| **`./data:/data`** | `/data` | 持久化数据目录：SQLite WAL 数据库（`/data/manager.db`）与任务日志 |
| **`./workspace:/workspace`** | `/workspace` | 本地源码目录：自动增量同步至 Colab 实例 `/content` |

---

## 快速开始

### 方式 A：Docker Compose 容器化部署（推荐）

1. **克隆项目并配置密钥**
   ```bash
   git clone https://github.com/Aftermath863/colab-management.git
   cd colab-management

   # 生成 32 字节 (64 位 16 进制) 主加密密钥
   export MASTER_KEY=$(openssl rand -hex 32)
   export ADMIN_PASSWORD="your_secure_password"
   ```

2. **启动服务**
   ```bash
   docker compose up -d --build
   ```

3. **访问 Web 控制台**
   - 打开浏览器访问：`http://localhost:1113`
   - 使用管理员密码登录系统。

---

### 方式 B：本地源码开发模式 (Python 3.12 + Node.js)

1. **启动后端服务**
   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt

   # 设置环境变量
   export PORT=1113
   export MASTER_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
   export ADMIN_PASSWORD="admin"
   export DB_PATH="../data/manager.db"
   export WORKSPACE_PATH="../workspace"

   # 启动 FastAPI 服务
   uvicorn app.main:app --host 0.0.0.0 --port 1113 --reload
   ```

2. **启动前端开发服务器**
   ```bash
   cd frontend
   npm install
   npm run dev
   # Vite 将启动在本地端口，并将 /api 与 /ws 自动代理至 http://localhost:1113
   ```

---

## 项目目录结构

```
colab-management/
├── docs/
│   └── ARCHITECTURE.md              # 权威系统架构设计与协议规范（端口 1113）
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI 统一入口 (端口 1113)
│   │   ├── config.py                # 环境变量与配置项
│   │   ├── core/
│   │   │   ├── security.py          # JWT 认证与 AES-256-GCM 凭证保管库
│   │   │   ├── database.py          # SQLite WAL 引擎与 Schema 初始化
│   │   │   └── models.py            # 数据模型与 Pydantic 校验规范
│   │   ├── services/
│   │   │   ├── colab_client.py      # 无状态 Colab 控制面与 Runtime 数据面适配层
│   │   │   ├── account_pool.py      # 多账号 CCU 轮询与孤儿对账池
│   │   │   ├── session_manager.py   # 会话生命周期与加速器调度管理
│   │   │   ├── ssh_multiplexer.py   # AsyncSSH 2220-2229 多路复用网关
│   │   │   ├── lifecycle_engine.py  # 交互空闲停机、心跳保活与熔断守护
│   │   │   ├── telemetry.py         # 侧信道非阻塞 GPU/RAM/Disk 采集器
│   │   │   ├── job_runner.py        # 异步任务队列与 Notebook 导出引擎
│   │   │   ├── storage_wizard.py    # GDrive 网页授权、S3/R2 与 HuggingFace 配置器
│   │   │   └── notification.py      # 飞书/企微/Telegram/Slack Webhook 告警
│   │   └── api/
│   │       ├── routes_auth.py       # 登录鉴权接口
│   │       ├── routes_accounts.py   # 多账号 OAuth 与状态接口
│   │       ├── routes_sessions.py   # 实例分配与控制接口
│   │       ├── routes_terminal.py   # WebSocket TTY 终端流接口
│   │       ├── routes_files.py      # /content 远程文件管理与 Monaco 编辑
│   │       ├── routes_templates.py  # 环境模板接口
│   │       ├── routes_jobs.py       # 任务投递与实时日志流
│   │       └── routes_telemetry.py  # 硬件指标接口
│   ├── tests/
│   │   ├── mock_colab/              # 零依赖 Mock Colab 测试桩服务端
│   │   ├── tier1_features/          # Tier 1 核心功能基准测试
│   │   ├── tier2_boundaries/        # Tier 2 边界与配额耗尽测试
│   │   ├── tier3_pairwise/          # Tier 3 状态转换组合测试
│   │   └── tier4_real_world/        # Tier 4 真实场景与断网恢复测试
│   ├── pyproject.toml               # Python 现代化构建配置
│   └── requirements.txt             # 核心依赖清单
├── frontend/
│   ├── src/
│   │   ├── main.tsx                 # 前端入口
│   │   ├── App.tsx                  # 页面路由与主框架
│   │   ├── components/              # Terminal (xterm), Monaco, 指标图表组件
│   │   ├── pages/                   # 控制台看板、实例列表、多账号、任务队列
│   │   └── services/                # API 客户端与 WebSocket 封装
│   ├── package.json
│   ├── vite.config.ts               # 开发代理配置 (指向 1113)
│   └── tailwind.config.js
├── data/                            # SQLite WAL 数据库持久化目录 (.gitkeep)
├── workspace/                       # 本地源码同步目录 (.gitkeep)
├── Dockerfile                       # 多阶段构建 (Node 前端构建 -> Python 3.12 运行环境)
├── docker-compose.yml               # 生产部署规范 (端口 1113:1113, 2220-2229:2220-2229)
├── .gitignore                       # 完整开发与生产过滤规则
└── README.md                        # 项目说明文档
```

---

## 环境变量说明

| 变量名 | 默认值 | 描述 |
|---|---|---|
| `PORT` | `1113` | Web 控制台与 REST/WS API 监听端口 |
| `ADMIN_PASSWORD` | `changeme` | 控制台管理员初始密码 |
| `MASTER_KEY` | *(必填)* | 32 字节十六进制主密钥，用于 AES-256-GCM 加密 Google Token 与 Secrets |
| `DB_PATH` | `/data/manager.db` | SQLite 数据库文件完整路径 |
| `WORKSPACE_PATH` | `/workspace` | 本地挂载代码工作目录 |
| `TZ` | `Asia/Shanghai` | 容器时区设置 |

---

## 阶段实现路线图

- **Milestone 0: 项目骨架与架构基准**
  - 交付 `docs/ARCHITECTURE.md`（统一 1113 端口）、Git 初始化、目录脚手架、构建与容器配置。
- **Milestone 1: 核心底座 MVP 闭环**
  - 交付管理员鉴权、AES-256-GCM 凭证保险箱、SQLite WAL 表、多账号远程 OAuth2、CCU 轮询、实例分配与释放、Web xterm.js 终端及 `/content` 文件管理。
- **Milestone 2: 高级连接、数据同步与环境自动化**
  - 交付 `asyncssh` 2220-2229 本地端口池多路复用网关、环境模板、云存储挂载向导（GDrive/S3/HF）与本地增量同步。
- **Milestone 3: 调度自动化与可观测性闭环**
  - 交付三档生命周期自动停机与熔断引擎、侧信道硬件遥测采集、异步任务队列及多渠道 Webhook 告警。
- **Milestone 4: 全流程自动化 E2E 验证与加固**
  - 基于内置 Mock Colab 服务端通过 85+ 项端到端测试用例并完成生产加固。

---

## 许可证

MIT License.
