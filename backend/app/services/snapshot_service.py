"""Colab Environment Snapshot & Restore Service.

Captures running Colab runtime environments (installed apt/pip packages, optional
full binary Python library overlay, and full /content workspace) into the user's
mounted Google Drive under `/content/drive/MyDrive/ColabSnapshots/<snapshot_id>/`
and restores them onto fresh Colab sessions.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from backend.app.core.database import get_db
from backend.app.core.models import (
    SnapshotActionResponse,
    SnapshotResponse,
    SnapshotRestoreRequest,
    SnapshotSaveRequest,
)
from backend.app.services import session_manager, storage_wizard
from backend.app.services.colab_client import ColabClient

logger = logging.getLogger("snapshot_service")

SNAPSHOT_DRIVE_DIR_NAME = "ColabSnapshots"
RESULT_MARKER = "__SNAPSHOT_RESULT__:"

# In-memory active/recent background task state keyed by session_id
_SESSION_SNAPSHOT_TASKS: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# 1. Database CRUD Helpers
# ---------------------------------------------------------------------------

def _row_to_snapshot(row: Any) -> Dict[str, Any]:
    d = dict(row)
    return {
        "id": d["id"],
        "name": d["name"],
        "description": d.get("description") or "",
        "account_id": d["account_id"],
        "account_email": d.get("account_email"),
        "source_session_id": d.get("source_session_id"),
        "capture_mode": d.get("capture_mode") or "hybrid",
        "drive_folder_path": d["drive_folder_path"],
        "python_version": d.get("python_version") or "",
        "accelerator": d.get("accelerator") or "T4",
        "shape": d.get("shape") or "standard",
        "apt_package_count": int(d.get("apt_package_count") or 0),
        "pip_package_count": int(d.get("pip_package_count") or 0),
        "workspace_size_bytes": int(d.get("workspace_size_bytes") or 0),
        "pylibs_size_bytes": int(d.get("pylibs_size_bytes") or 0),
        "status": d.get("status") or "CREATING",
        "error_message": d.get("error_message"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


def list_snapshots(account_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List environment snapshots, optionally filtered by Google account ID."""
    query = """
        SELECT sn.*, a.email AS account_email
        FROM snapshots sn
        LEFT JOIN accounts a ON sn.account_id = a.id
    """
    params: List[Any] = []
    if account_id:
        query += " WHERE sn.account_id = ?"
        params.append(account_id)
    query += " ORDER BY sn.updated_at DESC, sn.created_at DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_snapshot(r) for r in rows]


def get_snapshot(snapshot_id: str) -> Optional[Dict[str, Any]]:
    """Get a single snapshot metadata record by ID."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT sn.*, a.email AS account_email
            FROM snapshots sn
            LEFT JOIN accounts a ON sn.account_id = a.id
            WHERE sn.id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        return _row_to_snapshot(row) if row else None


def delete_snapshot(snapshot_id: str) -> bool:
    """Delete snapshot metadata record from database."""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# 2. Task State Management
# ---------------------------------------------------------------------------

def get_session_snapshot_task(session_id: str) -> Optional[Dict[str, Any]]:
    """Return latest snapshot save/restore task state for a session."""
    task = _SESSION_SNAPSHOT_TASKS.get(session_id)
    return dict(task) if task else None


def _update_task(session_id: str, **updates: Any) -> Dict[str, Any]:
    task = _SESSION_SNAPSHOT_TASKS.setdefault(session_id, {})
    task["session_id"] = session_id
    task.update(updates)
    task["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    return dict(task)


async def _get_ssh_gateway(session_id: str):
    """Acquire or initialize the SSH multiplexer gateway for a running session."""
    from backend.app.services.ssh_multiplexer import ColabSSHGateway, ssh_multiplexer_service

    gateway = ssh_multiplexer_service.get_gateway(session_id)
    if gateway:
        return gateway

    ssh_multiplexer_service._ensure_keys()
    with get_db() as conn:
        row = conn.execute(
            "SELECT ssh_local_port FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    port = row["ssh_local_port"] if row else None
    if port:
        return await ssh_multiplexer_service.register_session(session_id, port)

    return ColabSSHGateway(
        session_id=session_id,
        port=0,
        host_key=ssh_multiplexer_service._host_key,
        client_key=ssh_multiplexer_service._client_key,
    )


def _decode_stream(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode("utf-8", errors="replace")
    return "" if raw is None else str(raw)


def _parse_marker_json(stdout_text: str) -> Optional[Dict[str, Any]]:
    for line in reversed(stdout_text.splitlines()):
        stripped = line.strip()
        if stripped.startswith(RESULT_MARKER):
            payload_raw = stripped[len(RESULT_MARKER) :].strip()
            try:
                return json.loads(payload_raw)
            except json.JSONDecodeError:
                return None
    return None


# ---------------------------------------------------------------------------
# 3. Google Drive Readiness (Two-Step XSRF + FUSE Mount)
# ---------------------------------------------------------------------------

async def ensure_session_gdrive_ready(
    session_id: str,
    client: Optional[ColabClient] = None,
) -> Dict[str, Any]:
    """Ensure Colab Drive credentials are propagated and /content/drive is mounted."""
    prop = await asyncio.to_thread(
        storage_wizard.propagate_gdrive_credentials,
        session_id,
        False,
        client,
    )
    if prop.get("status") == "AUTH_REQUIRED" and not prop.get("success"):
        return {
            "ready": False,
            "action_status": "AUTH_REQUIRED",
            "redirect_url": prop.get("redirect_url"),
            "message": "Google Drive OAuth authorization required before snapshot transfer.",
        }

    mount_res = await storage_wizard.mount_gdrive_fuse_via_ssh(session_id, "/content/drive")
    if mount_res.get("error"):
        return {
            "ready": False,
            "action_status": "ERROR",
            "message": f"Google Drive FUSE mount failed: {mount_res['error']}",
        }

    return {"ready": True, "action_status": "MOUNTED", "mount": mount_res}


# ---------------------------------------------------------------------------
# 4. Remote Save / Restore / Sync Python Scripts (Executed via SSH)
# ---------------------------------------------------------------------------

def _build_remote_save_script(
    snapshot_id: str,
    account_id: str,
    name: str,
    description: str,
    capture_mode: str,
    accelerator: str,
    shape: str,
) -> str:
    cfg = {
        "snapshot_id": snapshot_id,
        "account_id": account_id,
        "name": name,
        "description": description,
        "capture_mode": capture_mode,
        "accelerator": accelerator,
        "shape": shape,
        "dir_name": SNAPSHOT_DRIVE_DIR_NAME,
        "marker": RESULT_MARKER,
    }
    cfg_json = json.dumps(cfg)

    return f"""
