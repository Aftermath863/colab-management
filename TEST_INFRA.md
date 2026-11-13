# Test Infrastructure Specification (`TEST_INFRA.md`)

## 1. Testing Philosophy & Quality Guarantees

The Colab Management platform adopts an **opaque-box, requirements-driven, zero-external-dependency** testing philosophy designed to guarantee production reliability, data safety, and resilience against external API failures.

### 1.1 Core Principles
1. **Zero External Cloud Dependencies (Hermetic CI)**:
   - Tests never call live Google APIs, Colab control planes, or public OAuth endpoints.
   - All network interactions are mediated through the local Mock Colab Suite (`backend/tests/mock_colab/`).
   - Tests execute with 100% success in offline environments and air-gapped CI containers.
2. **Dual-Execution Architecture**:
   - **In-Process ASGI Transport (`httpx.ASGITransport`)**: Used for sub-second REST API unit and contract tests without TCP socket overhead.
   - **Loopback Standalone Server Mode (`127.0.0.1:<ephemeral_port>`)**: Used for end-to-end WebSocket (`/colab/tty`, `/colab/ssh`), `asyncssh` multiplexing, and external CLI client tests.
3. **Deterministic & Fast Execution**:
   - Complete execution of Tiers 1-4 takes less than 30 seconds.
   - No arbitrary `sleep()` statements; synchronization is achieved via `asyncio.Event`, queue polling, or explicit state probes.
4. **Data Isolation & Clean State**:
   - Every test runs against an isolated in-memory or ephemeral SQLite database (`/tmp/manager_test_<uuid>.db`).
   - Mock servers expose `.reset()` hooks to purge sessions, tokens, and injected faults between test cases.
5. **Strict Protocol Fidelity**:
   - Mock servers emulate exact Google Colab quirks: `)]}'\n` XSSI prefixes, `X-Goog-Colab-Token` XSRF handshakes, `X-Colab-Ssh-Pubkey` header enforcement, RSA rejection (HTTP 400), and single-connection SSH locks (HTTP 429).

---

## 2. System Feature Matrix (F0 - F15)

The entire system is decomposed into 16 discrete features (F0 through F15). Each feature is mapped to its development milestone, primary test tier, test case count, and core verification invariants:

