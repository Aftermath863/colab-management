"""Session Lifecycle and Provisioning Management Service.

Coordinates Colab instance assignment, unassignment, SSH port pool allocation,
database persistence, and Colab direct web notebook deep-link generation.
"""

from __future__ import annotations

import logging
import socket
import time
import uuid
from typing import Any, Dict, List, Optional

from backend.app.config import settings
from backend.app.core.database import get_db
from backend.app.core.models import SessionCreateRequest, SessionSummary
from backend.app.core.security import decrypt_secret, encrypt_secret
from backend.app.services import account_pool
from backend.app.services.colab_client import (
    ColabClient,
    generate_colab_notebook_url,
    uuid_to_web_safe_base64,
)

logger = logging.getLogger(__name__)


def _is_port_available_on_os(port: int) -> bool:
    """Check if port can be bound on 0.0.0.0 without conflict."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            return True
    except OSError:
        return False


def allocate_ssh_port() -> Optional[int]:
    """Allocate the lowest available SSH multiplexer port from the configured pool (2220-2229)."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT ssh_local_port FROM sessions
            WHERE ssh_local_port IS NOT NULL
              AND status NOT IN ('STOPPED', 'FAILED', 'LOST')
            """
        ).fetchall()
        used_ports = {row["ssh_local_port"] for row in rows}

    for port in settings.ssh_port_pool:
        if port not in used_ports and _is_port_available_on_os(port):
            return port
    return None  # All ports in pool exhausted or occupied on OS


def create_session(
    req: SessionCreateRequest, client: Optional[ColabClient] = None
) -> Dict[str, Any]:
    """Provision a new Colab runtime session and persist to database."""
    colab = client or ColabClient()

    # 1. Resolve Account
    account_id = account_pool.select_account(req.account_id)
    access_token = account_pool.get_valid_access_token(account_id)

    # 2. Generate UUID and 44-character notebook hash
    session_uuid = uuid.uuid4()
    notebook_hash = uuid_to_web_safe_base64(session_uuid)

    # 3. Call Colab two-phase assign handshake
    assign_result = colab.assign(
        access_token=access_token,
        notebook_hash=notebook_hash,
        accelerator=req.accelerator,
        shape=req.shape,
    )

    endpoint = assign_result.get("endpoint")
    proxy_info = assign_result.get("runtimeProxyInfo", {})
    proxy_url = proxy_info.get("url", "")
    proxy_token = proxy_info.get("token", "")
    token_expires_in = proxy_info.get("tokenExpiresInSeconds", 3600)
    token_expires_at = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + token_expires_in)
    )

    if not endpoint or not proxy_url:
        raise ValueError("Colab control plane returned incomplete assignment payload")

    # 4. Allocate SSH Port and Encrypt Proxy Token
    ssh_port = allocate_ssh_port()
    encrypted_proxy_token = encrypt_secret(proxy_token)
    session_id = f"sess_{uuid.uuid4().hex[:12]}"
    initial_status = "BOOTSTRAPPING" if req.template_id else "READY"

    # 5. Insert Session and increment active_assignments
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                id, name, account_id, notebook_hash, endpoint, proxy_url,
                proxy_token_enc, token_expires_at, accelerator, shape,
                lifecycle_policy, idle_timeout_sec, ssh_local_port,
                workspace_subpath, remote_workdir, template_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                req.name,
                account_id,
                notebook_hash,
                endpoint,
                proxy_url,
                encrypted_proxy_token,
                token_expires_at,
                req.accelerator,
                req.shape,
                req.lifecycle_policy,
                req.idle_timeout_sec,
                ssh_port,
                req.workspace_subpath or "",
                req.remote_workdir or "/content",
                req.template_id,
                initial_status,
            ),
        )
        conn.execute(
            "UPDATE accounts SET active_assignments = active_assignments + 1 WHERE id = ?",
            (account_id,),
        )

    # 6. Register SSH multiplexer gateway if port allocated
    if ssh_port:
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            if loop.is_running():
                from backend.app.services.ssh_multiplexer import ssh_multiplexer_service
                loop.create_task(ssh_multiplexer_service.register_session(session_id, ssh_port))
        except (RuntimeError, Exception):
            pass

    # 7. Execute template bootstrap if requested
    if req.template_id:
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            if loop.is_running():
                from backend.app.services.template_service import execute_session_bootstrap
                loop.create_task(execute_session_bootstrap(session_id, req.template_id, client=colab))
        except (RuntimeError, Exception):
            pass

    # 8. Register session with lifecycle engine
    try:
        from backend.app.services.lifecycle_engine import lifecycle_engine
        lifecycle_engine.register_session(session_id)
    except Exception:
        pass

    return get_session(session_id)  # type: ignore


def stop_session(session_id: str, client: Optional[ColabClient] = None) -> bool:
    """Tear down a running session via Colab unassign and mark STOPPED in database."""
    colab = client or ColabClient()

    with get_db() as conn:
        sess = conn.execute(
            "SELECT id, account_id, endpoint, status FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not sess:
            raise ValueError(f"Session {session_id} not found")

        if sess["status"] in ("STOPPED", "FAILED", "LOST"):
            return True

        account_id = sess["account_id"]
        endpoint = sess["endpoint"]

    # Retrieve valid access token to call unassign
    try:
        access_token = account_pool.get_valid_access_token(account_id)
        colab.unassign(access_token, endpoint)
    except Exception as e:
        logger.warning(f"Colab unassign call failed for session {session_id}: {e}")

    # Update database record
    with get_db() as conn:
        conn.execute(
            """
            UPDATE sessions
            SET status = 'STOPPED', stopped_at = CURRENT_TIMESTAMP, ssh_local_port = NULL
            WHERE id = ?
            """,
            (session_id,),
        )
        conn.execute(
            """
            UPDATE accounts
            SET active_assignments = MAX(0, active_assignments - 1)
            WHERE id = ?
            """,
            (account_id,),
        )

    # Unregister SSH multiplexer gateway
    try:
        from backend.app.services.ssh_multiplexer import ssh_multiplexer_service
        gateway = ssh_multiplexer_service.unregister_session_sync(session_id)
        if gateway:
            try:
                import asyncio
                running_loop = asyncio.get_running_loop()
                if running_loop and running_loop.is_running():
                    running_loop.create_task(gateway.close())
            except RuntimeError:
                pass
    except Exception as e:
        logger.warning(f"Could not unregister SSH gateway for session {session_id}: {e}")

    try:
        from backend.app.services.lifecycle_engine import lifecycle_engine
        lifecycle_engine.unregister_session(session_id)
    except Exception:
        pass

    return True


def list_sessions(status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """List sessions joined with account email."""
    query = """
        SELECT s.id, s.name, s.account_id, a.email as account_email,
               s.notebook_hash, s.endpoint, s.proxy_url, s.accelerator,
               s.shape, s.lifecycle_policy, s.idle_timeout_sec,
               s.ssh_local_port, s.workspace_subpath, s.remote_workdir,
               s.template_id, s.status, s.created_at, s.stopped_at
        FROM sessions s
        LEFT JOIN accounts a ON s.account_id = a.id
    """
    params: List[Any] = []
    if status_filter:
        query += " WHERE s.status = ?"
        params.append(status_filter)
    query += " ORDER BY s.created_at DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if d.get("account_email") and d.get("endpoint"):
                d["colab_web_url"] = generate_colab_notebook_url(
                    d["account_email"], d["endpoint"]
                )
            result.append(d)
        return result


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get single session summary."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT s.id, s.name, s.account_id, a.email as account_email,
                   s.notebook_hash, s.endpoint, s.proxy_url, s.accelerator,
                   s.shape, s.lifecycle_policy, s.idle_timeout_sec,
                   s.ssh_local_port, s.workspace_subpath, s.remote_workdir,
                   s.template_id, s.status, s.created_at, s.stopped_at
            FROM sessions s
            LEFT JOIN accounts a ON s.account_id = a.id
            WHERE s.id = ?
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("account_email") and d.get("endpoint"):
            d["colab_web_url"] = generate_colab_notebook_url(
                d["account_email"], d["endpoint"]
            )
        return d


def get_session_internal(session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve raw internal session with decrypted proxy token for backend routing."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, name, account_id, endpoint, proxy_url, proxy_token_enc,
                   token_expires_at, status
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["proxy_token"] = decrypt_secret(d["proxy_token_enc"])
        return d


def get_session_colab_url(session_id: str) -> str:
    """Retrieve Colab web deep link URL for session."""
    sess = get_session(session_id)
    if not sess:
        raise ValueError(f"Session {session_id} not found")
    url = sess.get("colab_web_url")
    if not url:
        raise ValueError(f"Cannot generate Colab URL for session {session_id}")
    return url


def get_session_ssh_config(session_id: str) -> Dict[str, Any]:
    """Retrieve SSH connection parameters and VS Code Remote-SSH configuration snippet."""
    sess = get_session(session_id)
    if not sess:
        raise ValueError(f"Session {session_id} not found")

    port = sess.get("ssh_local_port")
    name = sess.get("name") or session_id
    cmd = f"ssh -p {port} root@localhost" if port else "SSH port pool exhausted"
    vscode_cfg = (
        f"Host colab-{name}\n"
        f"    HostName localhost\n"
        f"    Port {port}\n"
        f"    User root\n"
        f"    StrictHostKeyChecking no\n"
        f"    UserKnownHostsFile /dev/null\n"
    ) if port else ""

    return {
        "session_id": session_id,
        "host": "localhost",
        "port": port,
        "username": "root",
        "connection_command": cmd,
        "vscode_config": vscode_cfg,
    }