import json, os, shutil, site, subprocess, sys, time

CFG = json.loads({cfg_json!r})
CONTENT_DIR = os.environ.get("COLAB_SNAPSHOT_CONTENT_DIR", "/content")
IS_COLAB = os.path.exists("/var/colab/hostname")

def resolve_mydrive():
    if IS_COLAB:
        for cand in ("/content/drive/MyDrive", "/content/drive/My Drive"):
            if os.path.isdir(cand):
                return cand
        raise RuntimeError("Google Drive is not mounted under /content/drive")
    mock_root = os.path.join("/tmp/mock_colab_drive", CFG["account_id"], "MyDrive")
    os.makedirs(mock_root, exist_ok=True)
    return mock_root

mydrive = resolve_mydrive()
snap_dir = os.path.join(mydrive, CFG["dir_name"], CFG["snapshot_id"])
os.makedirs(snap_dir, exist_ok=True)

use_zstd = shutil.which("zstd") is not None
ext = "tar.zst" if use_zstd else "tar.gz"

# 1. Capture apt manual packages
apt_pkgs = []
if shutil.which("apt-mark"):
    p = subprocess.run(["apt-mark", "showmanual"], capture_output=True, text=True)
    if p.returncode == 0:
        apt_pkgs = sorted({{ln.strip() for ln in p.stdout.splitlines() if ln.strip()}})

apt_tmp = os.path.join(snap_dir, "apt-packages.txt.tmp")
with open(apt_tmp, "w", encoding="utf-8") as f:
    f.write("\\n".join(apt_pkgs) + ("\\n" if apt_pkgs else ""))

# 2. Capture pip freeze manifest
pip_lines = []
p_pip = subprocess.run([sys.executable, "-m", "pip", "list", "--format=freeze"], capture_output=True, text=True)
if p_pip.returncode == 0:
    pip_lines = [ln.strip() for ln in p_pip.stdout.splitlines() if ln.strip() and not ln.strip().startswith("-e ")]

pip_tmp = os.path.join(snap_dir, "pip-requirements.txt.tmp")
with open(pip_tmp, "w", encoding="utf-8") as f:
    f.write("\\n".join(pip_lines) + ("\\n" if pip_lines else ""))

