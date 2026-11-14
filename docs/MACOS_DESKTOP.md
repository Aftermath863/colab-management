# Colab Manager — macOS Apple Silicon Desktop Edition (`arm64`)

> 原生 **Swift / AppKit / SwiftUI / WKWebView** 桌面壳 + 内嵌 **FastAPI (`:1113`) & AsyncSSH (`2220-2229`)** 本地守护进程，专为 Apple Silicon（M1/M2/M3/M4）macOS 14+ 打造。

---

## 1. 架构概览

Colab Manager macOS 桌面版采用**零第三方 Swift 依赖**的混合原生架构，将后端守护服务、前端工作台与 macOS 原生系统能力封装在单一 `Colab Manager.app`（Bundle ID: `com.colabmanager.desktop`，最低系统版本 macOS 14.0 Sonoma，`arm64` 原生指令集）中：

- **Native macOS Shell (`macos/`)**：基于 Apple 原生框架（`AppKit`、`SwiftUI`、`WebKit`、`UserNotifications`、`Network`、`Combine`）构建主窗口、Liquid Glass 引导层、Menu Bar 伴侣、`⌘L` 守护日志窗口与 `NSMenu` 系统菜单。
- **Backend Daemon Manager (`BackendDaemonManager.swift`)**：
  1. 启动时探测 `http://127.0.0.1:1113/healthz`。若本地已有实例（如 Docker Compose）在运行，则自动以**只读伴侣模式**接入（`daemonManaged = false`），不重复拉起进程。
  2. 若端口空闲，则依次检查 `COLAB_PYTHON_BIN`、应用内置 `.python_hint`、Homebrew / Conda / 系统 Python 3（验证 `fastapi`、`uvicorn`、`asyncssh`、`cryptography` 可用），在本地生成/读取权限为 `0600` 的桌面管理员密钥（`.desktop_admin_password`），并以独立工作目录启动：
     `python3 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 1113`
  3. 守护进程输出重定向至 `data/logs/daemon.log`，支持异常崩溃自动重启，并在应用退出时（当 `daemonManaged = true`）向子进程发送 `SIGTERM` 优雅回收。
- **Bundled Web Workbench (`frontend/dist`)**：打包至 `Contents/Resources/app/frontend/dist`，通过 `WKUserScript` 注入 `window.__COLAB_MACOS__` 实现桌面自动免密登录与原生能力桥接（`window.webkit.messageHandlers.colabNative`）。

---

## 2. 视觉与交互美学 (Obsidian & Liquid Glass)

桌面版遵循 macOS Sequoia / Tahoe **Obsidian & Liquid Glass** 设计语言：

- **半透明毛玻璃窗口 (`NSVisualEffectView`)**：主窗口（默认 `1320×840`，最小 `980×640`，自动记忆窗口位置 `ColabManagerMainWindow`）启用 `.fullSizeContentView`、隐藏透明标题栏与 `.darkAqua` 外观；底层 `.underWindowBackground` / `.behindWindow` 材质透过半透明 Web 表面（`html.is-mac-app` 下 `rgba(7, 11, 20, 0.78)`）呈现真实桌面透视虚化。
- **Obsidian 玻璃面板与微光反馈**：统一使用 `.mac-glass`、`.mac-card`（`1px` 顶部内高光描边）、鼠标光标追踪高光（`--spot-x` / `--spot-y`）、`.mac-pill-group` 分段胶囊控制器、弹性模态框动效以及等宽数字排版（`.tabular-nums`）。
- **Liquid-Glass 启动引导罩**：启动阶段显示带 Apple Silicon GPU 徽标与实时步骤状态的毛玻璃遮罩，并在 `/healthz` 就绪且 `WKWebView` 完成首次渲染后平滑淡出；异常时可直接点击重试或查看日志。
- **拖拽区与原生系统集成**：
  - 顶部标题栏及侧边栏安全区（`data-mac-drag-region="true"`）支持直接拖拽移动窗口与双击缩放，左侧为原生红黄绿交通灯按钮留出安全内边距。
  - **Haptic 触控反馈**：命令面板切换、SSH 复制等交互触发 `NSHapticFeedbackManager` 细腻震动。
  - **原生系统能力**：Finder 原生文件选择上传（`/content`）、一键在 `Terminal.app` 打开 `ssh -p <port> root@localhost`、一键通过 `cursor://` / `vscode://` 拉起 Remote-SSH（按需写入 `~/.ssh/config` `Host colab-local-<port>`）、外部 OAuth 与 Colab 页面自动跳转系统默认浏览器。
