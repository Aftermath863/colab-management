"""Pydantic data models, schemas, and validators for Colab Management Platform."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

# --- Enumerations and Literals ---

AcceleratorType = Literal["CPU", "T4", "L4", "G4", "A100", "H100", "V5E1", "V6E1"]
MachineShape = Literal["standard", "hm"]
LifecyclePolicy = Literal["interactive", "keep_alive", "ephemeral"]
AccountStatus = Literal["ACTIVE", "AUTH_EXPIRED", "COOLDOWN"]
SessionStatus = Literal[
    "PROVISIONING",
    "BOOTSTRAPPING",
    "READY",
    "BUSY",
    "ORPHAN",
    "STOPPED",
    "LOST",
    "FAILED",
]
JobType = Literal["python", "notebook", "shell"]
JobStatus = Literal[
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "OOM_KILLED",
]
StorageType = Literal["gdrive", "s3", "hf"]
NotificationProvider = Literal[
    "feishu", "dingtalk", "wecom", "telegram", "bark", "slack", "webhook"
]

HIGH_MEM_ONLY_ACCELERATORS = frozenset({"L4", "V5E1", "V6E1"})


def normalize_accelerator_value(value: Any) -> Any:
    """Normalize accelerator strings (e.g. Google's 'NONE' -> 'CPU', uppercase)."""
    if isinstance(value, str):
        cleaned = value.strip().upper()
        if cleaned == "NONE":
            return "CPU"
        return cleaned
    return value


# --- Account Models ---

class AccountBase(BaseModel):
    email: str
    display_name: Optional[str] = None
    warning_ccu: float = 10.0
    hard_stop_ccu: float = 2.0
    priority: int = 1


class AccountCreate(AccountBase):
    id: str
    oauth_creds_enc: str
    ccu_balance: float = 0.0
    burn_rate_hourly: float = 0.0
    active_assignments: int = 0
    status: AccountStatus = "ACTIVE"


class AccountSummary(BaseModel):
    id: str
    email: str
    display_name: Optional[str] = None
    ccu_balance: float
    burn_rate_hourly: float
    active_assignments: int
    warning_ccu: float
    hard_stop_ccu: float
    priority: int
    status: AccountStatus
    last_synced_at: Optional[str] = None


class AccountInDB(AccountSummary):
    oauth_creds_enc: str


# --- Session Models ---

class SessionCreateRequest(BaseModel):
    name: str
    accelerator: AcceleratorType = "T4"
    shape: MachineShape = "standard"
    lifecycle_policy: LifecyclePolicy = "interactive"
    idle_timeout_sec: int = 1800
    account_id: Optional[str] = None
    template_id: Optional[str] = None
    workspace_subpath: Optional[str] = ""
    remote_workdir: Optional[str] = "/content"

    @field_validator("accelerator", mode="before")
    @classmethod
    def _normalize_accelerator(cls, v: Any) -> Any:
        return normalize_accelerator_value(v)

    @model_validator(mode="after")
    def validate_high_ram_rules(self) -> SessionCreateRequest:
        if self.accelerator in HIGH_MEM_ONLY_ACCELERATORS:
            # L4/V5E1/V6E1 only exist as High-RAM machines on Colab.
            # Persist 'hm' in DB/models; ColabClient.assign() omits shape=hm on the HTTP wire.
            self.shape = "hm"
        return self


class SessionSummary(BaseModel):
    id: str
    name: str
    account_id: str
    account_email: Optional[str] = None
    notebook_hash: str
    endpoint: str
    proxy_url: str
    accelerator: str
    shape: str
    lifecycle_policy: str
    idle_timeout_sec: int
    ssh_local_port: Optional[int] = None
    workspace_subpath: str
    remote_workdir: str
    template_id: Optional[str] = None
    status: SessionStatus
    created_at: str
    stopped_at: Optional[str] = None
    colab_web_url: Optional[str] = None


class SessionInDB(SessionSummary):
    proxy_token_enc: str
    token_expires_at: str


# --- Template Models ---

class TemplateBase(BaseModel):
    name: str
    packages: List[str] = Field(default_factory=list)
    secret_ids: List[str] = Field(default_factory=list)
    storage_profile_ids: List[str] = Field(default_factory=list)
    startup_script: str = ""


class TemplateCreate(TemplateBase):
    pass


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    packages: Optional[List[str]] = None
    secret_ids: Optional[List[str]] = None
    storage_profile_ids: Optional[List[str]] = None
    startup_script: Optional[str] = None


class TemplateResponse(TemplateBase):
    id: str
    updated_at: str


class TemplateInDB(BaseModel):
    id: str
    name: str
    packages_json: str
    secret_ids_json: str
    storage_profile_ids_json: str
    startup_script: str
    updated_at: str


# --- Secret Models ---

class SecretCreate(BaseModel):
    key_name: str
    value: str
    description: str = ""


class SecretUpdate(BaseModel):
    value: Optional[str] = None
    description: Optional[str] = None


class SecretResponse(BaseModel):
    id: str
    key_name: str
    description: str = ""
    created_at: str


class SecretInDB(SecretResponse):
    value_enc: str


# --- Storage Profile Models ---

class StorageProfileCreate(BaseModel):
    name: str
    type: StorageType
    config: Dict[str, Any]
    mount_path: str = "/content/data"


class StorageProfileUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    mount_path: Optional[str] = None


class StorageProfileResponse(BaseModel):
    id: str
    name: str
    type: StorageType
    mount_path: str
    created_at: Optional[str] = None


class StorageProfileInDB(StorageProfileResponse):
    config_enc: str


# --- SSH & Sync Models ---

class SSHConfigResponse(BaseModel):
    session_id: str
    host: str = "localhost"
    port: Optional[int] = None
    username: str = "root"
    connection_command: str
    vscode_config: str


class SyncFileDetail(BaseModel):
    path: str
    action: str  # uploaded, downloaded, skipped, deleted, error
    size: int = 0
    reason: str = ""
    error: Optional[str] = None


class SyncPushRequest(BaseModel):
    dry_run: bool = False
    delete_missing: bool = False
    max_file_size_mb: int = 50
    exclude_patterns: Optional[List[str]] = None


class SyncPullRequest(BaseModel):
    dry_run: bool = False
    max_file_size_mb: int = 50
    exclude_patterns: Optional[List[str]] = None


class SyncResultResponse(BaseModel):
    session_id: str
    action: str  # push, pull
    dry_run: bool = False
    files_scanned: int = 0
    files_uploaded: Optional[int] = None
    files_downloaded: Optional[int] = None
    files_skipped: int = 0
    files_deleted: Optional[int] = None
    total_bytes_transferred: int = 0
    duration_ms: int = 0
    details: List[SyncFileDetail] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None


# --- Job Models ---

class JobCreate(BaseModel):
    name: str
    target_accelerator: AcceleratorType
    target_shape: MachineShape = "standard"
    allow_fallback: bool = True
    template_id: Optional[str] = None
    job_type: JobType = "python"
    entry_path_or_cmd: str
    args: List[str] = Field(default_factory=list)

    @field_validator("target_accelerator", mode="before")
    @classmethod
    def _normalize_target_accelerator(cls, v: Any) -> Any:
        return normalize_accelerator_value(v)

    @model_validator(mode="after")
    def validate_high_ram_rules(self) -> JobCreate:
        if self.target_accelerator in HIGH_MEM_ONLY_ACCELERATORS:
            self.target_shape = "hm"
        return self


class JobResponse(BaseModel):
    id: str
    name: str
    session_id: Optional[str] = None
    target_accelerator: str
    target_shape: str = "standard"
    allow_fallback: bool
    template_id: Optional[str] = None
    job_type: str
    entry_path_or_cmd: str
    args: List[str]
    status: JobStatus
    exit_code: Optional[int] = None
    log_path: str
    output_notebook_path: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class JobInDB(BaseModel):
    id: str
    name: str
    session_id: Optional[str] = None
    target_accelerator: str
    target_shape: str = "standard"
    allow_fallback: int
    template_id: Optional[str] = None
    job_type: str
    entry_path_or_cmd: str
    args_json: str
    status: str
    exit_code: Optional[int] = None
    log_path: str
    output_notebook_path: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


# --- Telemetry Models ---

class TelemetrySample(BaseModel):
    session_id: str
    ts: Optional[str] = None
    kernel_state: str = "idle"
    cpu_pct: float = 0.0
    ram_used_mb: float = 0.0
    ram_total_mb: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    gpu_name: Optional[str] = None
    gpu_util_pct: Optional[float] = None
    vram_used_mb: Optional[float] = None
    vram_total_mb: Optional[float] = None


class TelemetryResponse(BaseModel):
    session_id: str
    latest: Optional[TelemetrySample] = None
    history: List[TelemetrySample] = Field(default_factory=list)


# --- Notification Channel Models ---

class NotificationChannelCreate(BaseModel):
    name: str
    provider: NotificationProvider
    config: Dict[str, Any]
    events: List[str] = Field(
        default_factory=lambda: [
            "job_completed",
            "job_failed",
            "quota_warning",
            "orphan_discovered",
        ]
    )
    enabled: bool = True


class NotificationChannelResponse(BaseModel):
    id: str
    name: str
    provider: NotificationProvider
    events: List[str]
    enabled: bool
    config_masked: Optional[Dict[str, Any]] = None


# --- Auth & OAuth Request/Response Models ---

class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"


class UserStatusResponse(BaseModel):
    authenticated: bool
    role: str
    is_default_password: bool


class OAuthStartResponse(BaseModel):
    auth_url: str
    state: Optional[str] = None


class OAuthCompleteRequest(BaseModel):
    code: str
    state: Optional[str] = None


class ColabUrlResponse(BaseModel):
    url: str