# 3. Optional full_binary delta Python overlay (site-packages modified/added after boot + /usr/local/bin)
pylibs_archive = None
pylibs_tmp = None
pylibs_size = 0
if CFG["capture_mode"] == "full_binary":
    boot_env = os.environ.get("COLAB_SNAPSHOT_BOOT_CTIME")
    if boot_env is not None:
        boot_ctime = float(boot_env)
    elif os.path.exists("/var/colab/hostname"):
        boot_ctime = os.stat("/var/colab/hostname").st_ctime
    elif os.path.exists("/proc/1"):
        boot_ctime = os.stat("/proc/1").st_ctime
    else:
        boot_ctime = time.time() - 3600.0

    rel_paths = []
    search_roots = []
    sp_override = os.environ.get("COLAB_SNAPSHOT_SITE_PACKAGES")
    if sp_override:
        if os.path.isdir(sp_override):
            search_roots.append(sp_override)
    else:
        for sp in site.getsitepackages():
            if os.path.isdir(sp):
                search_roots.append(sp)
        usp = site.getusersitepackages()
        if usp and os.path.isdir(usp) and usp not in search_roots:
            search_roots.append(usp)

    for sp_root in search_roots:
        try:
            entries = os.listdir(sp_root)
        except OSError:
            continue
        for entry in entries:
            if not entry.endswith(".dist-info"):
                continue
            dist_path = os.path.join(sp_root, entry)
            try:
                if os.stat(dist_path).st_mtime < boot_ctime:
                    continue
            except OSError:
                continue
            rec_file = os.path.join(dist_path, "RECORD")
            if os.path.isfile(rec_file):
                with open(rec_file, "r", encoding="utf-8", errors="ignore") as rf:
                    for line in rf:
                        rel_rec = line.split(",")[0].strip()
                        if not rel_rec or ".." in rel_rec:
                            continue
                        full_p = os.path.normpath(os.path.join(sp_root, rel_rec))
                        if os.path.exists(full_p):
                            rel_paths.append(full_p.lstrip("/"))
            rel_paths.append(dist_path.lstrip("/"))

    local_bin = os.environ.get("COLAB_SNAPSHOT_LOCAL_BIN", "/usr/local/bin")
    if os.path.isdir(local_bin):
        try:
            for bname in os.listdir(local_bin):
                bpath = os.path.join(local_bin, bname)
                try:
                    if os.stat(bpath).st_mtime >= boot_ctime:
                        rel_paths.append(bpath.lstrip("/"))
                except OSError:
                    continue
        except OSError:
            pass

    seen = sorted(set(rel_paths))
    pylibs_archive = f"pylibs.{{ext}}"
    pylibs_tmp = os.path.join(snap_dir, f"{{pylibs_archive}}.tmp")
    list_file = os.path.join(snap_dir, ".pylibs_files.tmp")
    with open(list_file, "w", encoding="utf-8") as lf:
        for item in seen:
            lf.write(item + "\\n")

    tar_cmd = ["tar"]
    if use_zstd:
        tar_cmd.append("--zstd")
    else:
        tar_cmd.append("-z")
    tar_cmd.extend(["-cf", pylibs_tmp, "-C", "/", "-T", list_file])
    p_py = subprocess.run(tar_cmd, capture_output=True, text=True)
    try:
        os.remove(list_file)
    except OSError:
        pass
    if p_py.returncode not in (0, 1):
        raise RuntimeError(f"Failed archiving Python libraries: {{p_py.stderr.strip()}}")
    pylibs_size = os.path.getsize(pylibs_tmp) if os.path.exists(pylibs_tmp) else 0

# 4. Archive /content workspace (excluding Drive, sample_data, injected runtime files)
os.makedirs(CONTENT_DIR, exist_ok=True)
workspace_archive = f"workspace.{{ext}}"
ws_tmp = os.path.join(snap_dir, f"{{workspace_archive}}.tmp")

ws_cmd = ["tar"]
if use_zstd:
    ws_cmd.append("--zstd")
else:
    ws_cmd.append("-z")
ws_cmd.extend([
    "-cf", ws_tmp,
    "--exclude=./drive",
    "--exclude=./sample_data",
    "--exclude=./.config",
    "--exclude=./.colab_env.sh",
    "--exclude=./.ipython_env.py",
    "--exclude=./.bootstrap.sh",
    "-C", CONTENT_DIR,
    ".",
])
p_ws = subprocess.run(ws_cmd, capture_output=True, text=True)
if p_ws.returncode not in (0, 1):
    raise RuntimeError(f"Failed archiving workspace: {{p_ws.stderr.strip()}}")
ws_size = os.path.getsize(ws_tmp) if os.path.exists(ws_tmp) else 0

# 5. Atomic swap into final filenames (safe overwrite)
os.replace(apt_tmp, os.path.join(snap_dir, "apt-packages.txt"))
os.replace(pip_tmp, os.path.join(snap_dir, "pip-requirements.txt"))
os.replace(ws_tmp, os.path.join(snap_dir, workspace_archive))
for alt in ("workspace.tar.zst", "workspace.tar.gz"):
    if alt != workspace_archive:
        alt_p = os.path.join(snap_dir, alt)
        if os.path.exists(alt_p):
            os.remove(alt_p)

if pylibs_tmp and pylibs_archive:
    os.replace(pylibs_tmp, os.path.join(snap_dir, pylibs_archive))
for alt in ("pylibs.tar.zst", "pylibs.tar.gz"):
    if alt != pylibs_archive:
        alt_p = os.path.join(snap_dir, alt)
        if os.path.exists(alt_p):
            os.remove(alt_p)

now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
py_ver = f"{{sys.version_info.major}}.{{sys.version_info.minor}}.{{sys.version_info.micro}}"
py_mm = f"{{sys.version_info.major}}.{{sys.version_info.minor}}"

manifest_path = os.path.join(snap_dir, "manifest.json")
prev_created = now_iso
if os.path.isfile(manifest_path):
    try:
        prev_created = json.load(open(manifest_path, "r", encoding="utf-8")).get("created_at") or now_iso
    except Exception:
        pass

