"""Workspace Incremental Synchronization Engine.

Synchronizes local ./workspace code directory with remote Colab /content runtime.
Enforces:
1. Exclusion of .git, __pycache__, .venv, and version control internals.
2. Large model weight file exclusion (*.pt, *.bin, *.safetensors, *.onnx > 50MB by default).
3. Pre-creation of remote directory hierarchy via PUT /api/contents/{dir}.
4. Safe push and pull incremental change detection with detailed transfer accounting.
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import hashlib
import json
import logging
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.app.config import settings
from backend.app.core.database import get_db
from backend.app.core.models import (
    SyncFileDetail,
    SyncPullRequest,
    SyncPushRequest,
    SyncResultResponse,
)
from backend.app.core.security import decrypt_secret
from backend.app.services import session_manager
from backend.app.services.colab_client import ColabClient
from backend.app.services.storage_wizard import ensure_remote_directory

logger = logging.getLogger("workspace_sync")

# Default Exclusion Patterns
DEFAULT_EXCLUDE_DIR_PATTERNS = [
    ".git",
    "*/.git",
    "*/.git/*",
    "__pycache__",
    "*/__pycache__",
    "*/__pycache__/*",
    ".venv",
    "*/.venv",
    "*/.venv/*",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    ".ipynb_checkpoints",
]

DEFAULT_EXCLUDE_FILE_PATTERNS = [
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".DS_Store",
    "Thumbs.db",
    ".env",
    ".env.*",
]

LARGE_MODEL_PATTERNS = [
    "*.pt",
    "*.bin",
    "*.safetensors",
    "*.onnx",
    "*.ckpt",
    "*.h5",
    "*.pkl",
    "*.pb",
    "*.tflite",
    "*.weights",
    "*.pth",
    "*.gguf",
]

DEFAULT_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB


def compute_sha256(data: bytes) -> str:
    """Compute SHA-256 hash of bytes."""
    return hashlib.sha256(data).hexdigest()


class WorkspaceSyncEngine:
    """Core synchronization engine between host filesystem and Colab VFS."""

    def __init__(self, colab_client: Optional[ColabClient] = None):
        self.colab = colab_client or ColabClient()
        self.baseline_state: Dict[str, str] = {}

    def should_exclude_path(
        self,
        rel_path: str,
        custom_excludes: Optional[List[str]] = None,
        is_dir: bool = False,
    ) -> bool:
        """Determine if a relative path matches directory or file exclude patterns."""
        normalized = rel_path.replace("\\", "/").strip("/")
        parts = normalized.split("/")

        patterns = list(DEFAULT_EXCLUDE_DIR_PATTERNS)
        if not is_dir:
            patterns.extend(DEFAULT_EXCLUDE_FILE_PATTERNS)
        if custom_excludes:
            patterns.extend(custom_excludes)

        for pat in patterns:
            if fnmatch.fnmatch(normalized, pat):
                return True
            if any(fnmatch.fnmatch(part, pat) for part in parts):
                return True

        return False

    def is_large_model_file(
        self, file_name: str, file_size: int, max_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES
    ) -> bool:
        """Check if file matches model weight extensions AND exceeds size threshold."""
        for pat in LARGE_MODEL_PATTERNS:
            if fnmatch.fnmatch(file_name.lower(), pat):
                return file_size > max_size_bytes
        return False

    def push_workspace(
        self,
        local_dir: str,
        proxy_url: str,
        proxy_token: str,
        remote_prefix: str = "workspace",
        exclude_patterns: Optional[List[str]] = None,
        max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
    ) -> List[str]:
        """Pushes local directory files to remote VFS, skipping excluded patterns.

        Returns list of successfully uploaded remote relative paths.
        """
        uploaded_files = []
        clean_prefix = remote_prefix.strip("/")

        for root, dirs, files in os.walk(local_dir):
            # Prune excluded directories in-place
            dirs[:] = [
                d
                for d in dirs
                if not self.should_exclude_path(
                    os.path.relpath(os.path.join(root, d), local_dir),
                    custom_excludes=exclude_patterns,
                    is_dir=True,
                )
            ]

            for file in files:
                full_local_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_local_path, local_dir).replace("\\", "/")

                if self.should_exclude_path(rel_path, custom_excludes=exclude_patterns, is_dir=False):
                    continue

                if os.path.islink(full_local_path):
                    try:
                        resolved_target = os.path.realpath(full_local_path)
                        canonical_base = os.path.realpath(os.path.abspath(local_dir))
                        if os.path.commonpath([canonical_base, resolved_target]) != canonical_base:
                            logger.warning(
                                f"Skipping symlink '{rel_path}' pointing outside workspace to '{resolved_target}'"
                            )
                            continue
                    except (FileNotFoundError, OSError, ValueError) as e:
                        logger.warning(f"Skipping invalid symlink '{rel_path}': {e}")
                        continue

                try:
                    file_size = os.path.getsize(full_local_path)
                except (FileNotFoundError, OSError) as e:
                    logger.warning(f"Skipping invalid symlink or unreadable file '{rel_path}': {e}")
                    continue

                if self.is_large_model_file(file, file_size, max_file_size_bytes):
                    logger.info(f"Skipping large file '{rel_path}' ({file_size} bytes)")
                    continue

                remote_path = f"{clean_prefix}/{rel_path}".strip("/")
                parent_dir = os.path.dirname(remote_path)
                if parent_dir:
                    ensure_remote_directory(self.colab, proxy_url, proxy_token, parent_dir)

                try:
                    with open(full_local_path, "rb") as f:
                        file_bytes = f.read()
                except PermissionError as e:
                    logger.warning(f"Skipping unreadable file '{rel_path}' due to permission error: {e}")
                    continue
                except (FileNotFoundError, OSError) as e:
                    logger.warning(f"Skipping unreadable file '{rel_path}': {e}")
                    continue

                b64_content = base64.b64encode(file_bytes).decode("ascii")
                self.colab.upload_file(
                    proxy_url=proxy_url,
                    proxy_token=proxy_token,
                    path=remote_path,
                    content_base64=b64_content,
                    chunk=1,
                )
                self.baseline_state[rel_path] = compute_sha256(file_bytes)
                uploaded_files.append(remote_path)

        return uploaded_files

    def pull_workspace(
        self,
        local_dir: str,
        proxy_url: str,
        proxy_token: str,
        remote_prefix: str = "workspace",
        exclude_patterns: Optional[List[str]] = None,
        max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
    ) -> List[str]:
        """Downloads remote directory files from Colab VFS to local filesystem.

        Returns list of downloaded relative file paths.
        """
        downloaded_files = []
        clean_prefix = remote_prefix.strip("/")
        queue = [clean_prefix]

        while queue:
            current_remote_dir = queue.pop(0)
            try:
                contents_resp = self.colab.list_contents(
                    proxy_url=proxy_url,
                    proxy_token=proxy_token,
                    path=current_remote_dir,
                )
            except Exception as e:
                logger.warning(f"Failed to list remote directory '{current_remote_dir}': {e}")
                continue

            items = contents_resp.get("content") or []
            if not isinstance(items, list):
                continue

            for item in items:
                item_name = item.get("name", "")
                item_path = item.get("path", "").strip("/")
                item_type = item.get("type", "")

                # Calculate relative path relative to clean_prefix
                if item_path.startswith(clean_prefix):
                    rel_sub = item_path[len(clean_prefix):].strip("/")
                else:
                    rel_sub = item_name

                if not rel_sub:
                    continue

                if item_type == "directory":
                    if not self.should_exclude_path(
                        rel_sub, custom_excludes=exclude_patterns, is_dir=True
                    ):
                        queue.append(item_path)
                elif item_type == "file":
                    if self.should_exclude_path(
                        rel_sub, custom_excludes=exclude_patterns, is_dir=False
                    ):
                        continue

                    size = item.get("size", 0)
                    if self.is_large_model_file(item_name, size, max_file_size_bytes):
                        logger.info(f"Skipping large remote file '{rel_sub}' ({size} bytes)")
                        continue

                    # Download file content
                    try:
                        file_data = self.colab.download_file(
                            proxy_url=proxy_url,
                            proxy_token=proxy_token,
                            path=item_path,
                        )
                        b64_content = file_data.get("content", "")
                        raw_bytes = base64.b64decode(b64_content) if file_data.get("format") == "base64" else b64_content.encode("utf-8")

                        local_full = os.path.join(local_dir, rel_sub)
                        canonical_local = os.path.realpath(os.path.abspath(local_full))
                        canonical_base = os.path.realpath(os.path.abspath(local_dir))
                        try:
                            if os.path.commonpath([canonical_base, canonical_local]) != canonical_base:
                                logger.warning(f"Rejecting path traversal attempt: '{rel_sub}'")
                                continue
                        except ValueError:
                            logger.warning(f"Rejecting cross-drive path: '{rel_sub}'")
                            continue

                        os.makedirs(os.path.dirname(local_full), exist_ok=True)
                        with open(local_full, "wb") as f_out:
                            f_out.write(raw_bytes)

                        self.baseline_state[rel_sub] = compute_sha256(raw_bytes)
                        downloaded_files.append(rel_sub)
                    except Exception as e:
                        logger.error(f"Failed to download remote file '{item_path}': {e}")

        return downloaded_files


# Singleton Engine & Session Locks
_sync_engine = WorkspaceSyncEngine()
_session_sync_locks: Dict[str, asyncio.Lock] = {}


def _get_sync_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _session_sync_locks:
        _session_sync_locks[session_id] = asyncio.Lock()
    return _session_sync_locks[session_id]


def resolve_session_sync_paths(session_id: str) -> Tuple[str, str, str, str]:
    """Resolve (local_dir, remote_prefix, proxy_url, proxy_token) for a session."""
    sess = session_manager.get_session_internal(session_id)
    if not sess:
        raise ValueError(f"Session {session_id} not found")

    with get_db() as conn:
        meta = conn.execute(
            "SELECT workspace_subpath, remote_workdir FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()

    subpath = meta["workspace_subpath"] if meta and meta["workspace_subpath"] else ""
    remote_workdir = meta["remote_workdir"] if meta and meta["remote_workdir"] else "/content"

    canonical_ws_base = os.path.realpath(os.path.abspath(settings.WORKSPACE_DIR))
    local_dir = os.path.realpath(os.path.abspath(os.path.join(canonical_ws_base, subpath)))
    try:
        if os.path.commonpath([canonical_ws_base, local_dir]) != canonical_ws_base:
            raise ValueError(f"Invalid workspace subpath '{subpath}': attempts directory traversal")
    except ValueError:
        raise ValueError(f"Invalid workspace subpath '{subpath}': cross-drive traversal")
    os.makedirs(local_dir, exist_ok=True)

    # Convert /content/workspace or /content to relative path for Colab Contents API
    remote_prefix = remote_workdir
    if remote_prefix.startswith("/content/"):
        remote_prefix = remote_prefix[len("/content/"):].strip("/")
    if not remote_prefix or remote_prefix == "/content":
        remote_prefix = "workspace"

    return local_dir, remote_prefix, sess["proxy_url"], sess["proxy_token"]


async def sync_push_session(
    session_id: str,
    req: SyncPushRequest,
    client: Optional[ColabClient] = None,
) -> Dict[str, Any]:
    """Execute push sync for a session (local ./workspace -> Colab runtime)."""
    lock = _get_sync_lock(session_id)
    async with lock:
        start_ts = time.time()
        local_dir, remote_prefix, proxy_url, proxy_token = resolve_session_sync_paths(session_id)
        colab = client or ColabClient()
        engine = WorkspaceSyncEngine(colab)

        max_size_bytes = req.max_file_size_mb * 1024 * 1024
        excludes = req.exclude_patterns or []

        files_scanned = 0
        files_uploaded = 0
        files_skipped = 0
        total_bytes = 0
        details: List[Dict[str, Any]] = []

        clean_prefix = remote_prefix.strip("/")

        for root, dirs, files in os.walk(local_dir):
            dirs[:] = [
                d
                for d in dirs
                if not engine.should_exclude_path(
                    os.path.relpath(os.path.join(root, d), local_dir),
                    custom_excludes=excludes,
                    is_dir=True,
                )
            ]

            for file in files:
                files_scanned += 1
                full_local = os.path.join(root, file)
                rel_path = os.path.relpath(full_local, local_dir).replace("\\", "/")

                if engine.should_exclude_path(rel_path, custom_excludes=excludes, is_dir=False):
                    files_skipped += 1
                    try:
                        sz = os.path.getsize(full_local)
                    except (FileNotFoundError, OSError):
                        sz = 0
                    details.append({
                        "path": rel_path,
                        "action": "skipped",
                        "size": sz,
                        "reason": "excluded_pattern",
                    })
                    continue

                if os.path.islink(full_local):
                    try:
                        resolved_target = os.path.realpath(full_local)
                        canonical_base = os.path.realpath(os.path.abspath(local_dir))
                        if os.path.commonpath([canonical_base, resolved_target]) != canonical_base:
                            logger.warning(
                                f"Skipping symlink '{rel_path}' pointing outside workspace to '{resolved_target}'"
                            )
                            files_skipped += 1
                            details.append({
                                "path": rel_path,
                                "action": "skipped",
                                "size": 0,
                                "reason": "symlink_outside_workspace",
                            })
                            continue
                    except (FileNotFoundError, OSError, ValueError) as e:
                        logger.warning(f"Skipping invalid symlink '{rel_path}': {e}")
                        files_skipped += 1
                        details.append({
                            "path": rel_path,
                            "action": "skipped",
                            "size": 0,
                            "reason": "invalid_symlink",
                            "error": str(e),
                        })
                        continue

                try:
                    file_size = os.path.getsize(full_local)
                except (FileNotFoundError, OSError) as e:
                    logger.warning(f"Skipping invalid symlink or unreadable file '{rel_path}': {e}")
                    files_skipped += 1
                    details.append({
                        "path": rel_path,
                        "action": "skipped",
                        "size": 0,
                        "reason": "invalid_symlink",
                        "error": str(e),
                    })
                    continue

                if engine.is_large_model_file(file, file_size, max_size_bytes):
                    files_skipped += 1
                    details.append({
                        "path": rel_path,
                        "action": "skipped",
                        "size": file_size,
                        "reason": "large_file_excluded",
                    })
                    continue

                if req.dry_run:
                    files_uploaded += 1
                    total_bytes += file_size
                    details.append({
                        "path": rel_path,
                        "action": "uploaded",
                        "size": file_size,
                        "reason": "dry_run",
                    })
                    continue

                remote_path = f"{clean_prefix}/{rel_path}".strip("/")
                parent_dir = os.path.dirname(remote_path)
                if parent_dir:
                    ensure_remote_directory(colab, proxy_url, proxy_token, parent_dir)

                try:
                    with open(full_local, "rb") as f_in:
                        content_bytes = f_in.read()
                except PermissionError as e:
                    logger.warning(f"Skipping unreadable file '{rel_path}' due to permission error: {e}")
                    files_skipped += 1
                    details.append({
                        "path": rel_path,
                        "action": "skipped",
                        "size": file_size,
                        "reason": "permission_denied",
                        "error": str(e),
                    })
                    continue
                except (FileNotFoundError, OSError) as e:
                    logger.warning(f"Skipping unreadable file '{rel_path}': {e}")
                    files_skipped += 1
                    details.append({
                        "path": rel_path,
                        "action": "skipped",
                        "size": file_size,
                        "reason": "read_failed",
                        "error": str(e),
                    })
                    continue

                b64 = base64.b64encode(content_bytes).decode("ascii")
                try:
                    await asyncio.to_thread(
                        colab.upload_file,
                        proxy_url,
                        proxy_token,
                        remote_path,
                        b64,
                        chunk=1,
                    )
                    files_uploaded += 1
                    total_bytes += file_size
                    details.append({
                        "path": rel_path,
                        "action": "uploaded",
                        "size": file_size,
                        "reason": "content_modified",
                    })
                except Exception as e:
                    details.append({
                        "path": rel_path,
                        "action": "error",
                        "size": file_size,
                        "reason": "upload_failed",
                        "error": str(e),
                    })

        duration_ms = int((time.time() - start_ts) * 1000)
        return {
            "session_id": session_id,
            "action": "push",
            "dry_run": req.dry_run,
            "files_scanned": files_scanned,
            "files_uploaded": files_uploaded,
            "files_skipped": files_skipped,
            "files_deleted": 0,
            "total_bytes_transferred": total_bytes,
            "duration_ms": duration_ms,
            "details": details,
            "success": True,
        }


async def sync_pull_session(
    session_id: str,
    req: SyncPullRequest,
    client: Optional[ColabClient] = None,
) -> Dict[str, Any]:
    """Execute pull sync for a session (Colab runtime -> local ./workspace)."""
    lock = _get_sync_lock(session_id)
    async with lock:
        start_ts = time.time()
        local_dir, remote_prefix, proxy_url, proxy_token = resolve_session_sync_paths(session_id)
        colab = client or ColabClient()
        engine = WorkspaceSyncEngine(colab)

        max_size_bytes = req.max_file_size_mb * 1024 * 1024
        excludes = req.exclude_patterns or []

        files_scanned = 0
        files_downloaded = 0
        files_skipped = 0
        total_bytes = 0
        details: List[Dict[str, Any]] = []

        clean_prefix = remote_prefix.strip("/")
        queue = [clean_prefix]

        while queue:
            current_remote_dir = queue.pop(0)
            try:
                contents_resp = await asyncio.to_thread(
                    colab.list_contents,
                    proxy_url,
                    proxy_token,
                    current_remote_dir,
                )
            except Exception as e:
                logger.warning(f"Failed to list remote directory '{current_remote_dir}': {e}")
                continue

            items = contents_resp.get("content") or []
            if not isinstance(items, list):
                continue

            for item in items:
                files_scanned += 1
                item_name = item.get("name", "")
                item_path = item.get("path", "").strip("/")
                item_type = item.get("type", "")

                if item_path.startswith(clean_prefix):
                    rel_sub = item_path[len(clean_prefix):].strip("/")
                else:
                    rel_sub = item_name

                if not rel_sub:
                    continue

                if item_type == "directory":
                    if not engine.should_exclude_path(rel_sub, custom_excludes=excludes, is_dir=True):
                        queue.append(item_path)
                elif item_type == "file":
                    file_size = item.get("size", 0)
                    if engine.should_exclude_path(rel_sub, custom_excludes=excludes, is_dir=False):
                        files_skipped += 1
                        details.append({
                            "path": rel_sub,
                            "action": "skipped",
                            "size": file_size,
                            "reason": "excluded_pattern",
                        })
                        continue

                    if engine.is_large_model_file(item_name, file_size, max_size_bytes):
                        files_skipped += 1
                        details.append({
                            "path": rel_sub,
                            "action": "skipped",
                            "size": file_size,
                            "reason": "large_file_excluded",
                        })
                        continue

                    if req.dry_run:
                        files_downloaded += 1
                        total_bytes += file_size
                        details.append({
                            "path": rel_sub,
                            "action": "downloaded",
                            "size": file_size,
                            "reason": "dry_run",
                        })
                        continue

                    try:
                        file_data = await asyncio.to_thread(
                            colab.download_file,
                            proxy_url,
                            proxy_token,
                            item_path,
                        )
                        b64_content = file_data.get("content", "")
                        raw_bytes = (
                            base64.b64decode(b64_content)
                            if file_data.get("format") == "base64"
                            else b64_content.encode("utf-8")
                        )

                        local_full = os.path.join(local_dir, rel_sub)
                        canonical_local = os.path.realpath(os.path.abspath(local_full))
                        canonical_base = os.path.realpath(os.path.abspath(local_dir))
                        try:
                            if os.path.commonpath([canonical_base, canonical_local]) != canonical_base:
                                logger.warning(f"Rejecting path traversal attempt: '{rel_sub}'")
                                files_skipped += 1
                                details.append({
                                    "path": rel_sub,
                                    "action": "skipped",
                                    "size": file_size,
                                    "reason": "path_traversal_rejected",
                                })
                                continue
                        except ValueError:
                            logger.warning(f"Rejecting cross-drive path: '{rel_sub}'")
                            files_skipped += 1
                            continue

                        os.makedirs(os.path.dirname(local_full), exist_ok=True)
                        with open(local_full, "wb") as f_out:
                            f_out.write(raw_bytes)

                        files_downloaded += 1
                        total_bytes += len(raw_bytes)
                        details.append({
                            "path": rel_sub,
                            "action": "downloaded",
                            "size": len(raw_bytes),
                            "reason": "downloaded",
                        })
                    except Exception as e:
                        details.append({
                            "path": rel_sub,
                            "action": "error",
                            "size": file_size,
                            "reason": "download_failed",
                            "error": str(e),
                        })

        duration_ms = int((time.time() - start_ts) * 1000)
        return {
            "session_id": session_id,
            "action": "pull",
            "dry_run": req.dry_run,
            "files_scanned": files_scanned,
            "files_downloaded": files_downloaded,
            "files_skipped": files_skipped,
            "total_bytes_transferred": total_bytes,
            "duration_ms": duration_ms,
            "details": details,
            "success": True,
        }
