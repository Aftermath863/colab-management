"""T3.3: Session Teardown x Local Port 2220 Release.

Validates that when a session is torn down, its assigned local SSH multiplexer port
(from the pool 2220-2229) is immediately freed, updated to NULL in the database,
and recycled on the next session provisioning without port collisions or leaks.
"""

from __future__ import annotations

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
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestT33SessionTeardownPortRelease(unittest.TestCase):
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

        # Set up an active test account with ample CCU
        self.account_id = "acc_port_pool_test"
        self.email = "port_tester@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Port Tester",
            sub="sub_port_123",
            code="code_port",
            access_token="ya29.mock_token_port",
            refresh_token="1//mock_refresh_port",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_token_port",
            "refresh_token": "1//mock_refresh_port",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Port Tester', ?, 200.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t3_3_session_teardown_immediately_releases_port(self):
        # 1. Provision Session 1: acquires lowest available port 2220
        sess1 = session_manager.create_session(
            SessionCreateRequest(name="session-1", accelerator="T4", account_id=self.account_id)
        )
        self.assertEqual(sess1["status"], "READY")
        self.assertEqual(sess1["ssh_local_port"], 2220)

        # 2. Provision Session 2: acquires next available port 2221
        sess2 = session_manager.create_session(
            SessionCreateRequest(name="session-2", accelerator="T4", account_id=self.account_id)
        )
        self.assertEqual(sess2["status"], "READY")
        self.assertEqual(sess2["ssh_local_port"], 2221)

        # 3. Next allocation should return 2222
        next_port = session_manager.allocate_ssh_port()
        self.assertEqual(next_port, 2222)

        # 4. Teardown Session 1 via stop_session
        stopped = session_manager.stop_session(sess1["id"])
        self.assertTrue(stopped)

        # Verify DB state for Session 1: status is STOPPED, ssh_local_port is NULL
        sess1_db = session_manager.get_session(sess1["id"])
        self.assertEqual(sess1_db["status"], "STOPPED")
        self.assertIsNone(sess1_db["ssh_local_port"])
        self.assertIsNotNone(sess1_db["stopped_at"])

        # 5. Immediately check port allocator:
        # Port 2220 was freed and must be recycled as the lowest available port in 2220..2229
        recycled_port = session_manager.allocate_ssh_port()
        self.assertEqual(recycled_port, 2220)

        # 6. Provision Session 3:
        # Must claim recycled port 2220 without collisions
        sess3 = session_manager.create_session(
            SessionCreateRequest(name="session-3", accelerator="T4", account_id=self.account_id)
        )
        self.assertEqual(sess3["status"], "READY")
        self.assertEqual(sess3["ssh_local_port"], 2220)

        # Verify Session 2 still holds 2221 and Session 3 holds 2220
        sess2_db = session_manager.get_session(sess2["id"])
        self.assertEqual(sess2_db["status"], "READY")
        self.assertEqual(sess2_db["ssh_local_port"], 2221)

        # 7. Teardown Session 2 and Session 3
        session_manager.stop_session(sess2["id"])
        session_manager.stop_session(sess3["id"])

        # Verify both ports 2220 and 2221 are now free; allocator returns 2220
        self.assertEqual(session_manager.allocate_ssh_port(), 2220)


if __name__ == "__main__":
    unittest.main()