manifest = {{
    "snapshot_id": CFG["snapshot_id"],
    "name": CFG["name"],
    "description": CFG["description"],
    "account_id": CFG["account_id"],
    "capture_mode": CFG["capture_mode"],
    "drive_folder_path": f"/content/drive/MyDrive/{{CFG['dir_name']}}/{{CFG['snapshot_id']}}",
    "python_version": py_ver,
    "python_major_minor": py_mm,
    "accelerator": CFG["accelerator"],
    "shape": CFG["shape"],
    "apt_package_count": len(apt_pkgs),
    "pip_package_count": len(pip_lines),
    "workspace_archive": workspace_archive,
    "workspace_size_bytes": ws_size,
    "pylibs_archive": pylibs_archive,
    "pylibs_size_bytes": pylibs_size,
    "created_at": prev_created,
    "updated_at": now_iso,
}}
manifest_tmp = manifest_path + ".tmp"
with open(manifest_tmp, "w", encoding="utf-8") as mf:
    json.dump(manifest, mf, indent=2)
os.replace(manifest_tmp, manifest_path)

print(CFG["marker"] + json.dumps(manifest))
"""


def _build_remote_restore_script(snapshot_id: str, account_id: str) -> str:
    cfg = {
        "snapshot_id": snapshot_id,
        "account_id": account_id,
        "dir_name": SNAPSHOT_DRIVE_DIR_NAME,
        "marker": RESULT_MARKER,
    }
    cfg_json = json.dumps(cfg)

    return f"""
import json, os, shutil, subprocess, sys, time

CFG = json.loads({cfg_json!r})
CONTENT_DIR = os.environ.get("COLAB_SNAPSHOT_CONTENT_DIR", "/content")
IS_COLAB = os.path.exists("/var/colab/hostname")
STATE_PATH = os.environ.get("COLAB_SNAPSHOT_STATE_PATH", "/root/.colab_snapshot_state.json")

def resolve_mydrive():
    if IS_COLAB:
        for cand in ("/content/drive/MyDrive", "/content/drive/My Drive"):
            if os.path.isdir(cand):
                return cand
        raise RuntimeError("Google Drive is not mounted under /content/drive")
    mock_root = os.path.join("/tmp/mock_colab_drive", CFG["account_id"], "MyDrive")
    if os.path.isdir(mock_root):
        return mock_root
    raise RuntimeError(f"Drive root not found at {{mock_root}}")

mydrive = resolve_mydrive()
snap_dir = os.path.join(mydrive, CFG["dir_name"], CFG["snapshot_id"])
manifest_path = os.path.join(snap_dir, "manifest.json")
if not os.path.isfile(manifest_path):
    raise RuntimeError(f"Snapshot manifest not found in Drive: {{manifest_path}}")

with open(manifest_path, "r", encoding="utf-8") as mf:
    manifest = json.load(mf)

notes = []

# 1. Restore missing apt packages
apt_file = os.path.join(snap_dir, "apt-packages.txt")
apt_installed = 0
if os.path.isfile(apt_file) and shutil.which("apt-get") and shutil.which("dpkg-query"):
    with open(apt_file, "r", encoding="utf-8") as af:
        desired_apt = [ln.strip() for ln in af if ln.strip()]
    p_dq = subprocess.run(["dpkg-query", "-W", "-f=${{Package}}\\n"], capture_output=True, text=True)
    present_apt = set(p_dq.stdout.splitlines()) if p_dq.returncode == 0 else set()
    missing_apt = [pkg for pkg in desired_apt if pkg not in present_apt]
    if missing_apt:
        subprocess.run(["apt-get", "update", "-qq"], capture_output=True, text=True)
        p_inst = subprocess.run(
            ["apt-get", "install", "-y", "--no-install-recommends", *missing_apt],
            capture_output=True,
            text=True,
        )
        if p_inst.returncode == 0:
            apt_installed = len(missing_apt)
        else:
            for pkg in missing_apt:
                p_one = subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", pkg], capture_output=True, text=True)
                if p_one.returncode == 0:
                    apt_installed += 1
            notes.append(f"Partial apt restore ({{apt_installed}}/{{len(missing_apt)}})")

# 2. Restore Python libraries (binary overlay if matching Python version, else delta pip install)
py_mm = f"{{sys.version_info.major}}.{{sys.version_info.minor}}"
binary_restored = False
pip_installed = 0
pylibs_name = manifest.get("pylibs_archive")
if (
    manifest.get("capture_mode") == "full_binary"
    and pylibs_name
    and manifest.get("python_major_minor") == py_mm
    and os.path.isfile(os.path.join(snap_dir, pylibs_name))
):
    pylibs_path = os.path.join(snap_dir, pylibs_name)
    extract_target = os.environ.get("COLAB_SNAPSHOT_PYLIBS_TARGET", "/")
    cmd = ["tar", "--no-same-owner"]
    if pylibs_name.endswith(".zst") and shutil.which("zstd"):
        cmd.append("--zstd")
    elif pylibs_name.endswith(".gz"):
        cmd.append("-z")
    cmd.extend(["-xf", pylibs_path, "-C", extract_target])
    p_ext = subprocess.run(cmd, capture_output=True, text=True)
    if p_ext.returncode == 0:
        binary_restored = True
    else:
        notes.append(f"Binary pylibs extract failed ({{p_ext.stderr.strip()}}), falling back to pip")

