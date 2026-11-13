"""Integration tests for Account Pool, remote OAuth, token refresh, CCU sync, and selector."""

import base64
import hashlib
import json
import os
import tempfile
import time
import unittest
import urllib.parse

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.security import decrypt_secret, encrypt_secret
from backend.app.services import account_pool
from backend.app.services.colab_client import ColabClient
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestAccountPool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = MockControlHub()
        cls.hub.start()

        # Route platform endpoints to mock servers
        cls.orig_token_url = settings.OAUTH_TOKEN_URL
        cls.orig_userinfo_url = settings.OAUTH_USERINFO_URL
        cls.orig_control_url = settings.CONTROL_PLANE_URL

        settings.OAUTH_TOKEN_URL = cls.hub.oauth.token_uri
        settings.OAUTH_USERINFO_URL = cls.hub.oauth.userinfo_uri
        settings.CONTROL_PLANE_URL = cls.hub.urls["control_plane"]

    @classmethod
    def tearDownClass(cls):
        settings.OAUTH_TOKEN_URL = cls.orig_token_url
        settings.OAUTH_USERINFO_URL = cls.orig_userinfo_url
        settings.CONTROL_PLANE_URL = cls.orig_control_url
        cls.hub.stop()

    def setUp(self):
        self.hub.reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_manager.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_get_oauth_url(self):
        url = account_pool.get_oauth_url()
        self.assertTrue(url.startswith(settings.OAUTH_AUTH_URL))
        self.assertIn("client_id=", url)
        self.assertIn("response_type=code", url)
        self.assertIn("token_usage=remote", url)
        self.assertIn("code_challenge=", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("state=", url)

    def test_pkce_flow_and_redirect_url_code_extraction(self):
        auth_url, state = account_pool.start_oauth_flow()
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(auth_url).query))
        self.assertEqual(qs.get("state"), state)
        self.assertEqual(qs.get("code_challenge_method"), "S256")
        expected_challenge = qs.get("code_challenge")
        self.assertTrue(expected_challenge)

        code = "4/0A_pkce_test_code"
        email = "pkce.user@gmail.com"
        mock_acc = MockOAuthAccount(
            email=email,
            name="PKCE User",
            sub="sub_pkce_123",
            code=code,
            access_token="ya29.mock_token_pkce",
            refresh_token="1//mock_refresh_pkce",
        )
        self.hub.oauth.state.register_account(mock_acc)

        pasted_redirect = (
            f"https://sdk.cloud.google.com/applicationdefaultauthcode.html"
            f"?state={state}&code={code}"
        )
        account = account_pool.exchange_code(pasted_redirect)
        self.assertEqual(account["email"], email)

        token_reqs = [
            r for r in self.hub.oauth.state.request_history
            if r.get("path") == "/token" and r.get("params", {}).get("code") == code
        ]
        self.assertTrue(token_reqs, "Expected /token request recorded in mock OAuth history")
        sent_verifier = token_reqs[-1].get("params", {}).get("code_verifier")
        self.assertTrue(sent_verifier, "code_verifier must be forwarded on token exchange")
        computed_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(sent_verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        self.assertEqual(computed_challenge, expected_challenge)

    def test_exchange_code_and_account_registration(self):
        # Register an account in mock OAuth server
        code = "4/0A_custom_test_code"
        email = "developer.pro@gmail.com"
        mock_acc = MockOAuthAccount(
            email=email,
            name="Developer Pro",
            sub="sub_dev_123",
            code=code,
            access_token="ya29.mock_token_dev",
            refresh_token="1//mock_refresh_dev",
        )
        self.hub.oauth.state.register_account(mock_acc)

        account = account_pool.exchange_code(code)
        self.assertIsNotNone(account)
        self.assertEqual(account["email"], email)
        self.assertEqual(account["display_name"], "Developer Pro")
        self.assertEqual(account["status"], "ACTIVE")

        # Verify credentials in database are encrypted
        with get_db() as conn:
            row = conn.execute("SELECT oauth_creds_enc FROM accounts WHERE email = ?", (email,)).fetchone()
            self.assertIsNotNone(row)
            decrypted = decrypt_secret(row["oauth_creds_enc"])
            creds = json.loads(decrypted)
            self.assertEqual(creds["access_token"], "ya29.mock_token_dev")
            self.assertEqual(creds["refresh_token"], "1//mock_refresh_dev")

    def test_get_valid_access_token_and_auto_refresh(self):
        # Insert account with expired token
        email = "refresh.user@gmail.com"
        account_id = "acc_refresh_1"
        now = int(time.time())

        # Register refresh account in mock OAuth
        mock_acc = MockOAuthAccount(
            email=email,
            name="Refresh User",
            sub="sub_ref_123",
            code="code_ref",
            access_token="ya29.old_token",
            refresh_token="1//valid_refresh_token",
        )
        self.hub.oauth.state.register_account(mock_acc)

        expired_creds = {
            "access_token": "ya29.expired_token",
            "refresh_token": "1//valid_refresh_token",
            "expires_at": now - 100,  # Expired
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance)
                VALUES (?, ?, ?, 50.0)
                """,
                (account_id, email, encrypt_secret(json.dumps(expired_creds))),
            )

        # Calling get_valid_access_token should trigger auto-refresh
        valid_token = account_pool.get_valid_access_token(account_id)
        self.assertTrue(valid_token.startswith("ya29."))
        self.assertNotEqual(valid_token, "ya29.expired_token")

    def test_sync_account_and_orphan_discovery(self):
        # 1. Register and exchange account
        code = "4/0A_sync_code"
        email = "sync.user@gmail.com"
        mock_acc = MockOAuthAccount(
            email=email,
            name="Sync User",
            sub="sub_sync_123",
            code=code,
            access_token="ya29.mock_token_sync",
            refresh_token="1//mock_refresh_sync",
        )
        self.hub.oauth.state.register_account(mock_acc)
        account = account_pool.exchange_code(code)
        account_id = account["id"]

        # 2. Provision an external assignment on mock control plane (creating an orphan)
        colab = ColabClient(base_url=self.hub.urls["control_plane"])
        orphan_assign = colab.assign(
            access_token="ya29.mock_token_sync",
            notebook_hash="external_notebook_hash..........",
            accelerator="T4",
        )
        orphan_ep = orphan_assign["endpoint"]

        # 3. Trigger account sync
        synced = account_pool.sync_account(account_id, client=colab)
        self.assertEqual(synced["active_assignments"], 1)

        # 4. Verify orphan instance was recorded in local sessions table
        with get_db() as conn:
            orphan_row = conn.execute(
                "SELECT * FROM sessions WHERE endpoint = ?", (orphan_ep,)
            ).fetchone()
            self.assertIsNotNone(orphan_row)
            self.assertEqual(orphan_row["status"], "ORPHAN")
            self.assertEqual(orphan_row["account_id"], account_id)

    def test_account_auto_selector_logic(self):
        with get_db() as conn:
            # Account A: Priority 1, Balance 100 CCU, Active 0 -> Score: 1000 + 1000 - 0 = 2000
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, priority, active_assignments, status)
                VALUES ('acc_a', 'a@colab.com', 'enc', 100.0, 1, 0, 'ACTIVE')
                """
            )
            # Account B: Priority 2, Balance 50 CCU, Active 0 -> Score: 2000 + 500 - 0 = 2500 (Highest)
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, priority, active_assignments, status)
                VALUES ('acc_b', 'b@colab.com', 'enc', 50.0, 2, 0, 'ACTIVE')
                """
            )
            # Account C: Balance <= hard_stop_ccu (COOLDOWN) -> Ineligible
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, hard_stop_ccu, priority, status)
                VALUES ('acc_c', 'c@colab.com', 'enc', 1.0, 2.0, 3, 'COOLDOWN')
                """
            )

        # Selected should be Account B (highest priority score)
        best = account_pool.select_account()
        self.assertEqual(best, "acc_b")

        # Preferred account selection
        pref = account_pool.select_account(preferred_account_id="acc_a")
        self.assertEqual(pref, "acc_a")

        # Selecting COOLDOWN account raises error
        with self.assertRaises(ValueError):
            account_pool.select_account(preferred_account_id="acc_c")

    def test_delete_account(self):
        with get_db() as conn:
            conn.execute(
                "INSERT INTO accounts (id, email, oauth_creds_enc) VALUES ('acc_del', 'del@colab.com', 'enc')"
            )
        self.assertTrue(account_pool.delete_account("acc_del"))
        self.assertIsNone(account_pool.get_account("acc_del"))


if __name__ == "__main__":
    unittest.main()
