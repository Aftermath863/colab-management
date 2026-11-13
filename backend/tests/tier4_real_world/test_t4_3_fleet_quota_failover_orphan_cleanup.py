"""T4.3: Fleet Quota Failover & Orphan Cleanup Workflow.

Simulates multi-account fleet quota governance:
1. Job is running on Account 1 (low balance: 2.5 CCU, hard stop: 2.0 CCU).
2. Account 1 hits hard stop (1.8 CCU) -> Status becomes COOLDOWN, session is halted.
3. Scheduler automatically triggers failover: skips Account 1 and provisions replacement session
   on Account 2 (healthy balance: 80.0 CCU), resuming and completing the job.
4. Fleet scanner discovers an unmanaged orphan web instance left running on Colab.
5. Admin performs one-click termination: instance unassigned on Colab and purged from active fleet.
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
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount
from backend.tests.tier3_pairwise.test_t3_1_quota_depletion_reroute import QuotaCircuitBreakerWatchdog


class FleetFailoverScheduler:
    """Manages job execution with multi-account quota circuit breaker failover."""

    @staticmethod
    def failover_job(job_id: str, old_session_id: str) -> Dict[str, Any]:
        # 1. Stop old session on depleted account
        session_manager.stop_session(old_session_id)

        # 2. Select next eligible active account from pool
        new_account_id = account_pool.select_account(preferred_account_id=None)

        # 3. Create replacement session on new account
        req = SessionCreateRequest(
            name=f"failover-sess-{job_id[:6]}",
            accelerator="T4",
            shape="standard",
            lifecycle_policy="ephemeral",
            account_id=new_account_id,
        )
        new_sess = session_manager.create_session(req)

        # 4. Update job to link new session
        with get_db() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET session_id = ?, status = 'RUNNING'
                WHERE id = ?
                """,
                (new_sess["id"], job_id),
            )

        return {"new_account_id": new_account_id, "new_session": new_sess}