pip_file = os.path.join(snap_dir, "pip-requirements.txt")
if not binary_restored and os.path.isfile(pip_file):
    with open(pip_file, "r", encoding="utf-8") as pf:
        desired_specs = [ln.strip() for ln in pf if ln.strip() and not ln.strip().startswith("#")]
    p_cur = subprocess.run([sys.executable, "-m", "pip", "list", "--format=json"], capture_output=True, text=True)
    current_pkgs = {{}}
    if p_cur.returncode == 0:
        try:
            for item in json.loads(p_cur.stdout):
                current_pkgs[item["name"].lower().replace("_", "-")] = item["version"]
        except Exception:
            pass

    delta_specs = []
    for spec in desired_specs:
        if "==" in spec:
            pkg_name, ver = spec.split("==", 1)
            norm = pkg_name.strip().lower().replace("_", "-")
            if norm.startswith("google-colab") or norm in ("colab", "pydrive", "pydrive2"):
                continue
            if current_pkgs.get(norm) == ver.strip():
                continue
        delta_specs.append(spec)

    if delta_specs:
        installer = [shutil.which("uv"), "pip", "install", "--system", "--quiet"] if shutil.which("uv") else [sys.executable, "-m", "pip", "install", "--quiet"]
        p_pi = subprocess.run([*installer, *delta_specs], capture_output=True, text=True)
        if p_pi.returncode == 0:
            pip_installed = len(delta_specs)
        else:
            for spec in delta_specs:
                p_single = subprocess.run([*installer, spec], capture_output=True, text=True)
                if p_single.returncode == 0:
                    pip_installed += 1
            notes.append(f"Delta pip restore installed {{pip_installed}}/{{len(delta_specs)}} packages")

# 3. Extract /content workspace archive
ws_name = manifest.get("workspace_archive")
if not ws_name or not os.path.isfile(os.path.join(snap_dir, ws_name)):
    for cand in ("workspace.tar.zst", "workspace.tar.gz"):
        if os.path.isfile(os.path.join(snap_dir, cand)):
            ws_name = cand
            break

if not ws_name or not os.path.isfile(os.path.join(snap_dir, ws_name)):
    raise RuntimeError(f"Workspace archive missing in {{snap_dir}}")

os.makedirs(CONTENT_DIR, exist_ok=True)
ws_path = os.path.join(snap_dir, ws_name)
ws_cmd = ["tar", "--no-same-owner"]
if ws_name.endswith(".zst") and shutil.which("zstd"):
    ws_cmd.append("--zstd")
elif ws_name.endswith(".gz"):
    ws_cmd.append("-z")
ws_cmd.extend([
    "--exclude=./drive",
    "--exclude=./sample_data",
    "--exclude=./.config",
    "-xf", ws_path,
    "-C", CONTENT_DIR,
])
p_ws = subprocess.run(ws_cmd, capture_output=True, text=True)
if p_ws.returncode not in (0, 1):
    raise RuntimeError(f"Workspace extract failed: {{p_ws.stderr.strip()}}")

# 4. Persist snapshot lineage metadata on VM
os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
with open(STATE_PATH, "w", encoding="utf-8") as sf:
    json.dump({{
        "snapshot_id": CFG["snapshot_id"],
        "restored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "capture_mode": manifest.get("capture_mode"),
        "apt_installed": apt_installed,
        "pip_installed": pip_installed,
        "binary_restored": binary_restored,
    }}, sf, indent=2)

print(CFG["marker"] + json.dumps({{
    "snapshot_id": CFG["snapshot_id"],
    "apt_installed": apt_installed,
    "pip_installed": pip_installed,
    "binary_restored": binary_restored,
    "notes": notes,
}}))
"""


def _build_remote_sync_script(account_id: str) -> str:
    cfg = json.dumps({"account_id": account_id, "dir_name": SNAPSHOT_DRIVE_DIR_NAME, "marker": RESULT_MARKER})
    return f"""
import glob, json, os

CFG = json.loads({cfg!r})
IS_COLAB = os.path.exists("/var/colab/hostname")

def resolve_mydrive():
    if IS_COLAB:
        for cand in ("/content/drive/MyDrive", "/content/drive/My Drive"):
            if os.path.isdir(cand):
                return cand
        raise RuntimeError("Google Drive is not mounted under /content/drive")
    mock_root = os.path.join("/tmp/mock_colab_drive", CFG["account_id"], "MyDrive")
    os.makedirs(mock_root, exist_ok=True)
    return mock_root

mydrive = resolve_mydrive()
root_dir = os.path.join(mydrive, CFG["dir_name"])
manifests = []
if os.path.isdir(root_dir):
    for mpath in sorted(glob.glob(os.path.join(root_dir, "*", "manifest.json"))):
        try:
            with open(mpath, "r", encoding="utf-8") as mf:
                manifests.append(json.load(mf))
        except Exception:
            pass