- **程序化定制 App 图标 (`AppIcon.icns`)**：由 `scripts/macos/generate_icon.swift` 使用 CoreGraphics / AppKit 在 `1024×1024` 画布上实时矢量绘制——连续 Apple Squircle 轮廓、深邃黑曜石渐变（`#0B1120 -> #111C33`）、神经网络栅格与芯片引脚纹理、内嵌磨砂玻璃高光环，以及融合 Indigo/Cyan 与 Colab Amber 的 3D 交织张量无穷核心。

---

## 3. Menu Bar Companion (菜单栏常驻伴侣)

桌面版在 macOS 顶部菜单栏提供常驻状态图标与 Liquid-Glass HUD 弹窗（`340×440`）：

- **实时状态徽标**：使用 SF Symbol 搭配实时 **活跃 GPU 数 / CCU** 标签；关闭主窗口后应用自动驻留菜单栏后台（`applicationShouldTerminateAfterLastWindowClosed = false`），点击 Dock 图标或菜单栏即可随时唤回。
- **Popover 实时面板**：
  - 守护进程健康状态（`127.0.0.1:1113`）与 AsyncSSH 多路复用端口池（`2220-2229`）状态
  - 算力池 **总 CCU 余额** 与 **实时消耗速率 (Burn Rate)** 卡片
  - **活跃 Colab 实例列表**：直接复制 SSH 命令、拉起 `Terminal.app`、拉起 `Cursor / VS Code` 或一键直达工作台
  - **快捷操作网格**：新建实例（`⌘N`）、命令面板（`⌘K`）、同步配额、打开 Workspace 目录、查看守护日志（`⌘L`）、显示主窗口、退出应用

---

## 4. 键盘快捷键

桌面版同时支持原生 `NSMenu` 快捷键与前端工作台全局快捷键：

| 快捷键 | 功能 | 说明 |
|---|---|---|
| `⌘K` | 打开命令面板 | Raycast 风格 Liquid-Glass 模糊搜索（快捷操作、活跃实例、页面跳转、语言切换） |
| `⌘N` | 新建计算实例 | 弹出 `NewSessionModal` 分配 Colab GPU/TPU/CPU 实例 |
| `⇧⌘R` | 同步全部配额 | 立即轮询所有已绑定 Google 账号的 CCU 余额与状态 |
| `⌘1` – `⌘6` | 主导航切换 | 在 Overview (`⌘1`) / Sessions (`⌘2`) / Jobs (`⌘3`) / Accounts (`⌘4`) / Templates (`⌘5`) / Notifications (`⌘6`) 间快速切换 |
| `⌘\` | 折叠/展开侧边栏 | 在完整侧边栏（`256px`）与紧凑图标栏（`68px`）间切换 |
| `⌘L` | 打开守护日志 | 打开原生 `DaemonLogsWindow` 实时 tail `daemon.log`（支持复制、清屏、打开目录、重启守护进程） |
| `⌃1` – `⌃4` | 切换工作台子标签 | 在实例工作台内切换 `Web 终端` / `文件管理 (/content)` / `代码编辑器` / `硬件监控` |
| `⌘C / ⌘V / ⌘X / ⌘A / ⌘Z` | 标准编辑操作 | 完整对接原生 Edit 菜单，确保 Monaco 编辑器与 xterm.js 终端剪贴板正常工作 |
| `Esc` | 关闭浮层 | 关闭当前模态框或 `⌘K` 命令面板 |

---

## 5. 本地数据与存储路径

为符合 macOS 桌面应用规范，打包运行的桌面端将所有持久化状态保存在用户 `Application Support` 目录，与源码仓库及 Docker 卷完全隔离：

| 用途 | 环境变量 | 默认桌面端本地路径 |
|---|---|---|
| **应用数据目录** | `DATA_DIR` | `~/Library/Application Support/ColabManager/data` |
| **SQLite WAL 数据库** | `DATABASE_PATH` | `~/Library/Application Support/ColabManager/data/manager.db` |
| **AES-256-GCM 主密钥** | `MASTER_KEY_PATH` | `~/Library/Application Support/ColabManager/data/.master_key` |
| **桌面管理员自动密钥** | — | `~/Library/Application Support/ColabManager/data/.desktop_admin_password` (`0600`) |
| **守护与任务日志目录** | `LOGS_DIR` | `~/Library/Application Support/ColabManager/data/logs`（含 `daemon.log`） |
| **本地源码增量同步目录** | `WORKSPACE_DIR` | `~/Library/Application Support/ColabManager/workspace` |
| **IDE SSH 配置（按需写入）** | — | `~/.ssh/config`（`Host colab-local-<port>`） |

> 提示：可以通过菜单栏 HUD 或命令面板中的「Reveal Workspace in Finder / Reveal Logs」快速在访达中打开上述目录。

---

## 6. 构建、运行与打包指南

### 6.1 构建前置要求

- **硬件与系统**：Apple Silicon Mac（`arm64`），macOS 14.0+
- **工具链**：Xcode Command Line Tools（提供 `swift`、`iconutil`、`codesign`、`plutil`、`hdiutil`、`rsync`）
- **前端依赖**：Node.js 20+ 与 `npm`
- **Python 运行环境**：Python 3.12+，已安装 `backend/requirements.txt` 中的依赖（至少包含 `fastapi`、`uvicorn`、`asyncssh`、`cryptography`）

### 6.2 常用 `Makefile` 命令

```bash
# 1. 构建完整的 Apple Silicon .app 应用包 (dist/Colab Manager.app)
make macos-app