| Feature ID | Feature Name | Milestone | Target Tier | Test Count | Key Invariants & Verification Criteria | Target Module / Path |
|---|---|---|---|---|---|---|
| **F0** | Docs & Project Scaffolding | M0 | Tier 1 | 2 | `docs/ARCHITECTURE.md` exists with port `1113`; `pyproject.toml` and `package.json` parse cleanly; zero code in `.agents/`. | Root, `docs/` |
| **F1** | Auth & AES-256-GCM Vault | M1 | Tier 1, 2 | 11 | Admin JWT auth; Master Key derivation; AES-256-GCM encryption with unique IVs; tamper detection via auth tags; plaintext never stored. | `backend/app/core/security.py` |
| **F2** | Database & Models | M1 | Tier 1, 2 | 8 | SQLite WAL mode active; foreign keys enforced; atomic transactions; schema migrations execute cleanly; no data loss on rollback. | `backend/app/core/database.py`, `models.py` |
| **F3** | Multi-Account OAuth & Pool | M1 | Tier 1, 2, 3 | 12 | Remote OAuth code exchange; automatic CCU polling; burn rate calculation; priority auto-selector; orphan instance detection. | `backend/app/services/account_pool.py` |
| **F4** | Session Provisioning & Core | M1 | Tier 1, 2 | 10 | Two-step `assign` (GET token -> POST token); accelerator quota handling; `shape=hm` omitted for L4/V5E1/V6E1; two-step `unassign`. | `backend/app/services/session_manager.py` |
| **F5** | Web Console & Terminal (MVP) | M1 | Tier 1, 2 | 8 | React 19 UI on port 1113; `xterm.js` TTY bridge over WebSocket; remote `/content` REST file explorer; Monaco edit; Colab direct URL. | `frontend/`, `backend/app/api/routes_terminal.py` |
| **F6** | Docker Containerization (MVP) | M1 | Tier 1 | 2 | Multi-stage Dockerfile (Node 22 build -> Python runtime on 1113); `docker-compose.yml` maps 1113:1113, 2220-2229:2220-2229. | `Dockerfile`, `docker-compose.yml` |
| **F7** | AsyncSSH Multiplexer Gateway | M2 | Tier 1, 2, 3 | 9 | Host pool `2220-2229`; single upstream WebSocket to `/colab/ssh`; defeats HTTP 429; Ed25519/ECDSA accepted; RSA rejected (400). | `backend/app/services/ssh_multiplexer.py` |
| **F8** | Environment Templates & Vault | M2 | Tier 1, 2 | 7 | Template creation; pip/uv dependencies installed on boot; Vault secrets decrypted and injected into environment without log leaks. | `backend/app/services/storage_wizard.py` |
| **F9** | Cloud Storage Wizard & Mount | M2 | Tier 1, 2, 3 | 6 | Google Drive credential propagation (`/tun/m/credentials-propagation`); S3/R2 `rclone` config injection; HuggingFace auth. | `backend/app/services/storage_wizard.py` |
| **F10** | Workspace Incremental Sync | M2 | Tier 1, 2, 3 | 5 | Local `./workspace` watcher pushes diffs to remote `/content/workspace`; skips `.git` and `*.pt` weights; post-job pull. | `backend/app/services/workspace_sync.py` |
| **F11** | Lifecycle & Protection Engine | M3 | Tier 1, 2, 3 | 8 | 3 lifecycle modes (`interactive` idle shutdown, `keep_alive` heartbeat, `ephemeral` run-and-destroy); CCU circuit breaker. | `backend/app/services/lifecycle_engine.py` |
| **F12** | Non-Blocking Telemetry | M3 | Tier 1, 2, 3 | 6 | Async background sampling of GPU util %, VRAM, CPU, RAM, Disk; non-blocking sidecar; time-series storage; Recharts API. | `backend/app/services/telemetry.py` |
| **F13** | Async Job Runner & Notebooks | M3 | Tier 1, 2 | 8 | Queue for `.py`, `.ipynb`, and shell jobs; live log streaming (`/data/logs/<job_id>.log`); notebook cell execution and export. | `backend/app/services/job_runner.py` |
| **F14** | Multi-Channel Notifications | M3 | Tier 1, 2, 3 | 6 | Webhook dispatch to Feishu, WeCom, DingTalk, Telegram, Bark, Slack on job complete/failure/CCU alert; exponential backoff. | `backend/app/services/notification.py` |
| **F15** | E2E Testing Suite (Tiers 1-4) | M-E2E | Tiers 1-4 | 85+ | Zero-dependency mock suite (`mock_oauth.py`, `mock_control_plane.py`, `mock_runtime_proxy.py`, `mock_hub.py`); dual test runners. | `backend/tests/` |

---

## 3. Testing Tiers Architecture (Tiers 1 - 4)

```
+-----------------------------------------------------------------------------------+
|                           Tier 4: Real-World Workflows                            |
|             (4 full user journeys: Dev Day, LoRA Training, Failover, Partitions)  |
+-----------------------------------------------------------------------------------+
                                         ^
+-----------------------------------------------------------------------------------+
|                    Tier 3: Cross-Feature Pairwise Interactions                    |
|             (10 pairwise interaction tests validating systemic invariants)        |
+-----------------------------------------------------------------------------------+
                                         ^
+-----------------------------------------------------------------------------------+
|                      Tier 2: Boundary & Corner Cases                              |
|             (35 edge cases: 400/412/429/502 errors, quota limits, shape rules)   |
+-----------------------------------------------------------------------------------+
                                         ^
+-----------------------------------------------------------------------------------+
|                        Tier 1: Feature Coverage (MVP Core)                        |
|             (36 tests: nominal happy path across F1-F7, Vault, DB, TTY, Files)    |
+-----------------------------------------------------------------------------------+
```

