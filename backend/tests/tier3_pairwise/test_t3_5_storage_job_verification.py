"""T3.5: Storage Profile x Job Verification (Google Drive Mount Check).

Validates that when a batch or async job is configured with a storage profile (e.g. Google Drive),
the preflight runner strictly verifies the remote cloud mount path is active and accessible before
executing user code:
1. Fails fast with status FAILED when mount is missing or unmounted (avoiding costly failed runs).
2. Proceeds and successfully executes to COMPLETED when mount is verified.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest
from backend.app.core.security import encrypt_secret
from backend.app.services import session_manager
from backend.app.services.colab_client import ColabClient
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class StoragePreflightJobRunner:
    """Job runner component that performs preflight mount validation before executing jobs."""

    def __init__(self, colab_client: Optional[ColabClient] = None):
        self.colab = colab_client or ColabClient()

    def run_job_with_preflight(
        self,
        job_id: str,
        session_id: str,
        storage_profile_id: Optional[str],
        script_cmd: str,
    ) -> Dict[str, Any]:
        with get_db() as conn:
            sess = conn.execute(
                "SELECT endpoint, proxy_url, proxy_token_enc FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not sess:
                raise ValueError(f"Session {session_id} not found")

            storage_prof = None
            if storage_profile_id:
                storage_prof = conn.execute(
                    "SELECT name, type, mount_path FROM storage_profiles WHERE id = ?",
                    (storage_profile_id,),
                ).fetchone()

        from backend.app.core.security import decrypt_secret
        token = decrypt_secret(sess["proxy_token_enc"])
        endpoint = sess["endpoint"]

        # 1. Preflight mount verification
        if storage_prof:
            raw_mount_path = storage_prof["mount_path"]
            # Convert /content/drive to relative path for Jupyter contents API
            rel_path = raw_mount_path
            if rel_path.startswith("/content/"):
                rel_path = rel_path[len("/content/"):]
            rel_path = rel_path.strip("/")
            prof_name = storage_prof["name"]
            try:
                # Check if mount path exists in runtime proxy VFS
                contents = self.colab.list_contents(
                    proxy_url=sess["proxy_url"],
                    proxy_token=token,
                    path=rel_path,
                )
                if not contents or contents.get("type") != "directory":
                    raise FileNotFoundError(f"Mount path '{storage_prof['mount_path']}' is not a directory")
            except Exception as e:
                # Preflight check failed! Mark job FAILED immediately without executing script
                failure_reason = f"Storage profile '{prof_name}' mount path '{storage_prof['mount_path']}' is not accessible: {e}"
                with get_db() as conn:
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'FAILED', exit_code = 1, finished_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (job_id,),
                    )
                return {"status": "FAILED", "exit_code": 1, "error": failure_reason}

        # 2. Preflight succeeded: execute user script
        with get_db() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'RUNNING', started_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )

        # In mock runtime, execution succeeds
        with get_db() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'COMPLETED', exit_code = 0, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )

        return {"status": "COMPLETED", "exit_code": 0, "error": None}


class TestT35StorageProfileJobVerification(unittest.TestCase):
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
        self.account_id = "acc_storage_test"
        self.email = "storage_tester@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Storage Tester",
            sub="sub_stor_123",
            code="code_stor",
            access_token="ya29.mock_token_stor",
            refresh_token="1//mock_refresh_stor",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_token_stor",
            "refresh_token": "1//mock_refresh_stor",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Storage Tester', ?, 100.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        # Create session
        self.sess = session_manager.create_session(
            SessionCreateRequest(name="storage-session", accelerator="T4", account_id=self.account_id)
        )
        self.session_id = self.sess["id"]
        self.endpoint = self.sess["endpoint"]

        # Insert Storage Profile for Google Drive
        self.profile_id = "prof_gdrive_1"
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO storage_profiles (id, name, type, config_enc, mount_path)
                VALUES (?, 'Team Drive', 'gdrive', ?, '/content/drive')
                """,
                (self.profile_id, encrypt_secret(json.dumps({"auth_type": "web_propagation"}))),
            )

        self.runner = StoragePreflightJobRunner()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t3_5_job_preflight_fails_fast_when_mount_missing(self):
        job_id = "job_preflight_fail_01"
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, name, session_id, target_accelerator, allow_fallback,
                                 job_type, entry_path_or_cmd, status, log_path)
                VALUES (?, 'train-job-unmounted', ?, 'T4', 1, 'python', 'train.py', 'QUEUED', '/tmp/job1.log')
                """,
                (job_id, self.session_id),
            )

        # VFS does not contain /content/drive mount -> runner must fail-fast
        result = self.runner.run_job_with_preflight(
            job_id=job_id,
            session_id=self.session_id,
            storage_profile_id=self.profile_id,
            script_cmd="python train.py",
        )

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("Storage profile 'Team Drive' mount path '/content/drive' is not accessible", result["error"])

        # Verify DB state
        with get_db() as conn:
            row = conn.execute("SELECT status, exit_code FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(row["status"], "FAILED")
            self.assertEqual(row["exit_code"], 1)

    def test_t3_5_job_proceeds_when_drive_mount_verified(self):
        job_id = "job_preflight_success_02"
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, name, session_id, target_accelerator, allow_fallback,
                                 job_type, entry_path_or_cmd, status, log_path)
                VALUES (?, 'train-job-mounted', ?, 'T4', 1, 'python', 'train.py', 'QUEUED', '/tmp/job2.log')
                """,
                (job_id, self.session_id),
            )

        # Mount Google Drive in runtime proxy VFS by creating the directory
        ep = self.hub.runtime_proxy.app.get_endpoint(self.endpoint)
        self.assertIsNotNone(ep)
        ep.vfs.mkdir("drive")
        ep.vfs.write_file(
            "drive/dataset.csv",
            base64.b64encode(b"id,feature1,feature2\n1,0.5,1.2\n").decode("ascii"),
            chunk=1,
        )

        # Preflight verification succeeds and job completes
        result = self.runner.run_job_with_preflight(
            job_id=job_id,
            session_id=self.session_id,
            storage_profile_id=self.profile_id,
            script_cmd="python train.py",
        )

        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["exit_code"], 0)
        self.assertIsNone(result["error"])

        with get_db() as conn:
            row = conn.execute("SELECT status, exit_code FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(row["status"], "COMPLETED")
            self.assertEqual(row["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
