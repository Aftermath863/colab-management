"""T3.7: Remote Monaco Edit Conflict Detection x Sync Safety.

Validates that when a developer makes an online edit directly in the remote Monaco Editor
while local files on the host are concurrently modified:
1. The 3-way synchronization engine detects the conflicting modification against the last sync baseline.
2. The remote Monaco edit is NOT silently overwritten by the local watcher.
3. The remote file in VFS retains the developer's Monaco edits.
4. A conflict artifact (e.g. filename.local_conflict) is preserved on the host.
5. Zero data loss is guaranteed for both local and remote changes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional, Tuple

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest
from backend.app.core.security import encrypt_secret
from backend.app.services import session_manager
from backend.app.services.colab_client import ColabClient
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SafeWorkspaceSyncEngine:
    """Sync engine with 3-way hash tracking to detect Monaco online edit conflicts."""

    def __init__(self, colab_client: Optional[ColabClient] = None):
        self.colab = colab_client or ColabClient()
        # Tracks {rel_path: (baseline_sha256, baseline_mtime)}
        self.baseline_state: Dict[str, str] = {}

    def record_baseline(self, rel_path: str, content: bytes) -> None:
        self.baseline_state[rel_path] = compute_sha256(content)

    def sync_file_safely(
        self,
        local_full_path: str,
        rel_path: str,
        proxy_url: str,
        proxy_token: str,
        remote_path: str,
    ) -> Dict[str, Any]:
        with open(local_full_path, "rb") as f:
            local_bytes = f.read()

        local_hash = compute_sha256(local_bytes)
        baseline_hash = self.baseline_state.get(rel_path)

        # 1. Fetch remote content and hash
        remote_file_resp = self.colab.download_file(proxy_url, proxy_token, path=remote_path)
        remote_b64 = remote_file_resp.get("content", "")
        remote_bytes = base64.b64decode(remote_b64)
        remote_hash = compute_sha256(remote_bytes)

        # 2. 3-Way Conflict Evaluation
        # If baseline exists and both local and remote changed to different values
        if baseline_hash is not None and local_hash != baseline_hash and remote_hash != baseline_hash:
            if local_hash != remote_hash:
                # Concurrent conflict detected!
                conflict_path = f"{local_full_path}.local_conflict"
                with open(conflict_path, "wb") as f_conf:
                    f_conf.write(local_bytes)

                return {
                    "status": "SYNC_CONFLICT",
                    "conflict_file": conflict_path,
                    "remote_preserved": True,
                    "message": f"Conflict detected on '{rel_path}': remote Monaco edit preserved, local saved to '{conflict_path}'",
                }

        # No conflict: safe to push
        b64_content = base64.b64encode(local_bytes).decode("ascii")
        self.colab.upload_file(
            proxy_url=proxy_url,
            proxy_token=proxy_token,
            path=remote_path,
            content_base64=b64_content,
            chunk=1,
        )
        self.baseline_state[rel_path] = local_hash
        return {"status": "SYNCED", "remote_preserved": False}


class TestT37MonacoEditConflictSyncSafety(unittest.TestCase):
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
        self.account_id = "acc_monaco_test"
        self.email = "monaco_tester@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Monaco Tester",
            sub="sub_monaco_123",
            code="code_monaco",
            access_token="ya29.mock_token_monaco",
            refresh_token="1//mock_refresh_monaco",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_token_monaco",
            "refresh_token": "1//mock_refresh_monaco",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Monaco Tester', ?, 100.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        # Create session
        self.sess = session_manager.create_session(
            SessionCreateRequest(name="monaco-session", accelerator="T4", account_id=self.account_id)
        )
        self.session_id = self.sess["id"]
        self.endpoint = self.sess["endpoint"]
        self.proxy_url = self.sess["proxy_url"]

        internal = session_manager.get_session_internal(self.session_id)
        self.proxy_token = internal["proxy_token"]

        self.sync_engine = SafeWorkspaceSyncEngine()

        # Set up local workspace file
        self.local_ws = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(self.local_ws, exist_ok=True)
        self.train_file = os.path.join(self.local_ws, "train.py")

        # Baseline v1 content
        self.baseline_content = b"print('Baseline v1: common initial code')\n"
        with open(self.train_file, "wb") as f:
            f.write(self.baseline_content)

        # Upload baseline to remote VFS
        self.colab_client = ColabClient()
        self.colab_client.upload_file(
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            path="workspace/train.py",
            content_base64=base64.b64encode(self.baseline_content).decode("ascii"),
            chunk=1,
        )

        # Record baseline in sync engine
        self.sync_engine.record_baseline("train.py", self.baseline_content)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t3_7_monaco_remote_edit_conflict_prevents_silent_overwrite(self):
        # 1. Developer edits file directly in remote Monaco Editor
        monaco_remote_content = b"print('Monaco Remote Edit v2: modified via web editor')\n"
        self.colab_client.upload_file(
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            path="workspace/train.py",
            content_base64=base64.b64encode(monaco_remote_content).decode("ascii"),
            chunk=1,
        )

        # Verify remote VFS currently holds Monaco edit
        ep = self.hub.runtime_proxy.app.get_endpoint(self.endpoint)
        self.assertIsNotNone(ep)
        ws_node = ep.vfs.root.children["workspace"]
        self.assertEqual(bytes(ws_node.children["train.py"].content), monaco_remote_content)

        # 2. Concurrently, developer modifies local file on host
        local_edit_content = b"print('Local File Edit v3: modified locally in IDE')\n"
        with open(self.train_file, "wb") as f:
            f.write(local_edit_content)

        # 3. Workspace sync engine triggers to push changes
        sync_result = self.sync_engine.sync_file_safely(
            local_full_path=self.train_file,
            rel_path="train.py",
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            remote_path="workspace/train.py",
        )

        # 4. Invariant: 3-way conflict is detected
        self.assertEqual(sync_result["status"], "SYNC_CONFLICT")
        self.assertTrue(sync_result["remote_preserved"])

        # 5. Invariant: Remote VFS file was NOT silently overwritten
        # Remote file strictly retains Monaco online edit
        remote_curr = self.colab_client.download_file(
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            path="workspace/train.py",
        )
        self.assertEqual(base64.b64decode(remote_curr["content"]), monaco_remote_content)

        # 6. Invariant: Local modification preserved in .local_conflict file
        conflict_file = sync_result["conflict_file"]
        self.assertTrue(os.path.exists(conflict_file))
        with open(conflict_file, "rb") as f:
            self.assertEqual(f.read(), local_edit_content)

        # Zero data loss: local edit exists in conflict_file, remote edit exists in VFS


if __name__ == "__main__":
    unittest.main()