### 3.1 Tier 1: Feature Coverage (>=35 Tests)
Validates nominal execution and API contracts across core components:
- **T1.1 (Auth & Vault)**: Admin login, JWT issuance, master key derivation, secret store, secret decrypt, secret delete.
- **T1.2 (Account Pool)**: Remote OAuth code paste, CCU balance polling, priority selection, token refresh, status transitions.
- **T1.3 (Lifecycle Engine)**: Provision `interactive` session, auto-idle shutdown, `keep_alive` heartbeat, `ephemeral` run-and-destroy, token auto-renewal.
- **T1.4 (Files & Workspace)**: Directory listing, chunked base64 upload, file download, Monaco online edit, delta sync.
- **T1.5 (Terminal & SSH)**: xterm.js TTY session, terminal resize, local port allocation (`2220-2229`), single upstream SSH connection, Ed25519 pubkey handshake.
- **T1.6 (Templates & Storage)**: Template creation, bootloader execution, vault secret injection, storage profile creation, Google Drive auth propagation.
- **T1.7 (Jobs & Telemetry)**: Async Python job, Jupyter notebook execution and export, shell command, sidecar telemetry sampling, Webhook dispatch.

### 3.2 Tier 2: Boundary & Corner Cases (>=35 Tests)
Validates resilience against errors, rate limits, resource exhaustion, and adversarial inputs:
- **T2.1 (Security & Vault)**: Tampered JWT token (401), invalid `MASTER_KEY` at boot, 64KB unicode/binary secrets, 20 concurrent logins, SQL injection in secret key names.
- **T2.2 (Quota & Accounts)**: Account CCU drops to 0.00, exact boundary `hard_stop_ccu`, external refresh token revocation (`invalid_grant`), duplicate Google email collision, burn rate spike alert (>10 CCU/hr).
- **T2.3 (Provisioning & Shape)**: HTTP 412 concurrency limit (`TooManyAssignmentsError`), HTTP 400 quota exhaustion, high-RAM shape omission (`shape=hm` omitted for `L4`/`V5E1`/`V6E1`), orphan detection via `GET /tun/m/assignments`, rapid cancellation during assign.
- **T2.4 (Files & Data)**: 100MB chunked upload, token expiry mid-upload with auto-retry, path traversal (`../../etc/passwd`), circular symlinks, complex `.gitignore` negations.
- **T2.5 (SSH & TTY)**: Direct second connection to `/colab/ssh` receives HTTP 429, legacy RSA key rejected with HTTP 400, upstream WebSocket reconnect, port pool exhaustion (11th session), 50MB terminal text burst backpressure.
- **T2.6 (Templates & Boot)**: Startup script exit code 1, template references deleted secret, invalid S3 credentials fail-fast, startup script timeout watchdog, shell injection in environment variables safely escaped.
- **T2.7 (Jobs & System)**: Linux OOM killer detection, corrupted `.ipynb` JSON parsing error, Webhook endpoint HTTP 500 retry backoff, telemetry sampling under 100% CPU/GPU saturation, queue handling of 50 jobs on 2 sessions.

### 3.3 Tier 3: Cross-Feature Pairwise Interactions (>=10 Tests)
Validates system invariants across interacting subsystem boundaries:
1. `T3.1` (F2 Account x F3 Lifecycle): Account quota depletion triggers graceful session shutdown and automatic reroute to Account B.
2. `T3.2` (F3 Lifecycle x F5 SSH Mux): Active SSH traffic on port 2220 resets the idle timeout timer.
3. `T3.3` (F3 Lifecycle x F5 SSH Mux): Session teardown immediately closes local port 2220 and returns it to the port pool.
4. `T3.4` (F1 Vault x F6 Templates): Injected secrets are accessible in remote environment but strictly redacted from logs and telemetry.
5. `T3.5` (F6 Storage x F7 Jobs): Job execution strictly verifies Google Drive mount is active before executing user script.
6. `T3.6` (F4 Sync x F7 Jobs): Local workspace delta syncs before job start; output artifacts sync back to host before session teardown.
7. `T3.7` (F4 Sync x F4 Monaco): Remote Monaco edit conflict detection prevents silent overwrite by workspace sync.
8. `T3.8` (F2 Account x F7 Webhook): Account dropping below `warning_ccu` dispatches throttled card alert to Telegram/Feishu.
9. `T3.9` (F7 Telemetry x F7 Webhook): Sustained GPU VRAM > 98% for 3 intervals triggers high-priority alert.
10. `T3.10` (F2 Account x F3 Lifecycle): Fleet-wide orphan discovery surfaces unmanaged Colab instances with one-click cleanup.

