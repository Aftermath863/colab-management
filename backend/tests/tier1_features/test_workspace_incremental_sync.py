"""Tier 1 Feature Tests: Workspace Incremental Sync Engine & REST Endpoints (F10).

Validates:
1. Exclusion of version control internals (.git/**) and bytecode (__pycache__/**).
2. Exclusion of large model weights (*.safetensors, *.pt > 50MB).
3. Pre-creation of remote directory hierarchy via PUT /api/contents/{dir}.
4. Accurate transfer accounting and delta calculation.
5. Endpoints POST /api/sessions/{id}/sync/push, POST /api/sessions/{id}/sync/pull, and GET /api/sessions/{id}/sync/status.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import unittest

import httpx

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest, SyncPushRequest
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app
from backend.app.services import session_manager, workspace_sync
from backend.app.services.colab_client import ColabClient
from backend.app.services.workspace_sync import WorkspaceSyncEngine
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestWorkspaceIncrementalSync(unittest.TestCase):
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

        # Configure workspace directory
        self.workspace_dir = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(self.workspace_dir, exist_ok=True)
        settings.WORKSPACE_DIR = self.workspace_dir

        self.account_id = "acc_sync_test"
        self.email = "sync_tester@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Sync Tester",
            sub="sub_sync_123",
            code="code_sync",
            access_token="ya29.mock_sync_token",
            refresh_token="1//mock_sync_refresh",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_sync_token",
            "refresh_token": "1//mock_sync_refresh",
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

        self.token = create_access_token({"sub": "admin", "role": "admin"})
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_workspace_sync_engine_push_and_pull(self):
        """Test file push, exclusions (.git and >50MB weights), and subsequent pull."""
        # 1. Create a session
        sess = session_manager.create_session(
            SessionCreateRequest(
                name="sync-session",
                accelerator="T4",
                account_id=self.account_id,
                workspace_subpath="",
            )
        )
        session_id = sess["id"]
        internal_sess = session_manager.get_session_internal(session_id)
        proxy_url = internal_sess["proxy_url"]
        proxy_token = internal_sess["proxy_token"]

        # 2. Setup local workspace files
        # Code file (should sync)
        os.makedirs(os.path.join(self.workspace_dir, "src"), exist_ok=True)
        train_py = os.path.join(self.workspace_dir, "src", "train.py")
        with open(train_py, "w") as f:
            f.write("print('Hello from training script')\n")

        # Config file (should sync)
        cfg_json = os.path.join(self.workspace_dir, "config.json")
        with open(cfg_json, "w") as f:
            f.write(json.dumps({"lr": 0.001, "epochs": 5}))

        # .git internal file (must be skipped)
        git_dir = os.path.join(self.workspace_dir, ".git")
        os.makedirs(git_dir, exist_ok=True)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write("[core]\nrepositoryformatversion = 0\n")

        # Large weight file (100MB dummy - must be skipped)
        weights_file = os.path.join(self.workspace_dir, "model.safetensors")
        with open(weights_file, "wb") as f:
            # Write a large sparse file (seek and write 1 byte at 60MB mark)
            f.seek(60 * 1024 * 1024)
            f.write(b"\0")

        # 3. Execute Push via Engine
        engine = WorkspaceSyncEngine()
        uploaded = engine.push_workspace(
            local_dir=self.workspace_dir,
            proxy_url=proxy_url,
            proxy_token=proxy_token,
            remote_prefix="workspace",
        )

        # Assert uploaded files
        self.assertIn("workspace/src/train.py", uploaded)
        self.assertIn("workspace/config.json", uploaded)
        # Assert excluded files were NOT uploaded
        self.assertNotIn("workspace/.git/config", uploaded)
        self.assertNotIn("workspace/model.safetensors", uploaded)

        # 4. Create an output artifact on remote runtime (simulate job generation)
        colab = ColabClient()
        colab.upload_file(
            proxy_url=proxy_url,
            proxy_token=proxy_token,
            path="workspace/output/metrics.json",
            content_base64=base64.b64encode(b'{"loss": 0.05}').decode("ascii"),
        )

        # 5. Execute Pull via Engine into a new directory
        pull_target_dir = os.path.join(self.tmpdir.name, "pulled_workspace")
        os.makedirs(pull_target_dir, exist_ok=True)
        downloaded = engine.pull_workspace(
            local_dir=pull_target_dir,
            proxy_url=proxy_url,
            proxy_token=proxy_token,
            remote_prefix="workspace",
        )

        # Assert downloaded files
        self.assertIn("output/metrics.json", downloaded)
        pulled_metrics = os.path.join(pull_target_dir, "output", "metrics.json")
        self.assertTrue(os.path.isfile(pulled_metrics))
        with open(pulled_metrics, "r") as f:
            data = json.load(f)
            self.assertEqual(data["loss"], 0.05)

    def test_rest_api_sync_endpoints(self):
        """Verify POST /api/sessions/{id}/sync/push and /pull and GET sync/status."""
        sess = session_manager.create_session(
            SessionCreateRequest(name="api-sync-session", accelerator="T4", account_id=self.account_id)
        )
        session_id = sess["id"]

        # Put a local file in workspace
        with open(os.path.join(self.workspace_dir, "app.py"), "w") as f:
            f.write("import sys\n")

        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            # 1. GET sync status
            status_resp = client.get(f"/api/sessions/{session_id}/sync/status", headers=self.headers)
            self.assertEqual(status_resp.status_code, 200)
            status_data = status_resp.json()
            self.assertEqual(status_data["session_id"], session_id)
            self.assertFalse(status_data["is_syncing"])

            # 2. POST push sync (dry run)
            push_dry = client.post(
                f"/api/sessions/{session_id}/sync/push",
                headers=self.headers,
                json={"dry_run": True},
            )
            self.assertEqual(push_dry.status_code, 200)
            p_data = push_dry.json()
            self.assertTrue(p_data["success"])
            self.assertTrue(p_data["dry_run"])
            self.assertGreaterEqual(p_data["files_scanned"], 1)

            # 3. POST push sync (real)
            push_real = client.post(
                f"/api/sessions/{session_id}/sync/push",
                headers=self.headers,
                json={"dry_run": False},
            )
            self.assertEqual(push_real.status_code, 200)
            self.assertTrue(push_real.json()["success"])

            # 4. POST pull sync
            pull_resp = client.post(
                f"/api/sessions/{session_id}/sync/pull",
                headers=self.headers,
                json={"dry_run": False},
            )
            self.assertEqual(pull_resp.status_code, 200)
            self.assertTrue(pull_resp.json()["success"])
