"""Cloud Storage Integrations & Configuration Wizard Service.

Supports:
1. Google Drive credential propagation (2-step XSRF handshake against /tun/m/credentials-propagation/{endpoint}).
2. Amazon S3 / Cloudflare R2 rclone configuration generation.
3. Hugging Face token authorization.
4. Preflight cloud mount path accessibility verification.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional

import requests

from backend.app.config import settings
from backend.app.core.database import get_db
from backend.app.core.models import (
    StorageProfileCreate,
    StorageProfileResponse,
    StorageProfileUpdate,
)
from backend.app.core.security import decrypt_secret, encrypt_secret
from backend.app.services import account_pool, session_manager
from backend.app.services.colab_client import ColabClient, ColabClientError, strip_xssi_prefix

logger = logging.getLogger("storage_wizard")


# ---------------------------------------------------------------------------
# 1. Storage Profile CRUD
# ---------------------------------------------------------------------------

def create_storage_profile(data: StorageProfileCreate) -> Dict[str, Any]:
    """Encrypt profile config and persist."""
    profile_id = f"sp_{uuid.uuid4().hex[:8]}"
    enc_config = encrypt_secret(json.dumps(data.config))

    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM storage_profiles WHERE name = ?", (data.name,)
        ).fetchone()
        if existing:
            raise ValueError(f"Storage profile with name '{data.name}' already exists")

        conn.execute(
            """
            INSERT INTO storage_profiles (id, name, type, config_enc, mount_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (profile_id, data.name, data.type, enc_config, data.mount_path or "/content/data"),
        )
        row = conn.execute(
            "SELECT id, name, type, mount_path FROM storage_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        return dict(row)


def list_storage_profiles() -> List[Dict[str, Any]]:
    """List storage profiles (sanitized, config decrypted without sensitive keys)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, type, mount_path FROM storage_profiles ORDER BY name ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_storage_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    """Get single storage profile metadata."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name, type, mount_path FROM storage_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        return dict(row) if row else None


def get_storage_profile_config(profile_id: str) -> Dict[str, Any]:
    """Internal: Decrypt and return plaintext profile config dictionary."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT config_enc FROM storage_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Storage profile {profile_id} not found")
        raw = decrypt_secret(row["config_enc"])
        return json.loads(raw)


def update_storage_profile(profile_id: str, data: StorageProfileUpdate) -> Dict[str, Any]:
    """Update profile configuration or mount path."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM storage_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Storage profile {profile_id} not found")

        updates = []
        params = []
        if data.name is not None:
            updates.append("name = ?")
            params.append(data.name)
        if data.mount_path is not None:
            updates.append("mount_path = ?")
            params.append(data.mount_path)
        if data.config is not None:
            updates.append("config_enc = ?")
            params.append(encrypt_secret(json.dumps(data.config)))

        if updates:
            params.append(profile_id)
            conn.execute(
                f"UPDATE storage_profiles SET {', '.join(updates)} WHERE id = ?",
                params,
            )

        updated = conn.execute(
            "SELECT id, name, type, mount_path FROM storage_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        return dict(updated)


def delete_storage_profile(profile_id: str) -> bool:
    """Delete storage profile and remove from referencing templates."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM storage_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if not row:
            return False

        conn.execute("DELETE FROM storage_profiles WHERE id = ?", (profile_id,))

        # Prune references in templates.storage_profile_ids_json
        templates = conn.execute(
            "SELECT id, storage_profile_ids_json FROM templates"
        ).fetchall()
        for tpl in templates:
            try:
                ids = json.loads(tpl["storage_profile_ids_json"] or "[]")
                if profile_id in ids:
                    ids = [x for x in ids if x != profile_id]
                    conn.execute(
                        "UPDATE templates SET storage_profile_ids_json = ? WHERE id = ?",
                        (json.dumps(ids), tpl["id"]),
                    )
            except Exception:
                pass

        return True


# ---------------------------------------------------------------------------
# 2. Google Drive Authorization Propagation Flow & VM FUSE Mount
# ---------------------------------------------------------------------------

def _get_gdrive_xsrf_token(
    colab: ColabClient,
    url: str,
    params: Dict[str, str],
    headers: Dict[str, str],
) -> str:
    """Probe GET /tun/m/credentials-propagation/{endpoint} to retrieve an XSRF token."""
    get_resp = colab.session.get(url, params=params, headers=headers, timeout=colab.timeout)
    if not get_resp.ok:
        raise ColabClientError(
            f"GDrive probe GET failed: HTTP {get_resp.status_code}",
            status_code=get_resp.status_code,
        )
    get_data = json.loads(strip_xssi_prefix(get_resp.text))
    xsrf_token = get_data.get("token")
    if not xsrf_token:
        raise ColabClientError("Missing token in GDrive probe GET response")
    return xsrf_token


def _probe_gdrive_consent_url(
    colab: ColabClient,
    url: str,
    base_params: Dict[str, str],
    headers: Dict[str, str],
    files: Dict[str, Any],
    xsrf_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Run dryrun=true probe to check existing consent or retrieve unauthorized_redirect_uri."""
    token = xsrf_token or _get_gdrive_xsrf_token(colab, url, base_params, headers)
    probe_params = {**base_params, "dryrun": "true"}
    post_headers = {**headers, "X-Goog-Colab-Token": token}
    resp = colab.session.post(
        url, params=probe_params, headers=post_headers, files=files, timeout=colab.timeout
    )
    if not resp.ok:
        raise ColabClientError(
            f"GDrive probe POST failed: HTTP {resp.status_code}",
            status_code=resp.status_code,
        )
    return json.loads(strip_xssi_prefix(resp.text))


def propagate_gdrive_credentials(
    session_id: str,
    dryrun: bool = False,
    client: Optional[ColabClient] = None,
) -> Dict[str, Any]:
    """Execute 2-step XSRF credentials propagation flow against Colab control plane.

    1. GET /tun/m/credentials-propagation/{endpoint} -> Receive XSRF token.
    2. For dryrun=true: POST with dryrun=true to check consent status or obtain redirect URI.
    3. For dryrun=false: POST directly with dryrun=false (since authtype=dfs_ephemeral with
       record=false keeps returning success=false on dryrun=true even after browser consent).
       If dryrun=false indicates authorization is still required, extract redirect URI and
       return AUTH_REQUIRED.
    """
    colab = client or ColabClient()
    sess = session_manager.get_session_internal(session_id)
    if not sess:
        raise ValueError(f"Session {session_id} not found")

    endpoint = sess["endpoint"]
    account_id = sess["account_id"]
    access_token = account_pool.get_valid_access_token(account_id)

    url = f"{colab.base_url}/tun/m/credentials-propagation/{endpoint}"
    base_params = {
        "authuser": "0",
        "authtype": "dfs_ephemeral",
        "version": "2",
        "dryrun": "true",
        "propagate": "true",
        "record": "false",
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "X-Colab-Client-Agent": "colab-cli",
    }
    files = {"file_id": (None, "empty.ipynb")}

    # Step 1: Probe GET to receive XSRF token
    xsrf_token = _get_gdrive_xsrf_token(colab, url, base_params, headers)
    post_headers = {**headers, "X-Goog-Colab-Token": xsrf_token}

    if dryrun:
        probe_data = _probe_gdrive_consent_url(
            colab, url, base_params, headers, files, xsrf_token=xsrf_token
        )
        if not probe_data.get("success"):
            return {
                "status": "AUTH_REQUIRED",
                "success": False,
                "redirect_url": probe_data.get("unauthorized_redirect_uri"),
            }
        return {"status": "AUTHORIZED", "success": True, "redirect_url": None}

    # Step 2 (dryrun=false): Attempt actual credential propagation directly
    final_params = {**base_params, "dryrun": "false"}
    post_resp = colab.session.post(
        url, params=final_params, headers=post_headers, files=files, timeout=colab.timeout
    )
    if post_resp.ok:
        post_data = json.loads(strip_xssi_prefix(post_resp.text))
        if post_data.get("success"):
            return {
                "status": "PROPAGATED",
                "success": True,
                "details": post_data,
            }
        redirect_uri = post_data.get("unauthorized_redirect_uri") or ""
        if not redirect_uri:
            try:
                probe_data = _probe_gdrive_consent_url(
                    colab, url, base_params, headers, files
                )
                redirect_uri = probe_data.get("unauthorized_redirect_uri") or ""
            except Exception:
                pass
        return {
            "status": "AUTH_REQUIRED",
            "success": False,
            "redirect_url": redirect_uri,
        }

    # Fallback probe if dryrun=false returned non-2xx before consent was granted
    try:
        probe_data = _probe_gdrive_consent_url(colab, url, base_params, headers, files)
        redirect_uri = probe_data.get("unauthorized_redirect_uri")
        if not probe_data.get("success") and redirect_uri:
            return {
                "status": "AUTH_REQUIRED",
                "success": False,
                "redirect_url": redirect_uri,
            }
    except Exception:
        pass

    raise ColabClientError(
        f"GDrive finalize POST failed: HTTP {post_resp.status_code}",
        status_code=post_resp.status_code,
    )


async def mount_gdrive_fuse_via_ssh(
    session_id: str,
    mount_path: str = "/content/drive",
) -> Dict[str, Any]:
    """Mount Google Drive FUSE inside the Colab VM using the active SSH multiplexer."""
    from backend.app.services.ssh_multiplexer import UpstreamSSHError, ssh_multiplexer_service

    gateway = ssh_multiplexer_service.get_gateway(session_id)
    if gateway is None:
        return {"mounted": False, "skipped": True}

    sess = session_manager.get_session_internal(session_id)
    proxy_url = (sess or {}).get("proxy_url", "")
    is_local_mock = proxy_url.startswith(("http://127.0.0.1", "http://localhost"))

    remote_script = (
        "import json, os, sys\n"
        f"mount_path = {json.dumps(mount_path)}\n"
        "if not os.path.exists('/var/colab/hostname'):\n"
        "    print(json.dumps({'mounted': False, 'skipped': True}))\n"
        "    sys.exit(0)\n"
        "mydrive_ok = (\n"
        "    os.path.isdir(os.path.join(mount_path, 'MyDrive'))\n"
        "    or os.path.isdir(os.path.join(mount_path, 'My Drive'))\n"
        "    or (os.path.ismount(mount_path) and os.path.isdir(mount_path) and bool(os.listdir(mount_path)))\n"
        ")\n"
        "if mydrive_ok:\n"
        "    print(json.dumps({'mounted': True, 'already_mounted': True}))\n"
        "    sys.exit(0)\n"
        "env1 = {}\n"
        "try:\n"
        "    raw = open('/proc/1/environ', 'rb').read().decode('utf-8', 'ignore').split('\\0')\n"
        "    for item in raw:\n"
        "        if '=' in item:\n"
        "            k, v = item.split('=', 1)\n"
        "            env1[k] = v\n"
        "except Exception:\n"
        "    pass\n"
        "ephem_addr = env1.get('TBE_EPHEM_CREDS_ADDR')\n"
        "if not ephem_addr:\n"
        "    raise RuntimeError('Missing TBE_EPHEM_CREDS_ADDR in Colab container environment')\n"
        "os.environ['HOME'] = env1.get('HOME', '/root')\n"
        "os.environ['CLOUDSDK_CONFIG'] = env1.get('CLOUDSDK_CONFIG', '/content/.config')\n"
        "os.environ['TBE_EPHEM_CREDS_ADDR'] = ephem_addr\n"
        "os.environ['TBE_CREDS_ADDR'] = ephem_addr\n"
        "from google.colab import drive\n"
        "drive._output.clear = lambda *a, **kw: None\n"
        "drive._mount(mount_path, force_remount=False, timeout_ms=80000, ephemeral=False)\n"
        "verified = (\n"
        "    os.path.isdir(os.path.join(mount_path, 'MyDrive'))\n"
        "    or os.path.isdir(os.path.join(mount_path, 'My Drive'))\n"
        "    or os.path.ismount(mount_path)\n"
        ")\n"
        "print(json.dumps({'mounted': bool(verified), 'already_mounted': False}))\n"
    )
    script_b64 = base64.b64encode(remote_script.encode("utf-8")).decode("ascii")
    cmd = f"python3 -c 'import base64; exec(base64.b64decode(\"{script_b64}\").decode(\"utf-8\"))'"

    try:
        proc = await gateway.run_command(cmd, timeout=95.0)
    except (UpstreamSSHError, OSError, asyncio.TimeoutError) as e:
        if is_local_mock:
            return {"mounted": False, "skipped": True}
        return {"mounted": False, "error": str(e)}

    stdout_text = (
        proc.stdout
        if isinstance(proc.stdout, str)
        else (proc.stdout.decode("utf-8", errors="replace") if proc.stdout else "")
    )
    stderr_text = (
        proc.stderr
        if isinstance(proc.stderr, str)
        else (proc.stderr.decode("utf-8", errors="replace") if proc.stderr else "")
    )

    if proc.exit_status != 0:
        err_detail = (stderr_text or stdout_text or f"DriveFS mount exited with status {proc.exit_status}").strip()
        return {"mounted": False, "error": err_detail}

    for line in reversed(stdout_text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                pass

    return {"mounted": False, "error": "Unexpected output from DriveFS mount helper"}


# ---------------------------------------------------------------------------
# 3. Storage Config Generators (rclone, Hugging Face)
# ---------------------------------------------------------------------------

def generate_rclone_config(profile_name: str, config: Dict[str, Any]) -> str:
    """Generate rclone.conf content for S3 or Cloudflare R2."""
    safe_name = profile_name.replace(" ", "_")
    provider = config.get("provider", "AWS")
    access_key = config.get("access_key_id") or config.get("access_key", "")
    secret_key = config.get("secret_access_key") or config.get("secret_key", "")
    endpoint = config.get("endpoint", "")
    region = config.get("region", "auto")

    lines = [
        f"[{safe_name}]",
        "type = s3",
        f"provider = {provider}",
        f"access_key_id = {access_key}",
        f"secret_access_key = {secret_key}",
    ]
    if endpoint:
        lines.append(f"endpoint = {endpoint}")
    if region:
        lines.append(f"region = {region}")
    lines.append("")
    return "\n".join(lines)


def generate_hf_token_config(config: Dict[str, Any]) -> str:
    """Extract and validate Hugging Face token string."""
    token = config.get("token") or config.get("hf_token", "")
    return str(token).strip()


# ---------------------------------------------------------------------------
# 4. Session Mount & Preflight Verification
# ---------------------------------------------------------------------------

def ensure_remote_directory(
    client: ColabClient,
    proxy_url: str,
    proxy_token: str,
    dir_path: str,
) -> None:
    """Ensure a directory exists on the Colab remote runtime via PUT /api/contents/{path}."""
    clean_path = dir_path.strip("/")
    if not clean_path or clean_path in (".", "/"):
        return

    # In Jupyter Contents API, create parent directories first if nested
    parts = clean_path.split("/")
    accum = ""
    for part in parts:
        accum = f"{accum}/{part}" if accum else part
        quoted = urllib.parse.quote(accum, safe="/")
        url = f"{proxy_url.rstrip('/')}/api/contents/{quoted}"
        try:
            client.session.put(
                url,
                params={"authuser": "0", "colab-runtime-proxy-token": proxy_token},
                json={"type": "directory"},
                headers={
                    "Accept": "application/json",
                    "X-Colab-Client-Agent": "colab-cli",
                    "X-Colab-Runtime-Proxy-Token": proxy_token,
                },
                timeout=client.timeout,
            )
        except Exception:
            pass


def verify_mount_accessibility(
    session_id: str,
    mount_path: str,
    client: Optional[ColabClient] = None,
) -> bool:
    """Preflight check: verifies remote directory is accessible via Contents API."""
    colab = client or ColabClient()
    sess = session_manager.get_session_internal(session_id)
    if not sess:
        return False

    proxy_url = sess["proxy_url"]
    proxy_token = sess["proxy_token"]

    # Convert /content/drive/MyDrive or /content/data to relative path
    rel_path = mount_path
    if rel_path.startswith("/content/"):
        rel_path = rel_path[len("/content/"):]
    rel_path = rel_path.strip("/")

    try:
        contents = colab.list_contents(proxy_url, proxy_token, path=rel_path)
        if contents and contents.get("type") == "directory":
            return True
        return False
    except Exception as e:
        logger.warning(f"Mount accessibility check failed for path '{mount_path}': {e}")
        return False


def mount_profile_on_session(
    session_id: str,
    profile_id: str,
    client: Optional[ColabClient] = None,
) -> Dict[str, Any]:
    """Pushes storage configuration (GDrive/rclone/HF) to session and validates accessibility."""
    colab = client or ColabClient()
    sess = session_manager.get_session_internal(session_id)
    if not sess:
        raise ValueError(f"Session {session_id} not found")

    profile = get_storage_profile(profile_id)
    if not profile:
        raise ValueError(f"Storage profile {profile_id} not found")

    prof_type = profile["type"]
    mount_path = profile["mount_path"]
    proxy_url = sess["proxy_url"]
    proxy_token = sess["proxy_token"]

    try:
        if prof_type == "gdrive":
            prop_res = propagate_gdrive_credentials(session_id, dryrun=False, client=colab)
            if not prop_res.get("success"):
                return {
                    "success": False,
                    "profile_id": profile_id,
                    "type": "gdrive",
                    "mount_path": mount_path,
                    "propagation": prop_res,
                    "error": "Google Drive authorization required",
                }
            # Ensure mount path directory exists in remote VFS
            rel = mount_path[len("/content/"):] if mount_path.startswith("/content/") else mount_path
            ensure_remote_directory(colab, proxy_url, proxy_token, rel)
            is_valid = verify_mount_accessibility(session_id, mount_path, client=colab)
            return {
                "success": True,
                "profile_id": profile_id,
                "type": "gdrive",
                "mount_path": mount_path,
                "propagation": prop_res,
                "mount_verified": is_valid,
            }

        elif prof_type == "s3":
            config = get_storage_profile_config(profile_id)
            rclone_conf = generate_rclone_config(profile["name"], config)
            b64_conf = base64.b64encode(rclone_conf.encode("utf-8")).decode("ascii")

            # Upload rclone.conf
            ensure_remote_directory(colab, proxy_url, proxy_token, ".config/rclone")
            colab.upload_file(
                proxy_url=proxy_url,
                proxy_token=proxy_token,
                path=".config/rclone/rclone.conf",
                content_base64=b64_conf,
            )

            # Ensure root home configs are populated if SSH connection is active
            try:
                from backend.app.services.ssh_multiplexer import ssh_multiplexer_service
                gw = ssh_multiplexer_service.get_gateway(session_id)
                if gw and gw.upstream_conn and not gw.upstream_conn.is_closed():
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(gw.run_command(
                            "mkdir -p /root/.config/rclone && cp -f /content/.config/rclone/rclone.conf /root/.config/rclone/rclone.conf",
                            timeout=10.0,
                        ))
                    except RuntimeError:
                        pass
            except Exception as e:
                logger.warning(f"Could not sync rclone config to /root: {e}")

            # Ensure mount target directory exists
            rel = mount_path[len("/content/"):] if mount_path.startswith("/content/") else mount_path
            ensure_remote_directory(colab, proxy_url, proxy_token, rel)
            is_valid = verify_mount_accessibility(session_id, mount_path, client=colab)
            return {
                "success": True,
                "profile_id": profile_id,
                "type": "s3",
                "mount_path": mount_path,
                "mount_verified": is_valid,
            }

        elif prof_type == "hf":
            config = get_storage_profile_config(profile_id)
            hf_token = generate_hf_token_config(config)
            b64_token = base64.b64encode(hf_token.encode("utf-8")).decode("ascii")

            # Upload token to ~/.cache/huggingface/token
            ensure_remote_directory(colab, proxy_url, proxy_token, ".cache/huggingface")
            colab.upload_file(
                proxy_url=proxy_url,
                proxy_token=proxy_token,
                path=".cache/huggingface/token",
                content_base64=b64_token,
            )

            # Ensure root home configs are populated if SSH connection is active
            try:
                from backend.app.services.ssh_multiplexer import ssh_multiplexer_service
                gw = ssh_multiplexer_service.get_gateway(session_id)
                if gw and gw.upstream_conn and not gw.upstream_conn.is_closed():
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(gw.run_command(
                            "mkdir -p /root/.cache/huggingface && cp -f /content/.cache/huggingface/token /root/.cache/huggingface/token",
                            timeout=10.0,
                        ))
                    except RuntimeError:
                        pass
            except Exception as e:
                logger.warning(f"Could not sync HF token to /root: {e}")

            # Ensure mount path directory exists
            rel = mount_path[len("/content/"):] if mount_path.startswith("/content/") else mount_path
            ensure_remote_directory(colab, proxy_url, proxy_token, rel)
            is_valid = verify_mount_accessibility(session_id, mount_path, client=colab)
            return {
                "success": True,
                "profile_id": profile_id,
                "type": "hf",
                "mount_path": mount_path,
                "mount_verified": is_valid,
            }

        else:
            raise ValueError(f"Unsupported storage profile type: {prof_type}")

    except Exception as e:
        logger.error(f"Failed to mount storage profile {profile_id} on session {session_id}: {e}")
        return {
            "success": False,
            "profile_id": profile_id,
            "error": str(e),
        }