### 3.4 Tier 4: Real-World Workflows (>=4 Scenarios)
1. `T4.1` (Interactive Dev Day): Admin login -> OAuth account add -> Launch T4 session -> Web TTY inspection -> Monaco edit `train.py` -> VS Code Dual SSH connection over local port 2220 -> Auto-idle shutdown after 15m.
2. `T4.2` (Overnight LoRA Training): Submit batch LoRA job with A100 target -> Fallback to L4 on quota 400 -> Telemetry records GPU/VRAM curves -> Weights sync to `./workspace/output` -> Ephemeral session destroyed -> Webhook notification delivered.
3. `T4.3` (Fleet Quota Failover & Orphan Cleanup): 2 accounts (one near quota) -> Job 1 pauses on quota limit and failovers to Account 2 -> Fleet scanner discovers orphan web instance -> Admin terminates orphan in 1 click.
4. `T4.4` (Token Renewal & Network Partition Recovery): Long-running session -> Proxy token renewed at 5m margin -> Upstream network partition simulated -> Gateway retries with exponential backoff -> SSH session stays alive without terminal reset.

---

## 4. Test Runner Invocation Commands

### 4.1 Pytest Runner (Recommended for Development & CI)
Prerequisites: `pytest`, `pytest-asyncio`, `httpx` installed.

```bash
# 1. Run complete E2E test suite (Tiers 1-4)
OAUTHLIB_INSECURE_TRANSPORT=1 pytest backend/tests/ -v

# 2. Run Tier 1 Feature Coverage tests only
OAUTHLIB_INSECURE_TRANSPORT=1 pytest backend/tests/ -v -m tier1

# 3. Run Tier 2 Boundary & Corner tests only
OAUTHLIB_INSECURE_TRANSPORT=1 pytest backend/tests/ -v -m tier2

# 4. Run Tier 3 Cross-Feature Interaction tests only
OAUTHLIB_INSECURE_TRANSPORT=1 pytest backend/tests/ -v -m tier3

# 5. Run Tier 4 Real-World Workflow scenarios only
OAUTHLIB_INSECURE_TRANSPORT=1 pytest backend/tests/ -v -m tier4

# 6. Run with fail-fast (-x), short traceback, and execution timeout
OAUTHLIB_INSECURE_TRANSPORT=1 pytest backend/tests/ -x --tb=short --timeout=30

# 7. Run specific Mock Colab server unit tests
OAUTHLIB_INSECURE_TRANSPORT=1 pytest backend/tests/mock_colab/ -v
```

### 4.2 Python `unittest` Zero-Dependency Runner (Offline / Bootstrap CI)
Prerequisites: Standard Python 3.10+ only (zero third-party dependencies required).

```bash
# 1. Standard library test discovery
OAUTHLIB_INSECURE_TRANSPORT=1 python -m unittest discover -s backend/tests -p "test_*.py" -v

# 2. Standalone zero-dependency test runner script
OAUTHLIB_INSECURE_TRANSPORT=1 python backend/tests/run_all_tests.py

# 3. Direct self-verification of Mock OAuth Server
python -m unittest backend/tests/mock_colab/test_mock_colab_suite.py
```

### 4.3 Environment Variables Reference
- `OAUTHLIB_INSECURE_TRANSPORT=1`: Mandatory for allowing OAuth2 flows over loopback `http://`.
- `TEST_MODE=mock`: Enforces usage of mock Colab control plane and runtime proxies.
- `MASTER_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef`: 32-byte hex key for AES-256-GCM vault tests.
- `ADMIN_PASSWORD=testadminpass`: Default admin password for test authentication.
- `DATABASE_URL=sqlite:///:memory:`: Forces in-memory SQLite database for test isolation.
