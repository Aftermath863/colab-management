"""T3.6: Local Workspace Sync x Job Execution & Output Pull.

Validates the pairwise interaction between local workspace incremental synchronization and
job execution lifecycle:
1. Pre-job push syncs code files (e.g. .py, .json) while strictly skipping large binary weights
   (*.pt, *.bin, *.safetensors) and version control internals (.git/**).
2. Remote job generates artifacts in output/ (e.g. checkpoint.pt, metrics.json).
3. Post-job pull syncs remote output artifacts back to local host filesystem with 100% byte fidelity
   before session teardown.
"""

from __future__ import annotations

import base64
import fnmatch
import json
import os
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional, Set

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest
from backend.app.core.security import decrypt_secret, encrypt_secret
from backend.app.services import session_manager
from backend.app.services.colab_client import ColabClient
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


DEFAULT_EXCLUDE_PATTERNS = [
    ".git",
    ".git/*",
    "*/.git/*",
    "*.pt",
    "*.bin",
    "*.safetensors",
    "*.onnx",
    "__pycache__",
    "*.pyc",
]


class WorkspaceSyncEngine:
    """Incremental file synchronization engine between local workspace and remote Colab VFS."""

    def __init__(self, colab_client: Optional[ColabClient] = None):
        self.colab = colab_client or ColabClient()

    def should_exclude(self, rel_path: str, exclude_patterns: List[str]) -> bool:
        normalized = rel_path.replace("\\", "/")
        parts = normalized.split("/")
        for pattern in exclude_patterns:
            if fnmatch.fnmatch(normalized, pattern):
                return True
            if any(fnmatch.fnmatch(part, pattern) for part in parts):
                return True
        return False

    def push_workspace(
        self,
        local_dir: str,
        proxy_url: str,
        proxy_token: str,
        remote_prefix: str = "workspace",
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[str]:
        """Pushes local directory files to remote VFS, skipping excluded patterns."""
        excludes = exclude_patterns if exclude_patterns is not None else DEFAULT_EXCLUDE_PATTERNS
        uploaded_files = []

        for root, dirs, files in os.walk(local_dir):
            # Prune .git from dirs in-place
            dirs[:] = [d for d in dirs if not self.should_exclude(d, excludes)]

            for file in files:
                full_local_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_local_path, local_dir).replace("\\", "/")

                if self.should_exclude(rel_path, excludes):
                    continue

                remote_path = f"{remote_prefix.strip('/')}/{rel_path}".strip("/")

                with open(full_local_path, "rb") as f:
                    file_bytes = f.read()

                b64_content = base64.b64encode(file_bytes).decode("ascii")
                self.colab.upload_file(
                    proxy_url=proxy_url,
                    proxy_token=proxy_token,
                    path=remote_path,
                    content_base64=b64_content,
                    chunk=1,
                )
                uploaded_files.append(remote_path)

        return uploaded_files

    def pull_workspace(
        self,
        remote_prefix: str,
        local_dest_dir: str,
        proxy_url: str,
        proxy_token: str,
    ) -> List[str]:
        """Recursively pulls directory from remote VFS to local destination directory."""
        downloaded = []
        os.makedirs(local_dest_dir, exist_ok=True)

        def _pull_dir(remote_dir: str, current_local: str):
            listing = self.colab.list_contents(proxy_url, proxy_token, path=remote_dir)
            entries = listing.get("content", [])
            for entry in entries:
                name = entry["name"]
                remote_subpath = entry["path"]
                if entry["type"] == "directory":
                    next_local = os.path.join(current_local, name)
                    os.makedirs(next_local, exist_ok=True)
                    _pull_dir(remote_subpath, next_local)
                elif entry["type"] == "file":
                    file_resp = self.colab.download_file(proxy_url, proxy_token, path=remote_subpath)
                    content_b64 = file_resp.get("content", "")
                    content_bytes = base64.b64decode(content_b64)
                    local_file_path = os.path.join(current_local, name)
                    with open(local_file_path, "wb") as f:
                        f.write(content_bytes)
                    downloaded.append(local_file_path)

        _pull_dir(remote_prefix, local_dest_dir)
        return downloaded


