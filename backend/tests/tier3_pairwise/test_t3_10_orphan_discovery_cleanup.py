"""T3.10: Fleet-Wide Orphan Discovery x One-Click Cleanup.

Validates the pairwise interaction between fleet-wide account polling and orphan reconciliation:
1. When unmanaged instances exist on Google Colab (e.g. created outside the manager via web browser),
   the periodic fleet sync detects them via GET /tun/m/assignments.
2. The system registers them in the sessions table with status 'ORPHAN' and generates deep-link URLs.
3. The admin can terminate the orphan instance via the one-click cleanup action.
4. The control plane assignment is cleanly unassigned, and DB state reflects STOPPED with 0 orphans remaining.
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
from backend.app.core.security import encrypt_secret
from backend.app.services import account_pool, session_manager
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestT310FleetOrphanDiscoveryAndCleanup(unittest.TestCase):
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

        # 1. Register Account 1
        self.acc1_id = "acc_fleet_1"
        self.acc1_email = "fleet1@example.com"
        mock_acc1 = MockOAuthAccount(
            email=self.acc1_email,
            name="Fleet User 1",
            sub="sub_fl_1",
            code="code_fl_1",
            access_token="ya29.token_fl_1",
            refresh_token="1//refresh_fl_1",
        )
        self.hub.oauth.state.register_account(mock_acc1)
        creds1 = {
            "access_token": "ya29.token_fl_1",
            "refresh_token": "1//refresh_fl_1",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Fleet User 1', ?, 50.0, 0.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.acc1_id, self.acc1_email, encrypt_secret(json.dumps(creds1))),
            )

        # 2. Register Account 2
        self.acc2_id = "acc_fleet_2"
        self.acc2_email = "fleet2@example.com"
        mock_acc2 = MockOAuthAccount(
            email=self.acc2_email,
            name="Fleet User 2",
            sub="sub_fl_2",
            code="code_fl_2",
            access_token="ya29.token_fl_2",
            refresh_token="1//refresh_fl_2",
        )
        self.hub.oauth.state.register_account(mock_acc2)
        creds2 = {
            "access_token": "ya29.token_fl_2",
            "refresh_token": "1//refresh_fl_2",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Fleet User 2', ?, 80.0, 0.0, 10.0, 2.0, 2, 'ACTIVE')
                """,
                (self.acc2_id, self.acc2_email, encrypt_secret(json.dumps(creds2))),
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t3_10_fleet_orphan_discovered_and_terminated_one_click(self):
        # 1. Verify clean fleet state initially: sync Account 1
        acc1_synced = account_pool.sync_account(self.acc1_id)
        self.assertEqual(acc1_synced["active_assignments"], 0)

        # 2. External unmanaged instance left running on Account 2 outside manager
        orphan_endpoint = "ep_orphan_fleet_999"
        self.hub.inject_orphan_assignment(
            endpoint=orphan_endpoint,
            accelerator="L4",
            email=self.acc2_email,
        )

        # Confirm unmanaged assignment exists in mock control plane
        self.assertIn(orphan_endpoint, self.hub.control_plane.state.assignments)

        # 3. Fleet sync runs for Account 2
        acc2_synced = account_pool.sync_account(self.acc2_id)

        # Invariant: Account 2 active_assignments count detected as 1
        self.assertEqual(acc2_synced["active_assignments"], 1)

        # 3. Query sessions with status == 'ORPHAN'
        orphans = session_manager.list_sessions(status_filter="ORPHAN")
        self.assertEqual(len(orphans), 1)

        orphan_sess = orphans[0]
        self.assertEqual(orphan_sess["endpoint"], orphan_endpoint)
        self.assertEqual(orphan_sess["account_id"], self.acc2_id)
        self.assertEqual(orphan_sess["accelerator"], "L4")
        self.assertEqual(orphan_sess["status"], "ORPHAN")

        # Invariant: Colab web deep-link generated for web inspection
        self.assertIn("colab.research.google.com", orphan_sess["colab_web_url"])
        self.assertIn(orphan_endpoint, orphan_sess["colab_web_url"])

        # 4. Admin performs One-Click Cleanup via stop_session
        orphan_id = orphan_sess["id"]
        stopped = session_manager.stop_session(orphan_id)
        self.assertTrue(stopped)

        # 5. Invariants:
        # In DB: status transitions to 'STOPPED'
        stopped_orphan = session_manager.get_session(orphan_id)
        self.assertEqual(stopped_orphan["status"], "STOPPED")
        self.assertIsNotNone(stopped_orphan["stopped_at"])

        # In mock control plane: assignment removed via unassign
        self.assertNotIn(orphan_endpoint, self.hub.control_plane.state.assignments)

        # 6. Re-sync Account 2: active_assignments decrements to 0
        acc2_resynced = account_pool.sync_account(self.acc2_id)
        self.assertEqual(acc2_resynced["active_assignments"], 0)

        # Zero orphans remain active
        remaining_orphans = session_manager.list_sessions(status_filter="ORPHAN")
        self.assertEqual(len(remaining_orphans), 0)


if __name__ == "__main__":
    unittest.main()