print(CFG["marker"] + json.dumps({{"manifests": manifests}}))
"""


async def _run_remote_python(session_id: str, script: str, timeout: float = 1800.0) -> Dict[str, Any]:
    gateway = await _get_ssh_gateway(session_id)
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    cmd = f"python3 -c 'import base64; exec(base64.b64decode(\"{b64}\").decode(\"utf-8\"))'"
    res = await gateway.run_command(cmd, timeout=timeout)

    stdout_text = _decode_stream(res.stdout)
    stderr_text = _decode_stream(res.stderr)
    exit_code = (
        res.exit_status
        if res.exit_status is not None
        else (0 if getattr(res, "returncode", 0) == 0 else 1)
    )

    if exit_code != 0:
        err = (stderr_text or stdout_text or f"Remote snapshot script failed with exit code {exit_code}").strip()
        raise RuntimeError(err)

    payload = _parse_marker_json(stdout_text)
    if payload is None:
        raise RuntimeError(f"Unexpected snapshot helper output: {stdout_text[-500:]}")
    return payload


# ---------------------------------------------------------------------------
# 5. Save / Restore / Sync Workflows
# ---------------------------------------------------------------------------

async def start_save_snapshot(
    session_id: str,
    req: SnapshotSaveRequest,
    client: Optional[ColabClient] = None,
    wait_for_completion: bool = False,
) -> SnapshotActionResponse:
    """Preflight Drive auth & mount, create/prepare DB record, and start save task."""
    sess = session_manager.get_session_internal(session_id)
    if not sess:
        raise ValueError(f"Session {session_id} not found")
    if sess.get("status") not in ("READY", "BUSY"):
        raise ValueError(f"Session {session_id} is not active (status: {sess.get('status')})")

    drive_state = await ensure_session_gdrive_ready(session_id, client=client)
    if not drive_state.get("ready"):
        if drive_state.get("action_status") == "AUTH_REQUIRED":
            return SnapshotActionResponse(
                action_status="AUTH_REQUIRED",
                redirect_url=drive_state.get("redirect_url"),
                message=drive_state.get("message"),
            )
        raise RuntimeError(drive_state.get("message") or "Google Drive not ready")

    existing_task = _SESSION_SNAPSHOT_TASKS.get(session_id)
    if existing_task and existing_task.get("status") == "RUNNING":
        raise ValueError("A snapshot operation is already running for this session")

    account_id = sess["account_id"]
    is_overwrite = bool(req.overwrite_snapshot_id)
    if is_overwrite:
        target = get_snapshot(req.overwrite_snapshot_id or "")
        if not target:
            raise ValueError(f"Snapshot {req.overwrite_snapshot_id} not found")
        if target["account_id"] != account_id:
            raise ValueError("Cannot overwrite a snapshot owned by another Google account")
        if target["status"] != "READY":
            raise ValueError("Only READY snapshots can be safely overwritten")
        snapshot_id = target["id"]
        with get_db() as conn:
            conn.execute(
                """
                UPDATE snapshots
                SET error_message = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (snapshot_id,),
            )
    else:
        snapshot_id = f"snap_{uuid.uuid4().hex[:10]}"
        drive_folder_path = f"/content/drive/MyDrive/{SNAPSHOT_DRIVE_DIR_NAME}/{snapshot_id}"
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO snapshots (
                    id, name, description, account_id, source_session_id,
                    capture_mode, drive_folder_path, accelerator, shape, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CREATING')
                """,
                (
                    snapshot_id,
                    req.name.strip() or f"Snapshot {snapshot_id}",
                    req.description,
                    account_id,
                    session_id,
                    req.capture_mode,
                    drive_folder_path,
                    sess.get("accelerator") or "T4",
                    sess.get("shape") or "standard",
                ),
            )

    task_id = f"task_{uuid.uuid4().hex[:10]}"
    task = _update_task(
        session_id,
        task_id=task_id,
        snapshot_id=snapshot_id,
        action="save",
        status="RUNNING",
        step="archiving_environment",
        progress_pct=15,
        message="Capturing packages and archiving /content to Google Drive...",
        error_message=None,
        stop_after_save=req.stop_after_save,
        capture_mode=req.capture_mode,
        started_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        finished_at=None,
    )

    coro = _execute_save_snapshot(
        session_id=session_id,
        snapshot_id=snapshot_id,
        req=req,
        is_overwrite=is_overwrite,
        client=client,
    )
    if wait_for_completion:
        await coro
    else:
        asyncio.create_task(coro)

    snap_dict = get_snapshot(snapshot_id)
    return SnapshotActionResponse(
        action_status="STARTED",
        snapshot=SnapshotResponse(**snap_dict) if snap_dict else None,
        task=get_session_snapshot_task(session_id) or task,
        message="Snapshot save started",
    )


async def _execute_save_snapshot(
    session_id: str,
    snapshot_id: str,
    req: SnapshotSaveRequest,
    is_overwrite: bool,
    client: Optional[ColabClient] = None,
) -> None:
    """Execute remote tar + manifest save over SSH and optionally stop session."""
    try:
        sess = session_manager.get_session_internal(session_id)
        if not sess:
            raise RuntimeError(f"Session {session_id} disappeared")

        _update_task(
            session_id,
            step="archiving_workspace",
            progress_pct=45,
            message="Compressing installed packages and /content into Google Drive...",
        )

        script = _build_remote_save_script(
            snapshot_id=snapshot_id,
            account_id=sess["account_id"],
            name=req.name.strip() or f"Snapshot {snapshot_id}",
            description=req.description,
            capture_mode=req.capture_mode,
            accelerator=sess.get("accelerator") or "T4",
            shape=sess.get("shape") or "standard",
        )
        manifest = await _run_remote_python(session_id, script, timeout=1800.0)

        with get_db() as conn:
            conn.execute(
                """
                UPDATE snapshots
                SET name = ?, description = ?, source_session_id = ?, capture_mode = ?,
                    drive_folder_path = ?, python_version = ?, accelerator = ?, shape = ?,
                    apt_package_count = ?, pip_package_count = ?,
                    workspace_size_bytes = ?, pylibs_size_bytes = ?,
                    status = 'READY', error_message = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    manifest.get("name") or req.name,
                    manifest.get("description") or req.description,
                    session_id,
                    manifest.get("capture_mode") or req.capture_mode,
                    manifest.get("drive_folder_path") or f"/content/drive/MyDrive/{SNAPSHOT_DRIVE_DIR_NAME}/{snapshot_id}",
                    manifest.get("python_version") or "",
                    manifest.get("accelerator") or (sess.get("accelerator") or "T4"),
                    manifest.get("shape") or (sess.get("shape") or "standard"),
                    int(manifest.get("apt_package_count") or 0),
                    int(manifest.get("pip_package_count") or 0),
                    int(manifest.get("workspace_size_bytes") or 0),
                    int(manifest.get("pylibs_size_bytes") or 0),
                    snapshot_id,
                ),
            )

        if req.stop_after_save:
            _update_task(
                session_id,
                step="stopping_session",
                progress_pct=90,
                message="Snapshot saved cleanly; stopping Colab instance...",
            )
            await asyncio.to_thread(session_manager.stop_session, session_id, client)

        _update_task(
            session_id,
            status="COMPLETED",
            step="done",
            progress_pct=100,
            message="Snapshot saved and verified in Google Drive.",
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        )
    except Exception as exc:
        logger.error("Snapshot save failed for session %s (%s): %s", session_id, snapshot_id, exc)
        err_msg = str(exc)
        with get_db() as conn:
            if is_overwrite:
                # Preserve prior READY archive integrity on overwrite failure
                conn.execute(
                    "UPDATE snapshots SET error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (err_msg, snapshot_id),
                )
            else:
                conn.execute(
                    "UPDATE snapshots SET status = 'FAILED', error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (err_msg, snapshot_id),
                )
        _update_task(
            session_id,
            status="FAILED",
            step="error",
            message=err_msg,
            error_message=err_msg,
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        )