# 2. 构建可直接分发的压缩磁盘映像 (dist/Colab-Manager-macOS-arm64.dmg)
make macos-dmg

# 3. 启动桌面端应用（若 dist/Colab Manager.app 尚不存在则自动构建）
make macos-run

# 4. 仅重建 Swift 壳与 .app 包，跳过前端 npm run build（需已存在 frontend/dist）
SKIP_FRONTEND_BUILD=1 make macos-app

# 5. 单独生成 1024x1024 多分辨率 AppIcon.icns
make macos-icon

# 6. 运行全量后端 pytest + 前端测试
make test

# 7. 清理 macOS 编译缓存与 dist 产物
make clean-macos
```

### 6.3 直接调用打包脚本

```bash
# 单独生成图标
swift scripts/macos/generate_icon.swift dist/AppIcon.icns

# 构建并 ad-hoc 签名 dist/Colab Manager.app
./scripts/macos/build_app.sh

# 指定自定义 Python 解释器并跳过前端重编
COLAB_PYTHON_BIN=/opt/homebrew/Caskroom/miniconda/base/bin/python3 \
SKIP_FRONTEND_BUILD=1 \
./scripts/macos/build_app.sh

# 打包 dist/Colab-Manager-macOS-arm64.dmg（含 /Applications 拖拽快捷方式）
./scripts/macos/build_dmg.sh
```

### 6.4 打包产物结构 (`dist/Colab Manager.app`)

`build_app.sh` 会自动校验 `Info.plist`（`plutil -lint`）、记录可用的 `.python_hint` 路径并执行深度 ad-hoc 签名校验（`codesign --verify --deep --strict`）：

```text
dist/Colab Manager.app/
└── Contents/
    ├── Info.plist                      # com.colabmanager.desktop, arm64, ATS localhost 白名单
    ├── PkgInfo                         # APPL????
    ├── MacOS/
    │   └── ColabManager                # Swift arm64 release 可执行文件
    └── Resources/
        ├── AppIcon.icns                # 16px - 1024px@2x 全套视网膜图标
        └── app/
            ├── .python_hint            # 构建期验证通过的 Python 3 路径
            ├── backend/
            │   ├── __init__.py
            │   ├── requirements.txt
            │   └── app/                # 干净的 FastAPI + AsyncSSH 后端源码
            └── frontend/
                └── dist/               # React 19 + Vite 生产静态资源
```
