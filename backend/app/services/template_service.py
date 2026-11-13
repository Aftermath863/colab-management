"""Environment Template & Secrets Vault Management Service.

Provides CRUD for secrets (AES-256-GCM encrypted in SQLite), environment templates,
secret injection into Colab execution sessions, and log/telemetry sanitization.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shlex
import time
import uuid
from typing import Any, Dict, List, Optional

from backend.app.config import settings
from backend.app.core.database import get_db
from backend.app.core.models import (
    SecretCreate,
    SecretResponse,
    SecretUpdate,
    TemplateCreate,
    TemplateResponse,
    TemplateUpdate,
)
from backend.app.core.security import decrypt_secret, encrypt_secret
from backend.app.services.colab_client import ColabClient

logger = logging.getLogger("template_service")


# ---------------------------------------------------------------------------
# 1. Sanitizer & Redaction Engine
# ---------------------------------------------------------------------------

class LogAndTelemetrySanitizer:
    """Interprets active environment secrets and redacts them from logs and telemetry."""

    def __init__(self, secrets_to_redact: Dict[str, str]):
        # Keep non-empty secrets with meaningful length to avoid replacing single characters
        self.secrets = {
            k: v for k, v in secrets_to_redact.items() if v and len(str(v).strip()) >= 3
        }

    def sanitize_text(self, text: str) -> str:
        if not text:
            return text
        sanitized = text
        # Sort secrets by length descending to prevent shorter prefixes from truncating composite secrets
        sorted_secrets = sorted(
            self.secrets.items(), key=lambda item: len(str(item[1])), reverse=True
        )
        for key_name, secret_val in sorted_secrets:
            escaped = re.escape(str(secret_val))
            replacement = f"[REDACTED:{key_name}]"
            sanitized = re.sub(escaped, replacement, sanitized)
        return sanitized

    def sanitize_log_file(self, input_path: str, output_path: Optional[str] = None) -> None:
        target_out = output_path or input_path
        if not os.path.isfile(input_path):
            return
        with open(input_path, "r", encoding="utf-8", errors="ignore") as fin:
            content = fin.read()
        cleaned = self.sanitize_text(content)
        with open(target_out, "w", encoding="utf-8") as fout:
            fout.write(cleaned)

    def sanitize_telemetry_payload(self, payload: Any) -> Any:
        """Recursively sanitize nested dictionaries and arrays."""
        if isinstance(payload, str):
            return self.sanitize_text(payload)
        elif isinstance(payload, dict):
            return {k: self.sanitize_telemetry_payload(v) for k, v in payload.items()}
        elif isinstance(payload, list):
            return [self.sanitize_telemetry_payload(x) for x in payload]
        return payload


# ---------------------------------------------------------------------------
# 2. Secret Vault Operations (AES-256-GCM)
# ---------------------------------------------------------------------------

def create_secret(data: SecretCreate) -> Dict[str, Any]:
    """Encrypt and store secret in vault. Returns SecretResponse dictionary."""
    secret_id = f"sec_{uuid.uuid4().hex[:8]}"
    enc_value = encrypt_secret(data.value)

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM secrets WHERE key_name = ?", (data.key_name,)
        ).fetchone()
        if existing:
            raise ValueError(f"Secret with key_name '{data.key_name}' already exists")

        conn.execute(
            """
            INSERT INTO secrets (id, key_name, value_enc, description)
            VALUES (?, ?, ?, ?)
            """,
            (secret_id, data.key_name, enc_value, data.description or ""),
        )
        row = conn.execute(
            "SELECT id, key_name, description, created_at FROM secrets WHERE id = ?",
            (secret_id,),
        ).fetchone()
        return dict(row)


def list_secrets() -> List[Dict[str, Any]]:
    """List all secrets (metadata only, values strictly omitted)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, key_name, description, created_at FROM secrets ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_secret(secret_id: str) -> Optional[Dict[str, Any]]:
    """Get secret metadata by ID."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, key_name, description, created_at FROM secrets WHERE id = ?",
            (secret_id,),
        ).fetchone()
        return dict(row) if row else None


def update_secret(secret_id: str, data: SecretUpdate) -> Dict[str, Any]:
    """Update secret value and/or description."""
    with get_db() as conn:
        row = conn.execute("SELECT id FROM secrets WHERE id = ?", (secret_id,)).fetchone()
        if not row:
            raise ValueError(f"Secret {secret_id} not found")

        updates = []
        params = []
        if data.value is not None:
            updates.append("value_enc = ?")
            params.append(encrypt_secret(data.value))
        if data.description is not None:
            updates.append("description = ?")
            params.append(data.description)

        if updates:
            params.append(secret_id)
            conn.execute(
                f"UPDATE secrets SET {', '.join(updates)} WHERE id = ?",
                params,
            )

        updated_row = conn.execute(
            "SELECT id, key_name, description, created_at FROM secrets WHERE id = ?",
            (secret_id,),
        ).fetchone()
        return dict(updated_row)


def delete_secret(secret_id: str) -> bool:
    """Delete secret and prune references from templates."""
    with get_db() as conn:
        row = conn.execute("SELECT id FROM secrets WHERE id = ?", (secret_id,)).fetchone()
        if not row:
            return False

        # Delete from secrets table
        conn.execute("DELETE FROM secrets WHERE id = ?", (secret_id,))

        # Prune references in templates.secret_ids_json
        templates = conn.execute("SELECT id, secret_ids_json FROM templates").fetchall()
        for tpl in templates:
            try:
                ids = json.loads(tpl["secret_ids_json"])
                if secret_id in ids:
                    ids = [x for x in ids if x != secret_id]
                    conn.execute(
                        "UPDATE templates SET secret_ids_json = ? WHERE id = ?",
                        (json.dumps(ids), tpl["id"]),
                    )
            except Exception:
                pass

        return True


def resolve_secret_value(secret_id: str) -> str:
    """Internal: Decrypt and return plaintext secret value."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT value_enc FROM secrets WHERE id = ?", (secret_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Secret {secret_id} not found")
        return decrypt_secret(row["value_enc"])


# ---------------------------------------------------------------------------
# 3. Environment Template Operations
# ---------------------------------------------------------------------------

def create_template(data: TemplateCreate) -> Dict[str, Any]:
    """Store template configuration and return TemplateResponse."""
    template_id = f"tpl_{uuid.uuid4().hex[:8]}"
    packages_json = json.dumps(data.packages)
    secret_ids_json = json.dumps(data.secret_ids)
    storage_profile_ids_json = json.dumps(data.storage_profile_ids)

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM templates WHERE name = ?", (data.name,)
        ).fetchone()
        if existing:
            raise ValueError(f"Template with name '{data.name}' already exists")

        conn.execute(
            """
            INSERT INTO templates (
                id, name, packages_json, secret_ids_json,
                storage_profile_ids_json, startup_script
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                template_id,
                data.name,
                packages_json,
                secret_ids_json,
                storage_profile_ids_json,
                data.startup_script or "",
            ),
        )

    return get_template(template_id)  # type: ignore


def list_templates() -> List[Dict[str, Any]]:
    """List all templates with parsed JSON lists."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, name, packages_json, secret_ids_json,
                   storage_profile_ids_json, startup_script, updated_at
            FROM templates ORDER BY updated_at DESC
            """
        ).fetchall()
        result = []
        for r in rows:
            result.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "packages": json.loads(r["packages_json"] or "[]"),
                    "secret_ids": json.loads(r["secret_ids_json"] or "[]"),
                    "storage_profile_ids": json.loads(r["storage_profile_ids_json"] or "[]"),
                    "startup_script": r["startup_script"] or "",
                    "updated_at": str(r["updated_at"]),
                }
            )
        return result


def get_template(template_id: str) -> Optional[Dict[str, Any]]:
    """Get single template by ID."""
    with get_db() as conn:
        r = conn.execute(
            """
            SELECT id, name, packages_json, secret_ids_json,
                   storage_profile_ids_json, startup_script, updated_at
            FROM templates WHERE id = ?
            """,
            (template_id,),
        ).fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "name": r["name"],
            "packages": json.loads(r["packages_json"] or "[]"),
            "secret_ids": json.loads(r["secret_ids_json"] or "[]"),
            "storage_profile_ids": json.loads(r["storage_profile_ids_json"] or "[]"),
            "startup_script": r["startup_script"] or "",
            "updated_at": str(r["updated_at"]),
        }


def update_template(template_id: str, data: TemplateUpdate) -> Dict[str, Any]:
    """Update template configuration."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM templates WHERE id = ?", (template_id,)
        ).fetchone()
        if not existing:
            raise ValueError(f"Template {template_id} not found")

        updates = ["updated_at = CURRENT_TIMESTAMP"]
        params = []
        if data.name is not None:
            updates.append("name = ?")
            params.append(data.name)
        if data.packages is not None:
            updates.append("packages_json = ?")
            params.append(json.dumps(data.packages))
        if data.secret_ids is not None:
            updates.append("secret_ids_json = ?")
            params.append(json.dumps(data.secret_ids))
        if data.storage_profile_ids is not None:
            updates.append("storage_profile_ids_json = ?")
            params.append(json.dumps(data.storage_profile_ids))
        if data.startup_script is not None:
            updates.append("startup_script = ?")
            params.append(data.startup_script)

        params.append(template_id)
        conn.execute(
            f"UPDATE templates SET {', '.join(updates)} WHERE id = ?",
            params,
        )

    return get_template(template_id)  # type: ignore