async def start_restore_snapshot(
    session_id: str,
    req: SnapshotRestoreRequest,
    client: Optional[ColabClient] = None,
    wait_for_completion: bool = False,
) -> SnapshotActionResponse:
    """Preflight Drive auth & mount and restore snapshot onto a running session."""
    sess = session_manager.get_session_internal(session_id)
    if not sess:
        raise ValueError(f"Session {session_id} not found")
    if sess.get("status") not in ("READY", "BUSY"):
        raise ValueError(f"Session {session_id} is not active (status: {sess.get('status')})")

    snap = get_snapshot(req.snapshot_id)
    if not snap:
        raise ValueError(f"Snapshot {req.snapshot_id} not found")
    if snap["status"] != "READY":
        raise ValueError(f"Snapshot {req.snapshot_id} is not READY (status: {snap['status']})")
    if snap["account_id"] != sess["account_id"]:
        raise ValueError("Snapshot belongs to a different Google account than this session")

    drive_state = await ensure_session_gdrive_ready(session_id, client=client)
    if not drive_state.get("ready"):
        if drive_state.get("action_status") == "AUTH_REQUIRED":
            return SnapshotActionResponse(
                action_status="AUTH_REQUIRED",
                redirect_url=drive_state.get("redirect_url"),
                snapshot=SnapshotResponse(**snap),
                message=drive_state.get("message"),
            )
        raise RuntimeError(drive_state.get("message") or "Google Drive not ready")

    existing_task = _SESSION_SNAPSHOT_TASKS.get(session_id)
    if existing_task and existing_task.get("status") == "RUNNING":
        raise ValueError("A snapshot operation is already running for this session")

    task_id = f"task_{uuid.uuid4().hex[:10]}"
    task = _update_task(
        session_id,
        task_id=task_id,
        snapshot_id=snap["id"],
        action="restore",
        status="RUNNING",
        step="restoring_packages",
        progress_pct=20,
        message=f"Restoring snapshot '{snap['name']}' from Google Drive...",
        error_message=None,
        stop_after_save=False,
        capture_mode=snap["capture_mode"],
        started_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        finished_at=None,
    )

    coro = _execute_restore_snapshot(
        session_id=session_id,
        snapshot_id=snap["id"],
        account_id=sess["account_id"],
    )
    if wait_for_completion:
        await coro
    else:
        asyncio.create_task(coro)

    return SnapshotActionResponse(
        action_status="STARTED",
        snapshot=SnapshotResponse(**snap),
        task=get_session_snapshot_task(session_id) or task,
        message="Snapshot restore started",
    )


