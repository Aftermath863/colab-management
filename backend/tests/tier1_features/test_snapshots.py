"""Tests for Colab Environment Snapshot & Restore service, DB migration, and API routes."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import (
    SessionCreateRequest,
    SnapshotRestoreRequest,
    SnapshotSaveRequest,
)
from backend.app.core.security import encrypt_secret, get_current_admin
from backend.app.main import app
from backend.app.services import session_manager, snapshot_service


class _FakeGateway:
    """Executes remote python script locally against an isolated temp /content dir."""

    def __init__(
        self,
        content_dir: str,
        state_path: str,
        pylibs_target: str,
        site_packages_dir: str = "",
        local_bin_dir: str = "",
    ):
        self.content_dir = content_dir
        self.state_path = state_path
        self.pylibs_target = pylibs_target
        self.site_packages_dir = site_packages_dir
        self.local_bin_dir = local_bin_dir
        self.commands = []

    async def run_command(self, command: str, timeout: float = 300.0):
        self.commands.append(command)
        env = os.environ.copy()
        env["COLAB_SNAPSHOT_CONTENT_DIR"] = self.content_dir
        env["COLAB_SNAPSHOT_STATE_PATH"] = self.state_path
        env["COLAB_SNAPSHOT_PYLIBS_TARGET"] = self.pylibs_target
        if self.site_packages_dir:
            env["COLAB_SNAPSHOT_SITE_PACKAGES"] = self.site_packages_dir
        if self.local_bin_dir:
            env["COLAB_SNAPSHOT_LOCAL_BIN"] = self.local_bin_dir
        env["COLAB_SNAPSHOT_BOOT_CTIME"] = "0"
        proc = subprocess.run(
            ["/bin/sh", "-c", command],
            capture_output=True,
            text=True,
            env=env,
        )
        return SimpleNamespace(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_status=proc.returncode,
            returncode=proc.returncode,
        )


class TestSnapshots(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "snap_test.db")
        settings.DATABASE_PATH = self.db_path
        settings.DATA_DIR = os.path.join(self.tmpdir.name, "data")
        os.makedirs(settings.DATA_DIR, exist_ok=True)
        init_db(self.db_path)

        self.content_dir = os.path.join(self.tmpdir.name, "content")
        os.makedirs(self.content_dir, exist_ok=True)
        self.state_path = os.path.join(self.tmpdir.name, "state", "snapshot_state.json")
        self.pylibs_target = os.path.join(self.tmpdir.name, "pylibs_extract")
        os.makedirs(self.pylibs_target, exist_ok=True)
        self.site_packages_dir = os.path.join(self.tmpdir.name, "site_packages")
        os.makedirs(os.path.join(self.site_packages_dir, "my_custom_pkg"), exist_ok=True)
        os.makedirs(os.path.join(self.site_packages_dir, "my_custom_pkg-1.0.0.dist-info"), exist_ok=True)
        with open(os.path.join(self.site_packages_dir, "my_custom_pkg", "__init__.py"), "w") as f:
            f.write("CUSTOM_VAL = 42\n")
        with open(os.path.join(self.site_packages_dir, "my_custom_pkg-1.0.0.dist-info", "RECORD"), "w") as f:
            f.write("my_custom_pkg/__init__.py,sha256=abc,16\n")
        self.local_bin_dir = os.path.join(self.tmpdir.name, "local_bin")
        os.makedirs(self.local_bin_dir, exist_ok=True)

        # Clean mock drive folder for isolated account tests
        self.account_id = f"acc_{os.getpid()}"
        self.mock_drive = os.path.join("/tmp/mock_colab_drive", self.account_id)
        if os.path.exists(self.mock_drive):
            import shutil
            shutil.rmtree(self.mock_drive, ignore_errors=True)

        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, status)
                VALUES (?, 'snapuser@example.com', 'enc_tok', 95.0, 'ACTIVE')
                """,
                (self.account_id,),
            )
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, status)
                VALUES ('acc_other', 'other@example.com', 'enc_tok2', 80.0, 'ACTIVE')
                """
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, shape, lifecycle_policy, status
                ) VALUES (
                    'sess_active', 'train-01', ?, 'hash123', 'ep-1', 'https://mock-proxy',
                    ?, '2099-01-01 00:00:00', 'T4', 'standard', 'interactive', 'READY'
                )
                """,
                (
                    self.account_id,
                    encrypt_secret("mock-proxy-token"),
                ),
            )

    def tearDown(self):
        import shutil
        if os.path.exists(self.mock_drive):
            shutil.rmtree(self.mock_drive, ignore_errors=True)
        snapshot_service._SESSION_SNAPSHOT_TASKS.clear()
        self.tmpdir.cleanup()

    def test_legacy_db_migration_adds_snapshot_id_and_table(self):
        legacy_db = os.path.join(self.tmpdir.name, "legacy.db")
        conn = sqlite3.connect(legacy_db)
        conn.execute(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                target_accelerator TEXT NOT NULL DEFAULT 'T4',
                status TEXT NOT NULL DEFAULT 'QUEUED'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                accelerator TEXT NOT NULL DEFAULT 'T4',
                shape TEXT NOT NULL DEFAULT 'standard',
                template_id TEXT,
                status TEXT NOT NULL DEFAULT 'READY'
            )
            """
        )
        conn.commit()
        conn.close()

        init_db(legacy_db)

        check = sqlite3.connect(legacy_db)
        tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        cols = {r[1] for r in check.execute("PRAGMA table_info(sessions)").fetchall()}
        check.close()
        self.assertIn("snapshots", tables)
        self.assertIn("snapshot_id", cols)

    async def test_save_returns_auth_required_when_drive_consent_needed(self):
        with patch.object(
            snapshot_service.storage_wizard,
            "propagate_gdrive_credentials",
            return_value={
                "status": "AUTH_REQUIRED",
                "success": False,
                "redirect_url": "https://accounts.google.com/o/oauth2/auth?mock=1",
            },
        ):
            res = await snapshot_service.start_save_snapshot(
                "sess_active",
                SnapshotSaveRequest(name="unauthed-snap", capture_mode="hybrid"),
            )
        self.assertEqual(res.action_status, "AUTH_REQUIRED")
        self.assertIn("accounts.google.com", res.redirect_url)
        self.assertEqual(snapshot_service.list_snapshots(), [])

    async def test_hybrid_save_excludes_secrets_and_restores_workspace(self):
        # Populate /content with project files and secret/exclude artifacts
        proj_dir = os.path.join(self.content_dir, "my_project")
        os.makedirs(proj_dir, exist_ok=True)
        with open(os.path.join(proj_dir, "train.py"), "w", encoding="utf-8") as f:
            f.write("print('hello snapshot')\n")
        os.makedirs(os.path.join(self.content_dir, "drive", "MyDrive"), exist_ok=True)
        with open(os.path.join(self.content_dir, "drive", "MyDrive", "loop.txt"), "w") as f:
            f.write("must not be archived recursively")
        with open(os.path.join(self.content_dir, ".colab_env.sh"), "w") as f:
            f.write("export SECRET=123\n")
        os.makedirs(os.path.join(self.content_dir, "sample_data"), exist_ok=True)
        with open(os.path.join(self.content_dir, "sample_data", "README.md"), "w") as f:
            f.write("exclude sample_data")

        fake_gw = _FakeGateway(self.content_dir, self.state_path, self.pylibs_target)

        with (
            patch.object(
                snapshot_service.storage_wizard,
                "propagate_gdrive_credentials",
                return_value={"status": "PROPAGATED", "success": True},
            ),
            patch.object(
                snapshot_service.storage_wizard,
                "mount_gdrive_fuse_via_ssh",
                new=AsyncMock(return_value={"mounted": False, "skipped": True}),
            ),
            patch.object(
                snapshot_service,
                "_get_ssh_gateway",
                new=AsyncMock(return_value=fake_gw),
            ),
        ):
            res = await snapshot_service.start_save_snapshot(
                "sess_active",
                SnapshotSaveRequest(
                    name="hybrid-env-1",
                    description="PyTorch training setup",
                    capture_mode="hybrid",
                ),
                wait_for_completion=True,
            )

        self.assertEqual(res.action_status, "STARTED")
        snaps = snapshot_service.list_snapshots()
        self.assertEqual(len(snaps), 1)
        snap = snaps[0]
        self.assertEqual(snap["status"], "READY")
        self.assertEqual(snap["capture_mode"], "hybrid")
        self.assertGreater(snap["workspace_size_bytes"], 0)
        self.assertEqual(snap["pylibs_size_bytes"], 0)

        # Wipe /content and restore onto fresh session dir
        import shutil
        shutil.rmtree(proj_dir)
        self.assertFalse(os.path.exists(os.path.join(proj_dir, "train.py")))

        with (
            patch.object(
                snapshot_service.storage_wizard,
                "propagate_gdrive_credentials",
                return_value={"status": "PROPAGATED", "success": True},
            ),
            patch.object(
                snapshot_service.storage_wizard,
                "mount_gdrive_fuse_via_ssh",
                new=AsyncMock(return_value={"mounted": False, "skipped": True}),
            ),
            patch.object(
                snapshot_service,
                "_get_ssh_gateway",
                new=AsyncMock(return_value=fake_gw),
            ),
        ):
            restore_res = await snapshot_service.start_restore_snapshot(
                "sess_active",
                SnapshotRestoreRequest(snapshot_id=snap["id"]),
                wait_for_completion=True,
            )

        self.assertEqual(restore_res.action_status, "STARTED")
        self.assertTrue(os.path.isfile(os.path.join(proj_dir, "train.py")))
        # Excluded files should not have been restored into fresh /content
        task = snapshot_service.get_session_snapshot_task("sess_active")
        self.assertEqual(task["status"], "COMPLETED")

    async def test_full_binary_overwrite_and_stop_after_save(self):
        with open(os.path.join(self.content_dir, "checkpoint.pt"), "wb") as f:
            f.write(b"\x00" * 4096)
        fake_gw = _FakeGateway(
            self.content_dir,
            self.state_path,
            self.pylibs_target,
            self.site_packages_dir,
            self.local_bin_dir,
        )

        with (
            patch.object(
                snapshot_service.storage_wizard,
                "propagate_gdrive_credentials",
                return_value={"status": "PROPAGATED", "success": True},
            ),
            patch.object(
                snapshot_service.storage_wizard,
                "mount_gdrive_fuse_via_ssh",
                new=AsyncMock(return_value={"mounted": False, "skipped": True}),
            ),
            patch.object(
                snapshot_service,
                "_get_ssh_gateway",
                new=AsyncMock(return_value=fake_gw),
            ),
        ):
            first = await snapshot_service.start_save_snapshot(
                "sess_active",
                SnapshotSaveRequest(name="base-bin", capture_mode="full_binary"),
                wait_for_completion=True,
            )
            snap_id = first.snapshot.id

            # Update workspace and overwrite existing snapshot with stop_after_save=True
            with open(os.path.join(self.content_dir, "step2.txt"), "w") as f:
                f.write("overwritten snapshot content")

            with patch.object(session_manager, "stop_session", return_value=True) as mock_stop:
                overwrite_res = await snapshot_service.start_save_snapshot(
                    "sess_active",
                    SnapshotSaveRequest(
                        name="base-bin",
                        capture_mode="full_binary",
                        overwrite_snapshot_id=snap_id,
                        stop_after_save=True,
                    ),
                    wait_for_completion=True,
                )
                mock_stop.assert_called_once()

            self.assertEqual(overwrite_res.snapshot.id, snap_id)
            updated = snapshot_service.get_snapshot(snap_id)
            self.assertEqual(updated["status"], "READY")
            self.assertEqual(updated["capture_mode"], "full_binary")
            self.assertGreater(updated["pylibs_size_bytes"], 0)

            # Verify binary restore extracts my_custom_pkg/__init__.py into pylibs_target
            await snapshot_service.start_restore_snapshot(
                "sess_active",
                SnapshotRestoreRequest(snapshot_id=snap_id),
                wait_for_completion=True,
            )
            expected_pkg_init = os.path.join(
                self.pylibs_target,
                self.site_packages_dir.lstrip("/"),
                "my_custom_pkg",
                "__init__.py",
            )
            self.assertTrue(os.path.isfile(expected_pkg_init))

            # Verify Drive sync discovers snapshot even if DB row is deleted first
            snapshot_service.delete_snapshot(snap_id)
            self.assertEqual(snapshot_service.list_snapshots(), [])
            sync_res = await snapshot_service.sync_snapshots_from_drive("sess_active")
            self.assertEqual(sync_res.action_status, "SYNCED")
            self.assertEqual(len(sync_res.snapshots), 1)
            self.assertEqual(sync_res.snapshots[0].id, snap_id)

            # Verify overwrite failure keeps existing READY status intact
            broken_gw = MagicMock()
            broken_gw.run_command = AsyncMock(side_effect=RuntimeError("simulated SSH drop"))
            with patch.object(snapshot_service, "_get_ssh_gateway", new=AsyncMock(return_value=broken_gw)):
                await snapshot_service.start_save_snapshot(
                    "sess_active",
                    SnapshotSaveRequest(
                        name="base-bin",
                        capture_mode="hybrid",
                        overwrite_snapshot_id=snap_id,
                    ),
                    wait_for_completion=True,
                )
            preserved = snapshot_service.get_snapshot(snap_id)
            self.assertEqual(preserved["status"], "READY")
            self.assertIn("simulated SSH drop", preserved["error_message"])

    def test_create_session_binds_snapshot_owner_account(self):
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO snapshots (
                    id, name, account_id, capture_mode, drive_folder_path, status
                ) VALUES (
                    'snap_bound', 'Bound Env', ?, 'hybrid',
                    '/content/drive/MyDrive/ColabSnapshots/snap_bound', 'READY'
                )
                """,
                (self.account_id,),
            )

        # Mismatched account_id must be rejected
        with self.assertRaises(ValueError):
            session_manager.create_session(
                SessionCreateRequest(
                    name="bad-acct",
                    accelerator="T4",
                    shape="standard",
                    account_id="acc_other",
                    snapshot_id="snap_bound",
                ),
                client=MagicMock(),
            )

        # Empty account_id auto-binds to snapshot owner account
        mock_colab = MagicMock()
        mock_colab.assign.return_value = {
            "endpoint": "ep-restored",
            "runtimeProxyInfo": {"url": "https://mock-proxy", "token": "tok", "tokenExpiresInSeconds": 3600},
        }
        with (
            patch.object(session_manager.account_pool, "select_account", return_value=self.account_id) as sel,
            patch.object(session_manager.account_pool, "get_valid_access_token", return_value="oauth-tok"),
        ):
            created = session_manager.create_session(
                SessionCreateRequest(
                    name="restored-sess",
                    accelerator="T4",
                    shape="standard",
                    snapshot_id="snap_bound",
                ),
                client=mock_colab,
            )
            sel.assert_called_once_with(self.account_id)

        self.assertEqual(created["snapshot_id"], "snap_bound")
        self.assertEqual(created["account_id"], self.account_id)

    def test_snapshot_api_routes_roundtrip(self):
        app.dependency_overrides[get_current_admin] = lambda: {"sub": "admin"}
        try:
            client = TestClient(app)
            with get_db(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO snapshots (
                        id, name, description, account_id, capture_mode, drive_folder_path, status
                    ) VALUES (
                        'snap_api_1', 'API Snap', 'desc', ?, 'hybrid',
                        '/content/drive/MyDrive/ColabSnapshots/snap_api_1', 'READY'
                    )
                    """,
                    (self.account_id,),
                )

            r_list = client.get("/api/snapshots")
            self.assertEqual(r_list.status_code, 200)
            self.assertEqual(len(r_list.json()), 1)
            self.assertEqual(r_list.json()[0]["id"], "snap_api_1")

            r_get = client.get("/api/snapshots/snap_api_1")
            self.assertEqual(r_get.status_code, 200)
            self.assertEqual(r_get.json()["name"], "API Snap")

            r_task = client.get("/api/sessions/sess_active/snapshots/task")
            self.assertEqual(r_task.status_code, 200)
            self.assertEqual(r_task.json()["status"], "IDLE")

            r_del = client.delete("/api/snapshots/snap_api_1")
            self.assertEqual(r_del.status_code, 204)
            self.assertEqual(client.get("/api/snapshots").json(), [])
        finally:
            app.dependency_overrides.clear()
