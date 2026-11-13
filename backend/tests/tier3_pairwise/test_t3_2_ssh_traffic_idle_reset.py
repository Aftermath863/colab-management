"""T3.2: Active SSH Traffic x Idle Timeout Reset.

Validates that active network traffic passing through the SSH multiplexer gateway or TTY bridge
resets the session idle timer, preventing premature auto-idle termination of busy interactive sessions.
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


class InteractiveIdleWatchdog:
    """Watchdog that monitors interactive sessions and enforces idle timeouts.

    Maintains activity timestamps for sessions and resets the timer upon traffic events.
    """

    def __init__(self):
        self._last_activity: Dict[str, float] = {}

    def register_session(self, session_id: str, initial_timestamp: float) -> None:
        self._last_activity[session_id] = initial_timestamp

    def record_traffic(self, session_id: str, timestamp: float) -> None:
        """Called by SSH gateway or TTY bridge when bytes/packets are transferred."""
        self._last_activity[session_id] = timestamp

    def get_last_activity(self, session_id: str) -> Optional[float]:
        return self._last_activity.get(session_id)

    def evaluate_idle_sessions(
        self, current_timestamp: float, client: Optional[ColabClient] = None
    ) -> List[str]:
        """Check all active interactive sessions and terminate those exceeding idle_timeout_sec."""
        terminated = []
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, lifecycle_policy, idle_timeout_sec, status
                FROM sessions
                WHERE lifecycle_policy = 'interactive'
                  AND status = 'READY'
                """
            ).fetchall()

        for row in rows:
            sess_id = row["id"]
            timeout_sec = row["idle_timeout_sec"]
            last_active = self._last_activity.get(sess_id, current_timestamp)
            idle_duration = current_timestamp - last_active

            if idle_duration >= timeout_sec:
                session_manager.stop_session(sess_id, client=client)
                terminated.append(sess_id)

        return terminated


class TestT32ActiveSSHTrafficIdleReset(unittest.TestCase):
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

        # Register active test account
        self.account_id = "acc_ssh_idle_test"
        self.email = "idle_dev@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Idle Dev",
            sub="sub_idle_123",
            code="code_idle",
            access_token="ya29.mock_token_idle",
            refresh_token="1//mock_refresh_idle",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_token_idle",
            "refresh_token": "1//mock_refresh_idle",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Idle Dev', ?, 50.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        self.watchdog = InteractiveIdleWatchdog()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t3_2_active_ssh_traffic_resets_idle_timer(self):
        # 1. Provision interactive session with 60-second idle timeout
        req = SessionCreateRequest(
            name="interactive-dev-session",
            accelerator="T4",
            shape="standard",
            lifecycle_policy="interactive",
            idle_timeout_sec=60,
            account_id=self.account_id,
        )
        sess = session_manager.create_session(req)
        self.assertEqual(sess["status"], "READY")
        self.assertEqual(sess["idle_timeout_sec"], 60)
        session_id = sess["id"]

        # Base timestamp T0
        t0 = 1000.0
        self.watchdog.register_session(session_id, initial_timestamp=t0)

        # 2. Check at T0 + 40s (40s < 60s timeout): session remains READY
        terminated = self.watchdog.evaluate_idle_sessions(current_timestamp=t0 + 40.0)
        self.assertEqual(terminated, [])
        sess_db = session_manager.get_session(session_id)
        self.assertEqual(sess_db["status"], "READY")

        # 3. At T0 + 50s, incoming SSH traffic / VS Code remote commands pass through gateway
        # Gateway records traffic event, updating last_activity_at to T0 + 50s
        self.watchdog.record_traffic(session_id, timestamp=t0 + 50.0)
        self.assertEqual(self.watchdog.get_last_activity(session_id), t0 + 50.0)

        # 4. Check at T0 + 70s:
        # Elapsed since T0 is 70s (>60s), but elapsed since last traffic is 20s (<60s)
        # Invariant: session must NOT be terminated because idle timer was reset!
        terminated = self.watchdog.evaluate_idle_sessions(current_timestamp=t0 + 70.0)
        self.assertEqual(terminated, [])
        sess_db = session_manager.get_session(session_id)
        self.assertEqual(sess_db["status"], "READY")

        # 5. Check at T0 + 90s:
        # Elapsed since last traffic is 40s (<60s) -> still READY
        terminated = self.watchdog.evaluate_idle_sessions(current_timestamp=t0 + 90.0)
        self.assertEqual(terminated, [])
        sess_db = session_manager.get_session(session_id)
        self.assertEqual(sess_db["status"], "READY")

        # 6. Advance time to T0 + 120s with zero additional traffic:
        # Elapsed since last traffic (T0 + 50s) is 70s (> 60s timeout)
        # Watchdog must now detect inactivity and gracefully teardown the session
        terminated = self.watchdog.evaluate_idle_sessions(current_timestamp=t0 + 120.0)
        self.assertIn(session_id, terminated)

        # Invariant: session transitions to STOPPED and port is released
        sess_stopped = session_manager.get_session(session_id)
        self.assertEqual(sess_stopped["status"], "STOPPED")
        self.assertIsNone(sess_stopped["ssh_local_port"])
        self.assertIsNotNone(sess_stopped["stopped_at"])


if __name__ == "__main__":
    unittest.main()