class TestT43FleetQuotaFailoverAndOrphanCleanupWorkflow(unittest.TestCase):
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

        # 1. Account 1: Low balance near quota (2.5 CCU, hard_stop: 2.0 CCU, priority 10)
        self.acc1_id = "acc_fleet_alpha"
        self.acc1_email = "alpha.fleet@example.com"
        mock_acc1 = MockOAuthAccount(
            email=self.acc1_email,
            name="Alpha Fleet",
            sub="sub_alpha_fl",
            code="code_alpha_fl",
            access_token="ya29.token_alpha_fl",
            refresh_token="1//refresh_alpha_fl",
        )
        self.hub.oauth.state.register_account(mock_acc1)
        self.hub.set_account_ccu(self.acc1_email, balance=2.5, burn_rate=2.0)

        creds1 = {
            "access_token": "ya29.token_alpha_fl",
            "refresh_token": "1//refresh_alpha_fl",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Alpha Fleet', ?, 2.5, 2.0, 5.0, 2.0, 10, 'ACTIVE')
                """,
                (self.acc1_id, self.acc1_email, encrypt_secret(json.dumps(creds1))),
            )

        # 2. Account 2: Healthy standby balance (80.0 CCU, hard_stop: 2.0 CCU, priority 5)
        self.acc2_id = "acc_fleet_beta"
        self.acc2_email = "beta.fleet@example.com"
        mock_acc2 = MockOAuthAccount(
            email=self.acc2_email,
            name="Beta Fleet",
            sub="sub_beta_fl",
            code="code_beta_fl",
            access_token="ya29.token_beta_fl",
            refresh_token="1//refresh_beta_fl",
        )
        self.hub.oauth.state.register_account(mock_acc2)
        self.hub.set_account_ccu(self.acc2_email, balance=80.0, burn_rate=0.5)

        creds2 = {
            "access_token": "ya29.token_beta_fl",
            "refresh_token": "1//refresh_beta_fl",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Beta Fleet', ?, 80.0, 0.5, 10.0, 2.0, 5, 'ACTIVE')
                """,
                (self.acc2_id, self.acc2_email, encrypt_secret(json.dumps(creds2))),
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t4_3_fleet_failover_and_orphan_cleanup_journey(self):
        # ====================================================================
        # Phase 1: Job Provisioned on Account 1
        # ====================================================================
        sess1 = session_manager.create_session(
            SessionCreateRequest(
                name="job-sess-initial",
                accelerator="T4",
                account_id=self.acc1_id,
            )
        )
        self.assertEqual(sess1["account_id"], self.acc1_id)
        self.assertEqual(sess1["status"], "READY")
        sess1_id = sess1["id"]

        job_id = "job_failover_001"
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, name, session_id, target_accelerator, allow_fallback,
                                 job_type, entry_path_or_cmd, status, log_path)
                VALUES (?, 'bert-finetuning', ?, 'T4', 1, 'python', 'run.py', 'RUNNING', '/tmp/failover.log')
                """,
                (job_id, sess1_id),
            )

        # ====================================================================
        # Phase 2: Hard Stop Trigger & Failover to Account 2
        # ====================================================================
        # Account 1 balance drops to 1.8 <= 2.0 hard_stop_ccu
        self.hub.set_account_ccu(self.acc1_email, balance=1.8, burn_rate=2.0)
        self.hub.control_plane.state.ccu_balance = 1.8
        self.hub.control_plane.state.consumption_rate_hourly = 2.0

        # Sync Account 1 -> marked COOLDOWN
        acc1_synced = account_pool.sync_account(self.acc1_id)
        self.assertEqual(acc1_synced["status"], "COOLDOWN")
        self.assertEqual(acc1_synced["ccu_balance"], 1.8)

        # Watchdog terminates active session on Account 1
        stopped_sessions = QuotaCircuitBreakerWatchdog.evaluate_and_enforce_hard_stops()
        self.assertIn(sess1_id, stopped_sessions)

        sess1_db = session_manager.get_session(sess1_id)
        self.assertEqual(sess1_db["status"], "STOPPED")

        # Set mock control plane balance for Account 2 queries
        self.hub.control_plane.state.ccu_balance = 80.0
        self.hub.control_plane.state.consumption_rate_hourly = 0.5

        # Failover scheduler executes: auto-selects Account 2 and creates replacement session
        failover_res = FleetFailoverScheduler.failover_job(job_id=job_id, old_session_id=sess1_id)
        self.assertEqual(failover_res["new_account_id"], self.acc2_id)

        sess2 = failover_res["new_session"]
        self.assertEqual(sess2["account_id"], self.acc2_id)
        self.assertEqual(sess2["status"], "READY")

        # Job resumes and completes on Account 2
        with get_db() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'COMPLETED', exit_code = 0, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
            j_final = conn.execute("SELECT status, session_id, exit_code FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(j_final["status"], "COMPLETED")
            self.assertEqual(j_final["session_id"], sess2["id"])
            self.assertEqual(j_final["exit_code"], 0)

        # Stop completed job session
        session_manager.stop_session(sess2["id"])

        # ====================================================================
        # Phase 3: Orphan Web Instance Discovery
        # ====================================================================
        orphan_ep = "ep_orphan_web_777"
        self.hub.inject_orphan_assignment(
            endpoint=orphan_ep,
            accelerator="L4",
            email=self.acc1_email,
        )

        # Fleet scanner runs sync on Account 1
        account_pool.sync_account(self.acc1_id)

        # Invariant: Orphan instance discovered and tracked in sessions table
        orphans = session_manager.list_sessions(status_filter="ORPHAN")
        self.assertEqual(len(orphans), 1)
        orphan_record = orphans[0]
        self.assertEqual(orphan_record["endpoint"], orphan_ep)
        self.assertEqual(orphan_record["account_id"], self.acc1_id)
        self.assertEqual(orphan_record["status"], "ORPHAN")

        # ====================================================================
        # Phase 4: Admin One-Click Termination
        # ====================================================================
        orphan_id = orphan_record["id"]
        terminated = session_manager.stop_session(orphan_id)
        self.assertTrue(terminated)

        # Invariant: In DB, status is STOPPED
        orphan_stopped = session_manager.get_session(orphan_id)
        self.assertEqual(orphan_stopped["status"], "STOPPED")

        # Invariant: On mock control plane, assignment is removed
        self.assertNotIn(orphan_ep, self.hub.control_plane.state.assignments)

        # Fleet resync confirms 0 active assignments on Account 1
        acc1_resynced = account_pool.sync_account(self.acc1_id)
        self.assertEqual(acc1_resynced["active_assignments"], 0)
        self.assertEqual(len(session_manager.list_sessions(status_filter="ORPHAN")), 0)


if __name__ == "__main__":
    unittest.main()
