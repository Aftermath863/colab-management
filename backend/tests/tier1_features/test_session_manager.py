"""Integration tests for Session Provisioning, SSH port pool, and lifecycle teardown."""

import json
import os
import tempfile
import unittest

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest
from backend.app.core.security import encrypt_secret
from backend.app.services import session_manager
from backend.app.services.colab_client import ColabClient
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestSessionManager(unittest.TestCase):
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

        # Register and insert a default active account
        self.account_id = "acc_session_test"
        self.email = "compute.engineer@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Compute Engineer",
            sub="sub_comp_123",
            code="code_comp",
            access_token="ya29.mock_compute_token",
            refresh_token="1//mock_refresh_compute",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_compute_token",
            "refresh_token": "1//mock_refresh_compute",
            "expires_at": 9999999999,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, priority, status)
                VALUES (?, ?, 'Compute Engineer', ?, 100.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_ssh_port_pool_allocation(self):
        # Initial allocation should be first port in pool: 2220
        p1 = session_manager.allocate_ssh_port()
        self.assertEqual(p1, 2220)

        # Simulate sessions occupying ports 2220 and 2221
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc, token_expires_at, accelerator, ssh_local_port, status)
                VALUES ('s1', 'n1', ?, 'h1', 'e1', 'u1', 't1', '2026-11-13', 'T4', 2220, 'READY'),
                       ('s2', 'n2', ?, 'h2', 'e2', 'u2', 't2', '2026-11-13', 'T4', 2221, 'READY')
                """,
                (self.account_id, self.account_id),
            )

        # Next allocation should be 2222
        p2 = session_manager.allocate_ssh_port()
        self.assertEqual(p2, 2222)

        # If session s1 is STOPPED, port 2220 becomes free again
        with get_db() as conn:
            conn.execute("UPDATE sessions SET status = 'STOPPED', ssh_local_port = NULL WHERE id = 's1'")

        p_reused = session_manager.allocate_ssh_port()
        self.assertEqual(p_reused, 2220)

    def test_create_and_stop_session_lifecycle(self):
        colab = ColabClient(base_url=self.hub.urls["control_plane"])
        req = SessionCreateRequest(
            name="model-finetune-lora",
            accelerator="T4",
            shape="standard",
            lifecycle_policy="interactive",
            account_id=self.account_id,
        )

        # 1. Provision session
        session = session_manager.create_session(req, client=colab)
        self.assertIsNotNone(session)
        session_id = session["id"]
        self.assertEqual(session["name"], "model-finetune-lora")
        self.assertEqual(session["status"], "READY")
        self.assertEqual(session["ssh_local_port"], 2220)
        self.assertIn("colab.research.google.com", session["colab_web_url"])
        self.assertEqual(len(session["notebook_hash"]), 44)

        # Verify account active_assignments incremented to 1
        with get_db() as conn:
            acc = conn.execute("SELECT active_assignments FROM accounts WHERE id = ?", (self.account_id,)).fetchone()
            self.assertEqual(acc["active_assignments"], 1)

        # 2. Check internal record with decrypted proxy token
        internal = session_manager.get_session_internal(session_id)
        self.assertIsNotNone(internal)
        self.assertTrue(len(internal["proxy_token"]) > 0)

        # 3. List sessions
        sessions_list = session_manager.list_sessions()
        self.assertEqual(len(sessions_list), 1)
        self.assertEqual(sessions_list[0]["id"], session_id)

        # 4. Stop session
        stopped = session_manager.stop_session(session_id, client=colab)
        self.assertTrue(stopped)

        # 5. Verify stopped state in DB
        stopped_sess = session_manager.get_session(session_id)
        self.assertEqual(stopped_sess["status"], "STOPPED")
        self.assertIsNone(stopped_sess["ssh_local_port"])
        self.assertIsNotNone(stopped_sess["stopped_at"])

        # Verify account active_assignments decremented back to 0
        with get_db() as conn:
            acc = conn.execute("SELECT active_assignments FROM accounts WHERE id = ?", (self.account_id,)).fetchone()
            self.assertEqual(acc["active_assignments"], 0)

    def test_ssh_port_pool_exhaustion_returns_none(self):
        """Verify allocate_ssh_port returns None gracefully when all 10 ports (2220-2229) are allocated."""
        # Occupy all 10 ports in pool (2220 through 2229)
        with get_db() as conn:
            for i, port in enumerate(settings.ssh_port_pool):
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator, ssh_local_port, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-11-13', 'T4', ?, 'READY')
                    """,
                    (f"sess_pool_{i}", f"name_{i}", self.account_id, f"hash_{i}", f"ep_{i}", "url", "tok", port),
                )

        exhausted_port = session_manager.allocate_ssh_port()
        self.assertIsNone(exhausted_port, "Must return None when SSH port pool is exhausted")



if __name__ == "__main__":
    unittest.main()