async def _execute_restore_snapshot(
    session_id: str,
    snapshot_id: str,
    account_id: str,
) -> None:
    """Execute remote restore script over SSH and update session snapshot lineage."""
    try:
        _update_task(
            session_id,
            step="extracting_environment",
            progress_pct=50,
            message="Installing missing packages and extracting /content workspace...",
        )
        script = _build_remote_restore_script(snapshot_id=snapshot_id, account_id=account_id)
        result = await _run_remote_python(session_id, script, timeout=1800.0)

        with get_db() as conn:
            conn.execute(
                "UPDATE sessions SET snapshot_id = ? WHERE id = ?",
                (snapshot_id, session_id),
            )

        notes = result.get("notes") or []
        summary = (
            f"Restore completed (apt={result.get('apt_installed', 0)}, "
            f"pip={result.get('pip_installed', 0)}, binary={bool(result.get('binary_restored'))})."
        )
        if notes:
            summary += " " + "; ".join(str(n) for n in notes)

        _update_task(
            session_id,
            status="COMPLETED",
            step="done",
            progress_pct=100,
            message=summary,
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        )
    except Exception as exc:
        logger.error("Snapshot restore failed for session %s (%s): %s", session_id, snapshot_id, exc)
        err_msg = str(exc)
        _update_task(
            session_id,
            status="FAILED",
            step="error",
            message=err_msg,
            error_message=err_msg,
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        )


async def sync_snapshots_from_drive(
    session_id: str,
    client: Optional[ColabClient] = None,
) -> SnapshotActionResponse:
    """Scan `<MyDrive>/ColabSnapshots/*/manifest.json` via an active session and upsert DB."""
    sess = session_manager.get_session_internal(session_id)
    if not sess:
        raise ValueError(f"Session {session_id} not found")
    if sess.get("status") not in ("READY", "BUSY"):
        raise ValueError(f"Session {session_id} is not active (status: {sess.get('status')})")

    drive_state = await ensure_session_gdrive_ready(session_id, client=client)
    if not drive_state.get("ready"):
        if drive_state.get("action_status") == "AUTH_REQUIRED":
            return SnapshotActionResponse(
                action_status="AUTH_REQUIRED",
                redirect_url=drive_state.get("redirect_url"),
                message=drive_state.get("message"),
            )
        raise RuntimeError(drive_state.get("message") or "Google Drive not ready")

    account_id = sess["account_id"]
    payload = await _run_remote_python(session_id, _build_remote_sync_script(account_id), timeout=120.0)
    manifests = payload.get("manifests") or []

    with get_db() as conn:
        for mf in manifests:
            snap_id = mf.get("snapshot_id")
            if not snap_id:
                continue
            conn.execute(
                """
                INSERT INTO snapshots (
                    id, name, description, account_id, capture_mode,
                    drive_folder_path, python_version, accelerator, shape,
                    apt_package_count, pip_package_count,
                    workspace_size_bytes, pylibs_size_bytes, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'READY', NULL)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    capture_mode = excluded.capture_mode,
                    drive_folder_path = excluded.drive_folder_path,
                    python_version = excluded.python_version,
                    accelerator = excluded.accelerator,
                    shape = excluded.shape,
                    apt_package_count = excluded.apt_package_count,
                    pip_package_count = excluded.pip_package_count,
                    workspace_size_bytes = excluded.workspace_size_bytes,
                    pylibs_size_bytes = excluded.pylibs_size_bytes,
                    status = 'READY',
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    snap_id,
                    mf.get("name") or snap_id,
                    mf.get("description") or "",
                    account_id,
                    mf.get("capture_mode") or "hybrid",
                    mf.get("drive_folder_path") or f"/content/drive/MyDrive/{SNAPSHOT_DRIVE_DIR_NAME}/{snap_id}",
                    mf.get("python_version") or "",
                    mf.get("accelerator") or "T4",
                    mf.get("shape") or "standard",
                    int(mf.get("apt_package_count") or 0),
                    int(mf.get("pip_package_count") or 0),
                    int(mf.get("workspace_size_bytes") or 0),
                    int(mf.get("pylibs_size_bytes") or 0),
                ),
            )

    synced = list_snapshots(account_id=account_id)
    return SnapshotActionResponse(
        action_status="SYNCED",
        snapshots=[SnapshotResponse(**s) for s in synced],
        message=f"Synchronized {len(manifests)} snapshot(s) from Google Drive.",
    )
