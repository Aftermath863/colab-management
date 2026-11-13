"""Application configuration and environment settings for Colab Management Platform."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import List

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class _BaseConfig(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
        )
except ImportError:
    # Zero-dependency fallback for bootstrap test environments
    class _BaseConfig:  # type: ignore
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)


class Settings(_BaseConfig):
    # Server & Port Configuration (Mandatory Port 1113)
    PORT: int = int(os.getenv("PORT", "1113"))
    HOST: str = os.getenv("HOST", "0.0.0.0")

    # Security & Admin Authentication
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "changeme")
    ADMIN_PASSWORD_HASH: str = os.getenv("ADMIN_PASSWORD_HASH", "")
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))  # 24 hours

    # Storage Paths & Directories
    # In Docker container: /data and /workspace. In local test/dev: fallback to ./data if /data is not writable
    DATA_DIR: str = os.getenv("DATA_DIR", "/data")
    WORKSPACE_DIR: str = os.getenv("WORKSPACE_DIR", "/workspace")
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "")
    MASTER_KEY: str = os.getenv("MASTER_KEY", "")
    MASTER_KEY_PATH: str = os.getenv("MASTER_KEY_PATH", "")
    LOGS_DIR: str = os.getenv("LOGS_DIR", "")
    STATIC_DIR: str = os.getenv("STATIC_DIR", "/app/frontend/dist")

    # SSH Multiplexer Port Pool
    SSH_PORT_START: int = int(os.getenv("SSH_PORT_START", "2220"))
    SSH_PORT_END: int = int(os.getenv("SSH_PORT_END", "2229"))
    SSH_PUBLIC_HOST: str = os.getenv("SSH_PUBLIC_HOST", "localhost")

    # Lifecycle & Quota Defaults
    DEFAULT_WARNING_CCU: float = float(os.getenv("DEFAULT_WARNING_CCU", "10.0"))
    DEFAULT_HARD_STOP_CCU: float = float(os.getenv("DEFAULT_HARD_STOP_CCU", "2.0"))
    DEFAULT_IDLE_TIMEOUT_SEC: int = int(os.getenv("DEFAULT_IDLE_TIMEOUT_SEC", "1800"))
    CCU_POLL_INTERVAL_SEC: int = int(os.getenv("CCU_POLL_INTERVAL_SEC", "60"))
    TELEMETRY_INTERVAL_SEC: int = int(os.getenv("TELEMETRY_INTERVAL_SEC", "15"))

    # Google Cloud SDK Remote OAuth2 Details (google-colab-cli v0.7.5 protocol)
    OAUTH_CLIENT_ID: str = (
        "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com"
    )
    OAUTH_CLIENT_SECRET: str = "d-FL95Q19q7MQmFpd7hHD0Ty"
    OAUTH_REDIRECT_URI: str = "https://sdk.cloud.google.com/applicationdefaultauthcode.html"
    OAUTH_AUTH_URL: str = "https://accounts.google.com/o/oauth2/auth"
    OAUTH_TOKEN_URL: str = "https://oauth2.googleapis.com/token"
    OAUTH_USERINFO_URL: str = "https://www.googleapis.com/oauth2/v3/userinfo"
    OAUTH_SCOPES: List[str] = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/colaboratory",
        "https://www.googleapis.com/auth/drive.file",
    ]

    # Testing & Environment Flags
    TEST_MODE: bool = os.getenv("TEST_MODE", "").lower() in ("1", "true", "mock")
    CONTROL_PLANE_URL: str = os.getenv("CONTROL_PLANE_URL", "https://colab.research.google.com")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Adapt DATA_DIR if default /data is non-writable on host
        if self.DATA_DIR == "/data" and not os.path.exists("/data"):
            try:
                os.makedirs("/data", exist_ok=True)
            except (PermissionError, OSError):
                # Fallback to local ./data directory
                self.DATA_DIR = os.path.abspath(os.path.join(os.getcwd(), "data"))

        if self.WORKSPACE_DIR == "/workspace" and not os.path.exists("/workspace"):
            try:
                os.makedirs("/workspace", exist_ok=True)
            except (PermissionError, OSError):
                self.WORKSPACE_DIR = os.path.abspath(os.path.join(os.getcwd(), "workspace"))

        # Automatically derive dependent file paths if not explicitly configured
        if not self.DATABASE_PATH:
            self.DATABASE_PATH = os.path.join(self.DATA_DIR, "manager.db")
        if not self.MASTER_KEY_PATH:
            self.MASTER_KEY_PATH = os.path.join(self.DATA_DIR, ".master_key")
        if not self.LOGS_DIR:
            self.LOGS_DIR = os.path.join(self.DATA_DIR, "logs")

        # Static dir fallback for local development
        if not os.path.exists(self.STATIC_DIR):
            local_static = os.path.abspath(os.path.join(os.getcwd(), "frontend", "dist"))
            if os.path.exists(local_static):
                self.STATIC_DIR = local_static

    @property
    def ssh_port_pool(self) -> List[int]:
        return list(range(self.SSH_PORT_START, self.SSH_PORT_END + 1))


@lru_cache()
def get_settings() -> Settings:
    """Return a cached singleton instance of application settings."""
    return Settings()


settings = get_settings()
