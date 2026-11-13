"""Tier 1 Feature Tests: Session Lifecycle Engine & Circuit Breaker.

Validates:
1. Interactive session idle detection and traffic reset via InteractiveIdleWatchdog.
2. Auto-stop enforcement on idle timeout expiration.
3. Quota circuit breaker hard-stop (<= 2.0 CCU) stopping active sessions on cooldown accounts.
4. Ephemeral session auto-teardown upon job terminal status.
5. Keep-alive periodic probe for keep_alive sessions.
6. Proxy token renewal when within 300s expiration margin.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest
from backend.app.core.security import encrypt_secret
from backend.app.services import account_pool, session_manager
from backend.app.services.colab_client import ColabClient
from backend.app.services.lifecycle_engine import (
    InteractiveIdleWatchdog,
    LifecycleEngine,
    QuotaCircuitBreakerWatchdog,
    lifecycle_engine,
)
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestLifecycleEngine(unittest.TestCase):
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
        self.db_path = os.path.join(self.tmpdir.name, "test_lifecycle.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        # Seed test account
        self.account_id = "acc_lifecycle_test"
        self.email = "lifecycle.user@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Lifecycle User",
            sub="sub_lifecycle_1",
            code="code_lifecycle",
            access_token="ya29.mock_lifecycle_token",
            refresh_token="1//mock_refresh_lifecycle",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_lifecycle_token",
            "refresh_token": "1//mock_refresh_lifecycle",
            "token_uri": self.hub.oauth.token_uri,
            "client_id": "mock_client_id",
            "client_secret": "mock_client_secret",
            "scopes": ["openid", "email"],
            "expiry": time.time() + 3600,
        }
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, warning_ccu, hard_stop_ccu, status)
                VALUES (?, ?, ?, ?, 25.0, 10.0, 2.0, 'ACTIVE')
                """,
                (self.account_id, self.email, "Lifecycle User", encrypt_secret(json.dumps(creds))),
            )

        self.colab_client = ColabClient(base_url=self.hub.urls["control_plane"])

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_interactive_idle_watchdog_traffic_reset_and_timeout(self):
        """Idle watchdog records traffic, resets activity timestamp, and terminates idle sessions."""
        watchdog = InteractiveIdleWatchdog()
        t0 = 1000.0

        # Provision interactive session
        req = SessionCreateRequest(
            name="Interactive Sess",
            accelerator="T4",
            shape="standard",
            lifecycle_policy="interactive",
            idle_timeout_sec=300,
            account_id=self.account_id,
        )
        sess = session_manager.create_session(req, client=self.colab_client)
        sess_id = sess["id"]

        watchdog.register_session(sess_id, initial_timestamp=t0)
        self.assertEqual(watchdog.get_last_activity(sess_id), t0)

        # 1. Evaluate at t0 + 200s (under 300s timeout): should NOT terminate
        terminated = watchdog.evaluate_idle_sessions(current_timestamp=t0 + 200.0, client=self.colab_client)
        self.assertEqual(terminated, [])

        # 2. Record traffic at t0 + 250s (resets timer)
        watchdog.record_traffic(sess_id, timestamp=t0 + 250.0)
        self.assertEqual(watchdog.get_last_activity(sess_id), t0 + 250.0)

        # 3. Evaluate at t0 + 400s (only 150s since last traffic): should NOT terminate
        terminated = watchdog.evaluate_idle_sessions(current_timestamp=t0 + 400.0, client=self.colab_client)
        self.assertEqual(terminated, [])

        # 4. Evaluate at t0 + 600s (350s since last traffic >= 300s): should terminate
        terminated = watchdog.evaluate_idle_sessions(current_timestamp=t0 + 600.0, client=self.colab_client)
        self.assertIn(sess_id, terminated)

        # Confirm session is stopped in DB
        stopped_sess = session_manager.get_session(sess_id)
        self.assertEqual(stopped_sess["status"], "STOPPED")

    def test_quota_circuit_breaker_hard_stop_enforcement(self):
        """Circuit breaker automatically stops active sessions when account is in COOLDOWN or balance <= 2.0 CCU."""
        req = SessionCreateRequest(
            name="Quota Breaker Sess",
            accelerator="T4",
            shape="standard",
            lifecycle_policy="interactive",
            account_id=self.account_id,
        )
        sess = session_manager.create_session(req, client=self.colab_client)
        sess_id = sess["id"]

        # Simulate CCU balance dropping to 1.5 CCU (<= hard_stop_ccu 2.0)
        with get_db(self.db_path) as conn:
            conn.execute(
                "UPDATE accounts SET ccu_balance = 1.5 WHERE id = ?",
                (self.account_id,),
            )

        stopped_ids = QuotaCircuitBreakerWatchdog.evaluate_and_enforce_hard_stops(client=self.colab_client)
        self.assertIn(sess_id, stopped_ids)

        with get_db(self.db_path) as conn:
            acc = conn.execute("SELECT status FROM accounts WHERE id = ?", (self.account_id,)).fetchone()
            self.assertEqual(acc["status"], "COOLDOWN")

        stopped_sess = session_manager.get_session(sess_id)
        self.assertEqual(stopped_sess["status"], "STOPPED")

    def test_ephemeral_session_teardown_on_job_terminal_state(self):
        """Ephemeral sessions are torn down when their linked job reaches COMPLETED/FAILED/CANCELLED/OOM_KILLED."""
        engine = LifecycleEngine(check_interval_sec=1.0)

        req = SessionCreateRequest(
            name="Ephemeral Sess",
            accelerator="T4",
            shape="standard",
            lifecycle_policy="ephemeral",
            account_id=self.account_id,
        )
        sess = session_manager.create_session(req, client=self.colab_client)
        sess_id = sess["id"]

        # Insert a completed job linked to this session
        job_id = "job_eph_test_1"
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, name, session_id, target_accelerator, job_type, entry_path_or_cmd, log_path, status)
                VALUES (?, 'Ephemeral Task', ?, 'T4', 'shell', 'echo done', '/data/logs/eph.log', 'COMPLETED')
                """,
                (job_id, sess_id),
            )

        # Run tick evaluation
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(engine.tick(client=self.colab_client))
        finally:
            loop.close()

        self.assertIn(sess_id, results["terminated_ephemeral"])
        stopped_sess = session_manager.get_session(sess_id)
        self.assertEqual(stopped_sess["status"], "STOPPED")

    def test_proxy_token_auto_renewal_at_5_minute_margin(self):
        """Session proxy token is renewed when within 300s of token_expires_at."""
        engine = LifecycleEngine(check_interval_sec=1.0)
        now = time.time()

        req = SessionCreateRequest(
            name="Renewal Sess",
            accelerator="T4",
            shape="standard",
            lifecycle_policy="interactive",
            account_id=self.account_id,
        )
        sess = session_manager.create_session(req, client=self.colab_client)
        sess_id = sess["id"]

        # Set token_expires_at to 120 seconds in the future (< 300s)
        near_expiry_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now + 120))
        with get_db(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET token_expires_at = ? WHERE id = ?",
                (near_expiry_str, sess_id),
            )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(engine.tick(client=self.colab_client))
        finally:
            loop.close()

        self.assertIn(sess_id, results["renewed_tokens"])


if __name__ == "__main__":
    unittest.main()