class TestT36WorkspaceSyncAndJobOutputPull(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = MockControlHub()
        cls.hub.start()

        cls.orig_control_url = settings.CONTROL_PLANE_URL
        cls.orig_token_url = settings.OAUTH_TOKEN_URL
        cls.orig_userinfo_url = settings.OAUTH_USERINFO_URL

        settings.CONTROL_PLANE_URL = cls.hub.urls["control_plane"]
        settings.OAUTH_TOKEN_URL = cls.hub.oauth.token_uri
        settings.OAUTH_USERINFO_URL = cls.hub.oauth.userinfo_uri

    @classmethod
    def tearDownClass(cls):
        settings.CONTROL_PLANE_URL = cls.orig_control_url
        settings.OAUTH_TOKEN_URL = cls.orig_token_url
        settings.OAUTH_USERINFO_URL = cls.orig_userinfo_url
        cls.hub.stop()

    def setUp(self):
        self.hub.reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_manager.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        # Register account
        self.account_id = "acc_sync_test"
        self.email = "sync_tester@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Sync Tester",
            sub="sub_sync_123",
            code="code_sync",
            access_token="ya29.mock_token_sync",
            refresh_token="1//mock_refresh_sync",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_token_sync",
            "refresh_token": "1//mock_refresh_sync",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Sync Tester', ?, 100.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        # Create session
        self.sess = session_manager.create_session(
            SessionCreateRequest(name="sync-session", accelerator="T4", account_id=self.account_id)
        )
        self.session_id = self.sess["id"]
        self.endpoint = self.sess["endpoint"]
        self.proxy_url = self.sess["proxy_url"]

        internal = session_manager.get_session_internal(self.session_id)
        self.proxy_token = internal["proxy_token"]

        self.sync_engine = WorkspaceSyncEngine()

        # Create local workspace directory structure
        self.local_ws = os.path.join(self.tmpdir.name, "local_workspace")
        os.makedirs(self.local_ws, exist_ok=True)

        # Code files
        with open(os.path.join(self.local_ws, "train.py"), "w", encoding="utf-8") as f:
            f.write("print('Hello from training script')\n")
        with open(os.path.join(self.local_ws, "utils.py"), "w", encoding="utf-8") as f:
            f.write("def helper(): return 42\n")

        # Large weights file (should be skipped by pre-job sync)
        with open(os.path.join(self.local_ws, "large_weights.pt"), "wb") as f:
            f.write(b"SIMULATED_LARGE_WEIGHTS_100MB" * 100)

        # .git repository internals (should be skipped)
        git_dir = os.path.join(self.local_ws, ".git")
        os.makedirs(git_dir, exist_ok=True)
        with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as f:
            f.write("ref: refs/heads/main\n")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t3_6_pre_job_sync_skips_weights_and_post_job_pulls_artifacts(self):
        # 1. Pre-job incremental push to remote /workspace
        uploaded = self.sync_engine.push_workspace(
            local_dir=self.local_ws,
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            remote_prefix="workspace",
        )

        self.assertIn("workspace/train.py", uploaded)
        self.assertIn("workspace/utils.py", uploaded)
        self.assertNotIn("workspace/large_weights.pt", uploaded)
        self.assertNotIn("workspace/.git/HEAD", uploaded)

        # Query remote VFS directly to verify file presence and exclusion
        ep = self.hub.runtime_proxy.app.get_endpoint(self.endpoint)
        self.assertIsNotNone(ep)
        ws_node = ep.vfs.root.children["workspace"]
        self.assertIn("train.py", ws_node.children)
        self.assertIn("utils.py", ws_node.children)
        self.assertNotIn("large_weights.pt", ws_node.children)
        self.assertNotIn(".git", ws_node.children)

        # 2. Remote job executes and produces training artifacts in /workspace/output/
        ep.vfs.mkdir("workspace/output")
        weights_content = b"NEWLY_TRAINED_LORA_WEIGHTS_BINARY_DATA_XYZ"
        metrics_json = json.dumps({"loss": 0.042, "accuracy": 0.991, "epochs": 5}).encode("utf-8")

        ep.vfs.write_file(
            "workspace/output/checkpoint.pt",
            base64.b64encode(weights_content).decode("ascii"),
            chunk=1,
        )
        ep.vfs.write_file(
            "workspace/output/metrics.json",
            base64.b64encode(metrics_json).decode("ascii"),
            chunk=1,
        )

        # 3. Post-job pull artifacts back to local host
        local_output_dir = os.path.join(self.local_ws, "output")
        downloaded = self.sync_engine.pull_workspace(
            remote_prefix="workspace/output",
            local_dest_dir=local_output_dir,
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
        )

        local_ckpt_path = os.path.join(local_output_dir, "checkpoint.pt")
        local_metrics_path = os.path.join(local_output_dir, "metrics.json")

        self.assertIn(local_ckpt_path, downloaded)
        self.assertIn(local_metrics_path, downloaded)

        # 4. Invariant: Byte-for-byte exact match on local files
        self.assertTrue(os.path.exists(local_ckpt_path))
        with open(local_ckpt_path, "rb") as f:
            self.assertEqual(f.read(), weights_content)

        self.assertTrue(os.path.exists(local_metrics_path))
        with open(local_metrics_path, "rb") as f:
            self.assertEqual(f.read(), metrics_json)


if __name__ == "__main__":
    unittest.main()
