"""Tier 2 Boundary Tests: Quota & Multi-Account Boundaries (T2.2).

Validates:
1. Account CCU balance 0.00 transitions status to COOLDOWN, excluded by select_account.
2. Exact boundary condition at balance == hard_stop_ccu triggers COOLDOWN; balance > hard_stop is ACTIVE.
3. Refresh token revocation (invalid_grant) transitions status to AUTH_EXPIRED and raises ValueError.
4. Duplicate Google email collision handling (IntegrityError on direct insert, smooth update via exchange_code).
5. Burn rate spike (> 10 CCU/hr) recording and metric integrity.
6. Fleet orphan instance reconciliation discovers web-created instances and marks them ORPHAN.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from unittest.mock import MagicMock, patch
import unittest

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.security import decrypt_secret, encrypt_secret
from backend.app.services import account_pool
from backend.app.services.colab_client import ColabClient


class TestQuotaAccountsBoundaries(unittest.TestCase):
    """Tier 2 Boundary tests for Quota, Accounts, and Pool Scheduling."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="t2_quota_")
        self.db_path = os.path.join(self.test_dir, "quota_boundary.db")
        init_db(self.db_path)
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_account(
        self,
        account_id: str = "acc_t2_test",
        email: str = "t2_user@example.com",
        ccu_balance: float = 50.0,
        hard_stop_ccu: float = 2.0,
        status: str = "ACTIVE",
        priority: int = 1,
    ):
        creds = {
            "access_token": "mock_access_token_123",
            "refresh_token": "mock_refresh_token_123",
            "expires_at": int(time.time()) + 3600,
        }
        enc_creds = encrypt_secret(json.dumps(creds))
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                    active_assignments, warning_ccu, hard_stop_ccu, priority, status
                ) VALUES (?, ?, 'Test User', ?, ?, 0.0, 0, 10.0, ?, ?, ?)
                """,
                (account_id, email, enc_creds, ccu_balance, hard_stop_ccu, priority, status),
            )

    def test_account_ccu_zero_balance_transitions_to_cooldown(self):
        """When an account's CCU balance drops to 0.00, sync_account marks it COOLDOWN and select_account excludes it."""
        self._create_account(account_id="acc_zero", ccu_balance=15.0, hard_stop_ccu=2.0, status="ACTIVE")

        mock_colab = MagicMock(spec=ColabClient)
        mock_colab.get_ccu_info.return_value = {
            "current_balance": 0.0,
            "consumption_rate_hourly": 0.0,
            "assignments_count": 0,
        }
        mock_colab.list_assignments.return_value = []

        # Sync account
        acc = account_pool.sync_account("acc_zero", client=mock_colab)
        self.assertEqual(acc["ccu_balance"], 0.0)
        self.assertEqual(acc["status"], "COOLDOWN")

        # Verify DB persisted state
        with get_db(self.db_path) as conn:
            row = conn.execute("SELECT status, ccu_balance FROM accounts WHERE id = 'acc_zero'").fetchone()
            self.assertEqual(row["status"], "COOLDOWN")
            self.assertEqual(row["ccu_balance"], 0.0)

        # select_account should fail because no accounts have ccu > hard_stop
        with self.assertRaises(ValueError) as ctx:
            account_pool.select_account()
        self.assertIn("No eligible active accounts", str(ctx.exception))

    def test_exact_hard_stop_ccu_boundary_condition(self):
        """At exact boundary balance == hard_stop_ccu, account enters COOLDOWN; only > hard_stop is ACTIVE."""
        self._create_account(account_id="acc_boundary", ccu_balance=10.0, hard_stop_ccu=2.0, status="ACTIVE")

        mock_colab = MagicMock(spec=ColabClient)
        mock_colab.list_assignments.return_value = []

        # 1. Exactly at boundary: 2.0 == 2.0
        mock_colab.get_ccu_info.return_value = {
            "current_balance": 2.0,
            "consumption_rate_hourly": 0.5,
            "assignments_count": 0,
        }
        acc = account_pool.sync_account("acc_boundary", client=mock_colab)
        self.assertEqual(acc["status"], "COOLDOWN")

        with self.assertRaises(ValueError):
            account_pool.select_account(preferred_account_id="acc_boundary")

        # 2. Just above boundary: 2.01 > 2.0
        mock_colab.get_ccu_info.return_value = {
            "current_balance": 2.01,
            "consumption_rate_hourly": 0.5,
            "assignments_count": 0,
        }
        acc2 = account_pool.sync_account("acc_boundary", client=mock_colab)
        self.assertEqual(acc2["status"], "ACTIVE")

        selected = account_pool.select_account(preferred_account_id="acc_boundary")
        self.assertEqual(selected, "acc_boundary")

    def test_external_refresh_token_revocation_invalid_grant(self):
        """When OAuth token refresh returns HTTP 400 invalid_grant, account transitions to AUTH_EXPIRED."""
        # Create account with expired access token (expires_at in past)
        expired_creds = {
            "access_token": "expired_access_token",
            "refresh_token": "revoked_refresh_token",
            "expires_at": int(time.time()) - 500,
        }
        enc_creds = encrypt_secret(json.dumps(expired_creds))
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, status)
                VALUES ('acc_revoked', 'revoked@example.com', 'Revoked', ?, 50.0, 'ACTIVE')
                """,
                (enc_creds,),
            )

        # Mock requests.post returning HTTP 400 invalid_grant
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 400
        mock_resp.text = json.dumps({"error": "invalid_grant", "error_description": "Token has been expired or revoked."})

        with patch("backend.app.services.account_pool.requests.post", return_value=mock_resp):
            with self.assertRaises(ValueError) as ctx:
                account_pool.get_valid_access_token("acc_revoked")
            self.assertIn("OAuth refresh token expired or revoked", str(ctx.exception))

        # Verify DB status transitioned to AUTH_EXPIRED
        with get_db(self.db_path) as conn:
            row = conn.execute("SELECT status FROM accounts WHERE id = 'acc_revoked'").fetchone()
            self.assertEqual(row["status"], "AUTH_EXPIRED")

    def test_duplicate_google_email_collision_handling(self):
        """Inserting duplicate email fails with IntegrityError, while exchange_code updates existing record."""
        self._create_account(account_id="acc_existing", email="collision@example.com", ccu_balance=25.0)

        # Direct insert with same email must fail with IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            with get_db(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance)
                    VALUES ('acc_duplicate', 'collision@example.com', 'dummy', 10.0)
                    """
                )

        # exchange_code for existing email must smoothly update
        mock_token_resp = MagicMock()
        mock_token_resp.ok = True
        mock_token_resp.json.return_value = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600,
        }

        mock_userinfo_resp = MagicMock()
        mock_userinfo_resp.ok = True
        mock_userinfo_resp.json.return_value = {
            "email": "collision@example.com",
            "name": "Updated Collision User",
        }

        with patch("backend.app.services.account_pool.requests.post", return_value=mock_token_resp), \
             patch("backend.app.services.account_pool.requests.get", return_value=mock_userinfo_resp), \
             patch("backend.app.services.account_pool.sync_account"):
            updated_acc = account_pool.exchange_code("mock_auth_code_xyz")
            self.assertEqual(updated_acc["id"], "acc_existing")
            self.assertEqual(updated_acc["display_name"], "Updated Collision User")

    def test_burn_rate_spike_recording_and_alert(self):
        """Consumption rates > 10 CCU/hr are recorded with float precision without crashing accounting metrics."""
        self._create_account(account_id="acc_spike", ccu_balance=100.0)

        mock_colab = MagicMock(spec=ColabClient)
        mock_colab.get_ccu_info.return_value = {
            "current_balance": 82.5,
            "consumption_rate_hourly": 17.55,  # Heavy spike
            "assignments_count": 2,
        }
        mock_colab.list_assignments.return_value = []

        acc = account_pool.sync_account("acc_spike", client=mock_colab)
        self.assertEqual(acc["burn_rate_hourly"], 17.55)
        self.assertEqual(acc["ccu_balance"], 82.5)
        self.assertEqual(acc["active_assignments"], 0)

        with get_db(self.db_path) as conn:
            row = conn.execute("SELECT burn_rate_hourly, ccu_balance FROM accounts WHERE id = 'acc_spike'").fetchone()
            self.assertAlmostEqual(row["burn_rate_hourly"], 17.55, places=2)

    def test_fleet_orphan_instance_reconciliation(self):
        """sync_account discovers assignments from Google not in local sessions and registers them as ORPHAN."""
        self._create_account(account_id="acc_orphan_source", ccu_balance=50.0)

        # Google returns an assignment not in local DB
        external_assignment = {
            "endpoint": "ep_web_orphan_999",
            "accelerator": "A100",
            "variant": "GPU",
            "machineShape": "hm",
            "runtimeProxyInfo": {
                "token": "orphan_proxy_secret_token",
                "tokenExpiresInSeconds": 7200,
                "url": "https://colab-proxy-orphan.google.com",
            },
        }

        mock_colab = MagicMock(spec=ColabClient)
        mock_colab.get_ccu_info.return_value = {
            "current_balance": 45.0,
            "consumption_rate_hourly": 2.5,
            "assignments_count": 1,
        }
        mock_colab.list_assignments.return_value = [external_assignment]

        account_pool.sync_account("acc_orphan_source", client=mock_colab)

        # Verify an ORPHAN session was registered in local database
        with get_db(self.db_path) as conn:
            orphan_row = conn.execute(
                "SELECT id, name, account_id, endpoint, proxy_url, proxy_token_enc, accelerator, status FROM sessions WHERE endpoint = 'ep_web_orphan_999'"
            ).fetchone()

            self.assertIsNotNone(orphan_row, "Orphan assignment must be registered in sessions table")
            self.assertEqual(orphan_row["status"], "ORPHAN")
            self.assertEqual(orphan_row["account_id"], "acc_orphan_source")
            self.assertEqual(orphan_row["accelerator"], "A100")
            self.assertEqual(orphan_row["proxy_url"], "https://colab-proxy-orphan.google.com")

            # Verify proxy token was securely encrypted
            decrypted_proxy_token = decrypt_secret(orphan_row["proxy_token_enc"])
            self.assertEqual(decrypted_proxy_token, "orphan_proxy_secret_token")


if __name__ == "__main__":
    unittest.main()