def delete_template(template_id: str) -> bool:
    """Delete template."""
    with get_db() as conn:
        row = conn.execute("SELECT id FROM templates WHERE id = ?", (template_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        return True


def resolve_template_env(template_id: str) -> Dict[str, str]:
    """Resolve and decrypt all vault secrets associated with an environment template."""
    with get_db() as conn:
        tpl = conn.execute(
            "SELECT secret_ids_json FROM templates WHERE id = ?", (template_id,)
        ).fetchone()
        if not tpl:
            raise ValueError(f"Template {template_id} not found")

        secret_ids = json.loads(tpl["secret_ids_json"] or "[]")
        env: Dict[str, str] = {}
        for sec_id in secret_ids:
            sec_row = conn.execute(
                "SELECT key_name, value_enc FROM secrets WHERE id = ?", (sec_id,)
            ).fetchone()
            if sec_row:
                key = sec_row["key_name"]
                val = decrypt_secret(sec_row["value_enc"])
                env[key] = val
        return env


# ---------------------------------------------------------------------------
# 4. Session Bootstrap Execution Engine
# ---------------------------------------------------------------------------

async def execute_session_bootstrap(
    session_id: str,
    template_id: str,
    client: Optional[ColabClient] = None,
) -> bool:
    """Bootstrap a Colab session: inject secrets, install packages, run startup script."""
    colab = client or ColabClient()
    logger.info(f"Starting bootstrap runner for session {session_id} using template {template_id}...")

    # Set status to BOOTSTRAPPING
    with get_db() as conn:
        conn.execute(
            "UPDATE sessions SET status = 'BOOTSTRAPPING' WHERE id = ?",
            (session_id,),
        )

    # Fetch session and template details
    with get_db() as conn:
        sess = conn.execute(
            "SELECT id, proxy_url, proxy_token_enc FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not sess:
            logger.error(f"Bootstrap failed: session {session_id} not found")
            return False

        proxy_url = sess["proxy_url"]
        proxy_token = decrypt_secret(sess["proxy_token_enc"])

    template = get_template(template_id)
    if not template:
        logger.warning(f"Template {template_id} not found, marking session READY")
        with get_db() as conn:
            conn.execute("UPDATE sessions SET status = 'READY' WHERE id = ?", (session_id,))
        return False

    # 1. Resolve secrets and configure sanitizer
    secrets_dict = resolve_template_env(template_id)
    sanitizer = LogAndTelemetrySanitizer(secrets_dict)

    # 2. Build shell environment export script (.colab_env.sh)
    env_lines = ["#!/bin/bash", "# Injected Colab Platform Environment Secrets"]
    for k, v in secrets_dict.items():
        escaped_v = shlex.quote(v)
        env_lines.append(f"export {k}={escaped_v}")
    env_lines.append("export RCLONE_CONFIG=/root/.config/rclone/rclone.conf")
    env_lines.append("export HF_HOME=/root/.cache/huggingface")
    env_content = "\n".join(env_lines) + "\n"

    # 3. Build Python environment injection script (00_env.py)
    py_lines = ["# Auto-injected Colab Platform Environment Variables", "import os"]
    for k, v in secrets_dict.items():
        escaped_v = repr(v)
        py_lines.append(f"os.environ[{repr(k)}] = {escaped_v}")
    py_content = "\n".join(py_lines) + "\n"

    # 4. Build bootstrap bash script (.bootstrap.sh)
    packages = template.get("packages") or []
    startup_script = template.get("startup_script") or ""
    pkg_args = " ".join(shlex.quote(p) for p in packages)

    boot_lines = [
        "#!/bin/bash",
        "set -e",
        "source /content/.colab_env.sh 2>/dev/null || true",
        "# Sync uploaded storage configurations to root home",
        "mkdir -p /root/.config/rclone /root/.cache/huggingface",
        "[ -f /content/.config/rclone/rclone.conf ] && cp -f /content/.config/rclone/rclone.conf /root/.config/rclone/rclone.conf || true",
        "[ -f /content/.cache/huggingface/token ] && cp -f /content/.cache/huggingface/token /root/.cache/huggingface/token || true",
    ]
    if packages:
        boot_lines.extend([
            f"echo '[Bootstrap] Installing pip packages: {pkg_args}'",
            f"uv pip install --system {pkg_args} 2>/dev/null || pip install {pkg_args}",
        ])
    if startup_script.strip():
        boot_lines.extend([
            "echo '[Bootstrap] Executing custom startup script...'",
            startup_script,
        ])
    boot_lines.append("echo '[Bootstrap] Bootstrap finished successfully.'")
    boot_content = "\n".join(boot_lines) + "\n"

    log_dir = os.path.join(settings.DATA_DIR, "logs") if hasattr(settings, "DATA_DIR") else "./data/logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{session_id}_bootstrap.log")

    try:
        # Upload environment files via Colab Contents API
        env_b64 = base64.b64encode(env_content.encode("utf-8")).decode("ascii")
        colab.upload_file(
            proxy_url=proxy_url,
            proxy_token=proxy_token,
            path=".colab_env.sh",
            content_base64=env_b64,
        )

        py_b64 = base64.b64encode(py_content.encode("utf-8")).decode("ascii")
        colab.upload_file(
            proxy_url=proxy_url,
            proxy_token=proxy_token,
            path=".ipython_env.py",
            content_base64=py_b64,
        )

        boot_b64 = base64.b64encode(boot_content.encode("utf-8")).decode("ascii")
        colab.upload_file(
            proxy_url=proxy_url,
            proxy_token=proxy_token,
            path=".bootstrap.sh",
            content_base64=boot_b64,
        )

        # 5. Handle Storage Profile preflight verification if attached
        storage_ids = template.get("storage_profile_ids") or []
        for sp_id in storage_ids:
            from backend.app.services.storage_wizard import mount_profile_on_session
            mount_result = mount_profile_on_session(session_id, sp_id, client=colab)
            if not mount_result.get("success"):
                raise RuntimeError(f"Storage profile mount failed: {mount_result.get('error')}")

        # 6. Genuine Remote Execution via Session SSH Gateway
        from backend.app.services.ssh_multiplexer import ssh_multiplexer_service, ColabSSHGateway

        gateway = ssh_multiplexer_service.get_gateway(session_id)
        if not gateway:
            ssh_multiplexer_service._ensure_keys()
            with get_db() as conn:
                s_row = conn.execute(
                    "SELECT ssh_local_port FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                port = s_row["ssh_local_port"] if s_row else None
            if port:
                gateway = await ssh_multiplexer_service.register_session(session_id, port)
            else:
                gateway = ColabSSHGateway(
                    session_id=session_id,
                    port=0,
                    host_key=ssh_multiplexer_service._host_key,
                    client_key=ssh_multiplexer_service._client_key,
                )

        logger.info(f"Executing remote bootstrap script for session {session_id} via SSH gateway...")
        res = await gateway.run_command("bash /content/.bootstrap.sh", timeout=300.0)

        stdout_text = (
            res.stdout
            if isinstance(res.stdout, str)
            else (res.stdout.decode("utf-8", errors="replace") if res.stdout else "")
        )
        stderr_text = (
            res.stderr
            if isinstance(res.stderr, str)
            else (res.stderr.decode("utf-8", errors="replace") if res.stderr else "")
        )
        exit_code = (
            res.exit_status
            if res.exit_status is not None
            else (0 if getattr(res, "returncode", 0) == 0 else 1)
        )

        raw_log = (
            f"=== Bootstrap Execution Log for Session {session_id} (Template: {template['name']}) ===\n"
            f"Exit Code: {exit_code}\n\n"
            f"--- STDOUT ---\n{stdout_text}\n"
            f"--- STDERR ---\n{stderr_text}\n"
        )
        sanitized_log = sanitizer.sanitize_text(raw_log)
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(sanitized_log)

        if exit_code != 0:
            logger.error(f"Bootstrap failed for session {session_id} with exit code {exit_code}")
            with get_db() as conn:
                conn.execute("UPDATE sessions SET status = 'FAILED' WHERE id = ?", (session_id,))
            return False

        # Update session status to READY on genuine exit code 0
        with get_db() as conn:
            conn.execute("UPDATE sessions SET status = 'READY' WHERE id = ?", (session_id,))
        logger.info(f"Bootstrap completed successfully with exit code 0 for session {session_id}")
        return True

    except Exception as e:
        logger.error(f"Bootstrap execution failed for session {session_id}: {e}")
        err_msg = sanitizer.sanitize_text(f"Bootstrap execution exception: {e}\n")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(err_msg)

        with get_db() as conn:
            conn.execute("UPDATE sessions SET status = 'FAILED' WHERE id = ?", (session_id,))
        return False
