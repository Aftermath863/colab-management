"""Unit tests for SQLite WAL Database engine and Pydantic Domain Models."""

import os
import tempfile
import unittest

from backend.app.config import settings
from backend.app.core.database import check_db_health, get_db, init_db
from backend.app.core.models import (
    AccountCreate,
    AccountSummary,
    JobCreate,
    SecretCreate,
    SessionCreateRequest,
    SessionSummary,
    StorageProfileCreate,
    TemplateCreate,
)


class TestDatabaseAndModels(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_manager.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_database_initialization_and_health(self):
        health = check_db_health(self.db_path)
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["journal_mode"].upper(), "WAL")
        self.assertTrue(health["foreign_keys_enabled"])
        self.assertGreaterEqual(health["table_count"], 8)

        expected_tables = {
            "accounts",
            "templates",
            "secrets",
            "storage_profiles",
            "sessions",
            "jobs",
            "telemetry_samples",
            "notification_channels",
        }
        self.assertTrue(expected_tables.issubset(set(health["tables"])))

    def test_foreign_key_cascade_deletion(self):
        with get_db(self.db_path) as conn:
            # 1. Insert Account
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance)
                VALUES ('acc_1', 'user@example.com', 'enc_token', 100.0)
                """
            )
            # 2. Insert Template
            conn.execute(
                """
                INSERT INTO templates (id, name, packages_json)
                VALUES ('tmpl_1', 'Torch 2.3', '["torch"]')
                """
            )
            # 3. Insert Session linked to account and template
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, template_id, status
                ) VALUES (
                    'sess_1', 'Session 1', 'acc_1', 'hash_44_dots.......', 'ep_1',
                    'https://proxy.colab.com', 'enc_token', '2026-11-13 12:00:00',
                    'T4', 'tmpl_1', 'READY'
                )
                """
            )
            # 4. Insert Telemetry linked to session
            conn.execute(
                """
                INSERT INTO telemetry_samples (session_id, kernel_state, cpu_pct)
                VALUES ('sess_1', 'idle', 12.5)
                """
            )

        # Verify initial records
        with get_db(self.db_path) as conn:
            self.assertIsNotNone(conn.execute("SELECT id FROM sessions WHERE id = 'sess_1'").fetchone())
            self.assertIsNotNone(conn.execute("SELECT id FROM telemetry_samples WHERE session_id = 'sess_1'").fetchone())

        # Test Template Deletion (SET NULL)
        with get_db(self.db_path) as conn:
            conn.execute("DELETE FROM templates WHERE id = 'tmpl_1'")
            sess = conn.execute("SELECT template_id FROM sessions WHERE id = 'sess_1'").fetchone()
            self.assertIsNone(sess["template_id"])

        # Test Account Deletion (CASCADE to session and telemetry)
        with get_db(self.db_path) as conn:
            conn.execute("DELETE FROM accounts WHERE id = 'acc_1'")
            self.assertIsNone(conn.execute("SELECT id FROM sessions WHERE id = 'sess_1'").fetchone())
            self.assertIsNone(conn.execute("SELECT id FROM telemetry_samples WHERE session_id = 'sess_1'").fetchone())

    def test_session_create_request_high_ram_shape_normalization(self):
        # For standard accelerator (e.g. T4), shape='hm' is preserved
        t4_req = SessionCreateRequest(name="test-t4", accelerator="T4", shape="hm")
        self.assertEqual(t4_req.shape, "hm")

        # For L4 and V5E1, passing shape='hm' is normalized to 'standard' per Colab rules
        l4_req = SessionCreateRequest(name="test-l4", accelerator="L4", shape="hm")
        self.assertEqual(l4_req.shape, "standard")

        v5e1_req = SessionCreateRequest(name="test-v5e1", accelerator="V5E1", shape="hm")
        self.assertEqual(v5e1_req.shape, "standard")

    def test_pydantic_models_validation(self):
        acc = AccountSummary(
            id="acc_test",
            email="developer@example.com",
            ccu_balance=50.0,
            burn_rate_hourly=1.2,
            active_assignments=1,
            warning_ccu=10.0,
            hard_stop_ccu=2.0,
            priority=2,
            status="ACTIVE",
        )
        self.assertEqual(acc.email, "developer@example.com")

        sess = SessionSummary(
            id="sess_test",
            name="train-run-1",
            account_id="acc_test",
            notebook_hash="transformed_hash",
            endpoint="ep_123",
            proxy_url="https://proxy.colab.com",
            accelerator="A100",
            shape="hm",
            lifecycle_policy="interactive",
            idle_timeout_sec=1800,
            ssh_local_port=2220,
            workspace_subpath="",
            remote_workdir="/content",
            status="READY",
            created_at="2026-11-13 10:00:00",
        )
        self.assertEqual(sess.accelerator, "A100")
        self.assertEqual(sess.ssh_local_port, 2220)

    def test_atomic_transaction_rollback_on_error(self):
        """Verify that an exception raised inside get_db() context manager triggers rollback."""
        # Insert a baseline account
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance)
                VALUES ('acc_rollback_base', 'base@example.com', 'token', 50.0)
                """
            )

        # Attempt to insert an account and update balance, then raise RuntimeError
        with self.assertRaises(RuntimeError):
            with get_db(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance)
                    VALUES ('acc_rolled_back', 'fail@example.com', 'token', 10.0)
                    """
                )
                conn.execute("UPDATE accounts SET ccu_balance = 999.0 WHERE id = 'acc_rollback_base'")
                raise RuntimeError("Simulated transaction failure")

        # Verify that the uncommitted account was NOT saved and base account balance was not modified
        with get_db(self.db_path) as conn:
            failed_row = conn.execute("SELECT id FROM accounts WHERE id = 'acc_rolled_back'").fetchone()
            self.assertIsNone(failed_row, "Uncommitted insert must be rolled back on error")

            base_row = conn.execute("SELECT ccu_balance FROM accounts WHERE id = 'acc_rollback_base'").fetchone()
            self.assertEqual(base_row["ccu_balance"], 50.0, "Modified balance must be rolled back on error")


if __name__ == "__main__":
    unittest.main()
