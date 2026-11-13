"""Google Multi-Account Pool & Lifecycle Management Service.

Handles remote headless OAuth2 authorization, AES-256-GCM token encryption,
token refresh, CCU quota polling, orphan instance discovery, and account auto-selection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests
from backend.app.config import settings
from backend.app.core.database import get_db
from backend.app.core.models import AccountSummary
from backend.app.core.security import decrypt_secret, encrypt_secret
from backend.app.services.colab_client import ColabClient

logger = logging.getLogger(__name__)

_PKCE_LOCK = threading.Lock()
_PKCE_TTL_SEC = 1800
# state -> (code_verifier, created_at)
_PENDING_PKCE_FLOWS: Dict[str, Tuple[str, float]] = {}


def _prune_expired_pkce_locked(now: float) -> None:
    expired = [
        st for st, (_, ts) in _PENDING_PKCE_FLOWS.items()
        if (now - ts) > _PKCE_TTL_SEC
    ]
    for st in expired:
        _PENDING_PKCE_FLOWS.pop(st, None)


def start_oauth_flow() -> Tuple[str, str]:
    """Generate remote Google OAuth consent URL with RFC 7636 PKCE (S256) and state."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(22)

    now = time.time()
    with _PKCE_LOCK:
        _prune_expired_pkce_locked(now)
        _PENDING_PKCE_FLOWS[state] = (code_verifier, now)

    params = {
        "response_type": "code",
        "client_id": settings.OAUTH_CLIENT_ID,
        "redirect_uri": settings.OAUTH_REDIRECT_URI,
        "scope": " ".join(settings.OAUTH_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "consent",
        "token_usage": "remote",
        "access_type": "offline",
    }
    return f"{settings.OAUTH_AUTH_URL}?{urllib.parse.urlencode(params)}", state


def get_oauth_url() -> str:
    """Generate remote Google OAuth consent URL for headless authorization."""
    auth_url, _ = start_oauth_flow()
    return auth_url


def exchange_code(code: str, state: Optional[str] = None) -> Dict[str, Any]:
    """Exchange authorization code for tokens, fetch user info, and persist account in DB."""
    raw_code = code.strip()
    if raw_code.startswith(("http://", "https://")) or ("code=" in raw_code and "&" in raw_code):
        query_str = urllib.parse.urlparse(raw_code).query if "://" in raw_code else raw_code.lstrip("?")
        parsed_qs = urllib.parse.parse_qs(query_str)
        if parsed_qs.get("code"):
            raw_code = parsed_qs["code"][0].strip()
        if not state and parsed_qs.get("state"):
            state = parsed_qs["state"][0].strip()

    matched_state: Optional[str] = None
    code_verifier: Optional[str] = None
    now = time.time()
    with _PKCE_LOCK:
        _prune_expired_pkce_locked(now)
        if state and state in _PENDING_PKCE_FLOWS:
            matched_state = state
            code_verifier = _PENDING_PKCE_FLOWS[state][0]
        elif not state and _PENDING_PKCE_FLOWS:
            matched_state = max(_PENDING_PKCE_FLOWS.items(), key=lambda kv: kv[1][1])[0]
            code_verifier = _PENDING_PKCE_FLOWS[matched_state][0]

    data = {
        "code": raw_code,
        "client_id": settings.OAUTH_CLIENT_ID,
        "client_secret": settings.OAUTH_CLIENT_SECRET,
        "redirect_uri": settings.OAUTH_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    if code_verifier:
        data["code_verifier"] = code_verifier

    resp = requests.post(settings.OAUTH_TOKEN_URL, data=data, timeout=15)
    if not resp.ok:
        raise ValueError(f"Failed to exchange authorization code: HTTP {resp.status_code} {resp.text}")

    if matched_state:
        with _PKCE_LOCK:
            _PENDING_PKCE_FLOWS.pop(matched_state, None)

    token_data = resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError("Missing access_token in Google OAuth response")

    # Fetch user email and profile
    headers = {"Authorization": f"Bearer {access_token}"}
    userinfo_resp = requests.get(settings.OAUTH_USERINFO_URL, headers=headers, timeout=15)
    if not userinfo_resp.ok:
        raise ValueError(f"Failed to fetch userinfo from Google: HTTP {userinfo_resp.status_code}")

    userinfo = userinfo_resp.json()
    email = userinfo.get("email")
    if not email:
        raise ValueError("Google account does not have an email address")

    display_name = userinfo.get("name", email.split("@")[0])

    # Calculate token expiry timestamp
    expires_in = token_data.get("expires_in", 3600)
    token_data["expires_at"] = int(time.time()) + expires_in

    # Encrypt credentials into AES-256-GCM Vault
    encrypted_creds = encrypt_secret(json.dumps(token_data))

    account_id = f"acc_{uuid.uuid4().hex[:12]}"

    with get_db() as conn:
        # Insert or update account record
        existing = conn.execute("SELECT id FROM accounts WHERE email = ?", (email,)).fetchone()
        if existing:
            account_id = existing["id"]
            conn.execute(
                """
                UPDATE accounts
                SET oauth_creds_enc = ?, display_name = ?, status = 'ACTIVE', last_synced_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (encrypted_creds, display_name, account_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO accounts (
                    id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                    active_assignments, warning_ccu, hard_stop_ccu, priority, status
                ) VALUES (?, ?, ?, ?, 0.0, 0.0, 0, ?, ?, 1, 'ACTIVE')
                """,
                (
                    account_id,
                    email,
                    display_name,
                    encrypted_creds,
                    settings.DEFAULT_WARNING_CCU,
                    settings.DEFAULT_HARD_STOP_CCU,
                ),
            )

    # Sync CCU quota immediately
    try:
        sync_account(account_id)
    except Exception as e:
        logger.warning(f"Initial CCU sync failed for {email}: {e}")

    return get_account(account_id)  # type: ignore


def get_valid_access_token(account_id: str) -> str:
    """Retrieve decrypted valid access token for an account, refreshing if expired."""
    with get_db() as conn:
        row = conn.execute("SELECT oauth_creds_enc FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise ValueError(f"Account {account_id} not found")

    creds_str = decrypt_secret(row["oauth_creds_enc"])
    creds = json.loads(creds_str)

    now = int(time.time())
    expires_at = creds.get("expires_at", 0)

    # If token expires within 5 minutes, refresh
    if now >= (expires_at - 300):
        refresh_token = creds.get("refresh_token")
        if not refresh_token:
            # Cannot refresh without refresh_token; return existing access_token
            return creds["access_token"]

        data = {
            "client_id": settings.OAUTH_CLIENT_ID,
            "client_secret": settings.OAUTH_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        resp = requests.post(settings.OAUTH_TOKEN_URL, data=data, timeout=15)
        if resp.ok:
            new_tokens = resp.json()
            creds["access_token"] = new_tokens["access_token"]
            expires_in = new_tokens.get("expires_in", 3600)
            creds["expires_at"] = now + expires_in
            if "refresh_token" in new_tokens:
                creds["refresh_token"] = new_tokens["refresh_token"]

            # Save updated credentials
            re_encrypted = encrypt_secret(json.dumps(creds))
            with get_db() as conn:
                conn.execute("UPDATE accounts SET oauth_creds_enc = ? WHERE id = ?", (re_encrypted, account_id))
        else:
            logger.error(f"Failed to refresh token for account {account_id}: {resp.status_code} {resp.text}")
            if resp.status_code == 400:
                with get_db() as conn:
                    conn.execute("UPDATE accounts SET status = 'AUTH_EXPIRED' WHERE id = ?", (account_id,))
                raise ValueError(f"OAuth refresh token expired or revoked for account {account_id}")

    return creds["access_token"]


def list_accounts() -> List[Dict[str, Any]]:
    """List all accounts without sensitive credential fields."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, email, display_name, ccu_balance, burn_rate_hourly,
                   active_assignments, warning_ccu, hard_stop_ccu, priority, status,
                   last_synced_at
            FROM accounts
            ORDER BY priority DESC, ccu_balance DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_account(account_id: str) -> Optional[Dict[str, Any]]:
    """Get single account summary."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, email, display_name, ccu_balance, burn_rate_hourly,
                   active_assignments, warning_ccu, hard_stop_ccu, priority, status,
                   last_synced_at
            FROM accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()
        return dict(row) if row else None


def sync_account(account_id: str, client: Optional[ColabClient] = None) -> Dict[str, Any]:
    """Poll CCU and active assignments for account, reconciling orphans and renewing tokens."""
    token = get_valid_access_token(account_id)
    colab = client or ColabClient()

    # Query CCU metrics
    ccu_info = colab.get_ccu_info(token)
    balance = ccu_info["current_balance"]
    burn_rate = ccu_info["consumption_rate_hourly"]

    # Query assignments from Google
    assignments = colab.list_assignments(token)
    active_count = len(assignments)

    with get_db() as conn:
        acc_row = conn.execute("SELECT hard_stop_ccu, status FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not acc_row:
            raise ValueError(f"Account {account_id} not found")

        status = acc_row["status"]
        if balance <= acc_row["hard_stop_ccu"]:
            status = "COOLDOWN"
        elif status == "COOLDOWN" and balance > acc_row["hard_stop_ccu"]:
            status = "ACTIVE"

        conn.execute(
            """
            UPDATE accounts
            SET ccu_balance = ?, burn_rate_hourly = ?, active_assignments = ?,
                status = ?, last_synced_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (balance, burn_rate, active_count, status, account_id),
        )

        # Reconcile running assignments with local session records
        local_sessions = conn.execute(
            "SELECT id, endpoint, status FROM sessions WHERE account_id = ? AND status IN ('READY', 'BUSY', 'PROVISIONING', 'BOOTSTRAPPING', 'ORPHAN')",
            (account_id,),
        ).fetchall()
        local_by_endpoint = {row["endpoint"]: row for row in local_sessions}

        for asgn in assignments:
            ep = asgn.get("endpoint")
            proxy_info = asgn.get("runtimeProxyInfo")
            if not ep or not proxy_info:
                continue

            proxy_token = proxy_info.get("token", "")
            proxy_url = proxy_info.get("url", "")
            expires_in = proxy_info.get("tokenExpiresInSeconds", 3600)
            token_expires_at = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + expires_in))

            if ep in local_by_endpoint:
                # Refresh local session token seamlessly
                conn.execute(
                    """
                    UPDATE sessions
                    SET proxy_url = ?, proxy_token_enc = ?, token_expires_at = ?
                    WHERE endpoint = ?
                    """,
                    (proxy_url, encrypt_secret(proxy_token), token_expires_at, ep),
                )
            else:
                # Discovered orphan instance! Record it
                orphan_id = f"orphan_{uuid.uuid4().hex[:8]}"
                acc_type = asgn.get("accelerator", "T4")
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator, shape,
                        lifecycle_policy, idle_timeout_sec, ssh_local_port, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'standard', 'interactive', 1800, NULL, 'ORPHAN')
                    """,
                    (
                        orphan_id,
                        f"orphan-{orphan_id}",
                        account_id,
                        f"orphan_{ep}",
                        ep,
                        proxy_url,
                        encrypt_secret(proxy_token),
                        token_expires_at,
                        acc_type,
                    ),
                )

    return get_account(account_id)  # type: ignore


def delete_account(account_id: str) -> bool:
    """Delete account and cascade delete its sessions."""
    with get_db() as conn:
        res = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        return res.rowcount > 0


def select_account(preferred_account_id: Optional[str] = None) -> str:
    """Auto-select the best eligible account based on priority, balance, and load.

    Score = (priority * 1000) + (ccu_balance * 10) - (active_assignments * 200).
    Filters: status == 'ACTIVE' and ccu_balance > hard_stop_ccu.
    """
    with get_db() as conn:
        if preferred_account_id:
            row = conn.execute(
                """
                SELECT id, ccu_balance, hard_stop_ccu, status
                FROM accounts WHERE id = ?
                """,
                (preferred_account_id,),
            ).fetchone()
            if row and row["status"] == "ACTIVE" and row["ccu_balance"] > row["hard_stop_ccu"]:
                return preferred_account_id
            if row:
                raise ValueError(
                    f"Account {preferred_account_id} is in status '{row['status']}' or below hard stop CCU ({row['ccu_balance']} <= {row['hard_stop_ccu']})"
                )
            raise ValueError(f"Specified account {preferred_account_id} does not exist")

        rows = conn.execute(
            """
            SELECT id, priority, ccu_balance, hard_stop_ccu, active_assignments, status
            FROM accounts
            WHERE status = 'ACTIVE' AND ccu_balance > hard_stop_ccu
            """
        ).fetchall()

        if not rows:
            raise ValueError("No eligible active accounts with available CCU quota in pool")

        # Score accounts
        scored = []
        for r in rows:
            score = (r["priority"] * 1000) + (r["ccu_balance"] * 10) - (r["active_assignments"] * 200)
            scored.append((score, r["id"]))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]
