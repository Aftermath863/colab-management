"""SQLite WAL database engine and schema management for Colab Management Platform."""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from typing import Any, Dict, Generator, Optional

from backend.app.config import settings

logger = logging.getLogger(__name__)

DDL_SCHEMA = """
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
    accelerator TEXT NOT NULL, -- CPU, T4, L4, G4, A100, H100, V5E1, V6E1
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
    target_shape TEXT NOT NULL DEFAULT 'standard',
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

-- Performance & Foreign Key Indexes
CREATE INDEX IF NOT EXISTS idx_sessions_account_id ON sessions(account_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_jobs_session_id ON jobs(session_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_telemetry_session_ts ON telemetry_samples(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);
"""


def get_db_path(custom_path: Optional[str] = None) -> str:
    """Return database file path, creating parent directories if missing."""
    db_path = custom_path or settings.DATABASE_PATH
    if db_path != ":memory:":
        parent_dir = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent_dir, exist_ok=True)
    return db_path


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize SQLite WAL database and create all 8 core tables and indexes."""
    target_path = get_db_path(db_path)
    logger.info(f"Initializing SQLite WAL database at {target_path}")

    conn = sqlite3.connect(target_path, timeout=10.0)
    try:
        # Enforce WAL mode, foreign keys, and concurrency timeouts
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")

        # Execute table definitions
        conn.executescript(DDL_SCHEMA)

        # Idempotent schema migrations for existing SQLite databases
        job_cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "target_shape" not in job_cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN target_shape TEXT NOT NULL DEFAULT 'standard'")

        # Ensure High-RAM-only accelerators (L4, V5E1, V6E1) reflect 'hm' in local records
        conn.execute(
            "UPDATE jobs SET target_shape = 'hm' WHERE target_accelerator IN ('L4', 'V5E1', 'V6E1') AND target_shape != 'hm'"
        )
        conn.execute(
            "UPDATE sessions SET shape = 'hm' WHERE accelerator IN ('L4', 'V5E1', 'V6E1') AND shape = 'standard'"
        )
        conn.commit()
    finally:
        conn.close()


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open and return a configured sqlite3 connection with Row factory and WAL PRAGMAs."""
    target_path = get_db_path(db_path)
    conn = sqlite3.connect(target_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


@contextlib.contextmanager
def get_db(db_path: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database operations with automatic commit on success and rollback on error."""
    conn = get_db_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def check_db_health(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Execute diagnostic checks against the database returning health status."""
    target_path = get_db_path(db_path)
    with get_db(target_path) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        fk_status = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
            ).fetchall()
            if not row[0].startswith("sqlite_")
        ]
        return {
            "status": "healthy",
            "path": target_path,
            "journal_mode": journal_mode,
            "foreign_keys_enabled": bool(fk_status),
            "table_count": len(tables),
            "tables": tables,
        }
