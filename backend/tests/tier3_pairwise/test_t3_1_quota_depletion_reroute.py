"""T3.1: Account Quota Depletion x Session Shutdown & Reroute to Account B.

Validates that when an account's CCU balance falls below hard_stop_ccu:
1. Account sync marks the account status as 'COOLDOWN'.
2. The lifecycle watchdog / quota circuit breaker terminates all active sessions on that account.
3. The session's allocated SSH port is released.
4. The remote Colab instance is unassigned via two-phase handshake.
5. Subsequent session allocation automatically skips the cooldown account and provisions
   on an alternative active account with healthy CCU quota.
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
from backend.app.services import account_pool, session_manager
from backend.app.services.colab_client import ColabClient
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class QuotaCircuitBreakerWatchdog:
    """Watchdog that evaluates account CCU health and terminates active sessions on cooldown accounts."""

    @staticmethod
    def evaluate_and_enforce_hard_stops(client: Optional[ColabClient] = None) -> List[str]:
        stopped_session_ids = []
        with get_db() as conn:
            # Find active sessions belonging to accounts in COOLDOWN or AUTH_EXPIRED
            rows = conn.execute(
                """
                SELECT s.id, s.account_id, a.status as account_status, a.ccu_balance, a.hard_stop_ccu
                FROM sessions s
                JOIN accounts a ON s.account_id = a.id
                WHERE a.status IN ('COOLDOWN', 'AUTH_EXPIRED')
                  AND s.status NOT IN ('STOPPED', 'FAILED', 'LOST')
                """
            ).fetchall()

        for row in rows:
            sess_id = row["id"]
            session_manager.stop_session(sess_id, client=client)
            stopped_session_ids.append(sess_id)

        return stopped_session_ids


class TestT31QuotaDepletionAndReroute(unittest.TestCase):
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

        # Register Account Alpha (Priority 10, initial balance 3.0, hard_stop 2.0)
        self.acc_alpha_id = "acc_alpha_1"
        self.email_alpha = "alpha@example.com"
        mock_acc_alpha = MockOAuthAccount(
            email=self.email_alpha,
            name="Alpha Account",
            sub="sub_alpha_1",
            code="code_alpha",
            access_token="ya29.mock_token_alpha",
            refresh_token="1//mock_refresh_alpha",
        )
        self.hub.oauth.state.register_account(mock_acc_alpha)
        self.hub.set_account_ccu(self.email_alpha, balance=3.0, burn_rate=1.5)

        creds_alpha = {
            "access_token": "ya29.mock_token_alpha",
            "refresh_token": "1//mock_refresh_alpha",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Alpha Account', ?, 3.0, 1.5, 5.0, 2.0, 10, 'ACTIVE')
                """,
                (self.acc_alpha_id, self.email_alpha, encrypt_secret(json.dumps(creds_alpha))),
            )

        # Register Account Beta (Priority 5, initial balance 50.0, hard_stop 2.0)
        self.acc_beta_id = "acc_beta_2"
        self.email_beta = "beta@example.com"
        mock_acc_beta = MockOAuthAccount(
            email=self.email_beta,
            name="Beta Account",
            sub="sub_beta_2",
            code="code_beta",
            access_token="ya29.mock_token_beta",
            refresh_token="1//mock_refresh_beta",
        )
        self.hub.oauth.state.register_account(mock_acc_beta)
        self.hub.set_account_ccu(self.email_beta, balance=50.0, burn_rate=0.5)

        creds_beta = {
            "access_token": "ya29.mock_token_beta",
            "refresh_token": "1//mock_refresh_beta",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Beta Account', ?, 50.0, 0.5, 10.0, 2.0, 5, 'ACTIVE')
                """,
                (self.acc_beta_id, self.email_beta, encrypt_secret(json.dumps(creds_beta))),
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t3_1_account_quota_depletion_triggers_shutdown_and_reroute(self):
        # 1. Provision Session A under Account Alpha
        req_a = SessionCreateRequest(
            name="session-alpha",
            accelerator="T4",
            shape="standard",
            lifecycle_policy="interactive",
            account_id=self.acc_alpha_id,
        )
        sess_a = session_manager.create_session(req_a)
        self.assertIsNotNone(sess_a)
        self.assertEqual(sess_a["status"], "READY")
        self.assertEqual(sess_a["account_id"], self.acc_alpha_id)
        self.assertEqual(sess_a["ssh_local_port"], 2220)
        endpoint_a = sess_a["endpoint"]

        # Confirm assignment exists in mock control plane
        self.assertIn(endpoint_a, self.hub.control_plane.state.assignments)

        # 2. Simulate CCU depletion on Account Alpha: balance drops to 1.5 (< hard_stop_ccu 2.0)
        self.hub.set_account_ccu(self.email_alpha, balance=1.5, burn_rate=2.0)
        self.hub.control_plane.state.ccu_balance = 1.5
        self.hub.control_plane.state.consumption_rate_hourly = 2.0

        # 3. Account pool sync runs for Account Alpha
        updated_alpha = account_pool.sync_account(self.acc_alpha_id)
        self.assertEqual(updated_alpha["status"], "COOLDOWN")
        self.assertEqual(updated_alpha["ccu_balance"], 1.5)

        # 4. Quota Circuit Breaker Watchdog detects Account Alpha is in COOLDOWN
        stopped_sessions = QuotaCircuitBreakerWatchdog.evaluate_and_enforce_hard_stops()
        self.assertIn(sess_a["id"], stopped_sessions)

        # Verify Session A in DB is STOPPED and SSH port 2220 is released
        sess_a_db = session_manager.get_session(sess_a["id"])
        self.assertIsNotNone(sess_a_db)
        self.assertEqual(sess_a_db["status"], "STOPPED")
        self.assertIsNone(sess_a_db["ssh_local_port"])
        self.assertIsNotNone(sess_a_db["stopped_at"])

        # Verify control plane assignment for Session A was unassigned
        self.assertNotIn(endpoint_a, self.hub.control_plane.state.assignments)

        # 5. Subsequent session allocation without explicit account:
        # Auto-selector must skip Account Alpha (COOLDOWN) and select Account Beta (ACTIVE)
        best_acc_id = account_pool.select_account(preferred_account_id=None)
        self.assertEqual(best_acc_id, self.acc_beta_id)

        # Provision Session B without specifying account_id
        req_b = SessionCreateRequest(
            name="session-beta",
            accelerator="T4",
            shape="standard",
            lifecycle_policy="interactive",
            account_id=None,
        )
        sess_b = session_manager.create_session(req_b)
        self.assertIsNotNone(sess_b)
        self.assertEqual(sess_b["status"], "READY")
        self.assertEqual(sess_b["account_id"], self.acc_beta_id)
        # Port 2220 should be recycled since Session A released it
        self.assertEqual(sess_b["ssh_local_port"], 2220)


if __name__ == "__main__":
    unittest.main()
