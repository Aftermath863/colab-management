"""Milestone 1 Phase 1 Empirical Stress Test Suite.

Independent verification suite written by teamwork_challenger_m1_1.
Tests:
1. Port 1113 strict compliance & legacy port eradication across entire repo
2. AES-256-GCM Vault tamper detection, key derivation, permissions, and edge cases
3. Database WAL mode, foreign keys enforcement, cascade delete, and set-null
4. JWT authentication stress tests (expired tokens, invalid signatures, malformed headers)
5. Colab HTTP 400 (QuotaExceededError) & HTTP 412 (TooManyAssignmentsError) error handling
"""

import base64
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.database import (
    check_db_health,
    get_db,
    get_db_connection,
    init_db,
)
from backend.app.core.security import (
    AuthenticationError,
    DecryptionError,
    create_access_token,
    decode_access_token,
    decrypt_secret,
    encrypt_secret,
    get_master_key,
    reset_master_key_cache,
)
from backend.app.main import app
from backend.app.services.colab_client import (
    HIGH_MEM_ONLY_ACCELERATORS,
    ColabClient,
    ColabClientError,
    QuotaExceededError,
    TooManyAssignmentsError,
    strip_xssi_prefix,
    uuid_to_web_safe_base64,
)


class TestPort1113ComplianceAndEradication(unittest.TestCase):
    """Verify strict port 1113 usage and complete absence of legacy port in repo."""

    def test_settings_port_default_is_1113(self):
        """Verify default settings port is 1113."""
        self.assertEqual(settings.PORT, 1113)

    def test_healthcheck_returns_port_1113(self):
        """Verify GET /healthz reports port 1113."""
        client = TestClient(app)
        resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("port"), 1113)
        self.assertEqual(data.get("status"), "ok")

    def test_zero_occurrences_of_legacy_port_in_codebase(self):
        """Verify that legacy port has zero occurrences outside of .agents directory and this test."""
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        legacy_target = "80" + "80"
        cmd = ["git", "grep", "--untracked", legacy_target, "--", ":!.agents", ":!backend/tests/test_m1_challenger_stress.py"]
        result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
        # git grep returns 1 when no matches are found
        self.assertEqual(
            result.returncode,
            1,
            f"Found legacy port occurrences outside .agents:\n{result.stdout}",
        )
        self.assertEqual(result.stdout.strip(), "")


class TestVaultAES256GCMStress(unittest.TestCase):
    """Stress-test AES-256-GCM Vault tamper resistance, master key fallback, and secret payloads."""

    def setUp(self):
        reset_master_key_cache()
        self.test_dir = tempfile.mkdtemp(prefix="test_vault_stress_")
        self.key_file = os.path.join(self.test_dir, ".master_key")

    def tearDown(self):
        reset_master_key_cache()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_tamper_ciphertext_raises_decryption_error(self):
        """Corrupting any byte of ciphertext must fail authentication and raise DecryptionError."""
        key = os.urandom(32)
        plaintext = "sensitive-oauth-refresh-token-12345"
        enc = encrypt_secret(plaintext, key=key)

        raw = bytearray(base64.b64decode(enc))
        # Nonce is first 12 bytes; ciphertext is from byte 12 to len(raw)-16
        ciphertext_byte_index = 15
        raw[ciphertext_byte_index] ^= 0xFF  # Flip bits
        tampered_enc = base64.b64encode(raw).decode("utf-8")

        with self.assertRaises(DecryptionError):
            decrypt_secret(tampered_enc, key=key)

    def test_tamper_auth_tag_raises_decryption_error(self):
        """Corrupting any byte of the 16-byte GCM authentication tag must raise DecryptionError."""
        key = os.urandom(32)
        plaintext = "super-confidential-secret-key"
        enc = encrypt_secret(plaintext, key=key)

        raw = bytearray(base64.b64decode(enc))
        # Tag is the last 16 bytes
        tag_byte_index = len(raw) - 1
        raw[tag_byte_index] ^= 0x01
        tampered_enc = base64.b64encode(raw).decode("utf-8")

        with self.assertRaises(DecryptionError):
            decrypt_secret(tampered_enc, key=key)

    def test_tamper_nonce_raises_decryption_error(self):
        """Altering the 12-byte initialization nonce must raise DecryptionError."""
        key = os.urandom(32)
        plaintext = "session-proxy-token-xyz"
        enc = encrypt_secret(plaintext, key=key)

        raw = bytearray(base64.b64decode(enc))
        # Nonce is first 12 bytes
        raw[0] ^= 0x42
        tampered_enc = base64.b64encode(raw).decode("utf-8")

        with self.assertRaises(DecryptionError):
            decrypt_secret(tampered_enc, key=key)

    def test_truncated_and_malformed_payloads(self):
        """Payloads shorter than 28 bytes or invalid Base64 must raise DecryptionError."""
        key = os.urandom(32)
        # Less than 28 bytes (12 nonce + 16 tag = minimum 28)
        short_raw = os.urandom(27)
        short_b64 = base64.b64encode(short_raw).decode("utf-8")
        with self.assertRaises(DecryptionError):
            decrypt_secret(short_b64, key=key)

        # 0 bytes
        empty_b64 = base64.b64encode(b"").decode("utf-8")
        with self.assertRaises(DecryptionError):
            decrypt_secret(empty_b64, key=key)

        # Not valid Base64
        with self.assertRaises(DecryptionError):
            decrypt_secret("!!!@@@###$$$%%%", key=key)

    def test_empty_master_key_env_falls_back_to_file_with_0600_permissions(self):
        """Empty or whitespace MASTER_KEY environment variable falls back to file auto-generation with 0600 mode."""
        orig_env = os.environ.get("MASTER_KEY")
        orig_key_path = settings.MASTER_KEY_PATH
        try:
            # Set MASTER_KEY to empty string in env
            os.environ["MASTER_KEY"] = ""
            settings.MASTER_KEY_PATH = self.key_file
            reset_master_key_cache()

            key = get_master_key()
            self.assertEqual(len(key), 32)
            self.assertTrue(os.path.exists(self.key_file))

            # Verify file permissions are strictly 0600
            file_stat = os.stat(self.key_file)
            permissions = stat.S_IMODE(file_stat.st_mode)
            self.assertEqual(
                permissions,
                0o600,
                f"Master key file permissions should be 0600, got octal {oct(permissions)}",
            )

            # Test round-trip persistence
            reset_master_key_cache()
            reloaded_key = get_master_key()
            self.assertEqual(key, reloaded_key)
        finally:
            settings.MASTER_KEY_PATH = orig_key_path
            if orig_env is not None:
                os.environ["MASTER_KEY"] = orig_env
            elif "MASTER_KEY" in os.environ:
                del os.environ["MASTER_KEY"]
            reset_master_key_cache()

    def test_key_formats_and_length_validation(self):
        """Verify 64-char hex, 32 raw bytes, base64, and reject invalid key lengths."""
        # 64-char hex
        raw_32 = os.urandom(32)
        hex_key = raw_32.hex()
        with patch.dict(os.environ, {"MASTER_KEY": hex_key}):
            reset_master_key_cache()
            self.assertEqual(get_master_key(), raw_32)

        # 32 raw bytes string
        ascii_32 = "a" * 32
        with patch.dict(os.environ, {"MASTER_KEY": ascii_32}):
            reset_master_key_cache()
            self.assertEqual(get_master_key(), ascii_32.encode("utf-8"))

        # Base64 32 bytes
        b64_key = base64.b64encode(raw_32).decode("utf-8")
        with patch.dict(os.environ, {"MASTER_KEY": b64_key}):
            reset_master_key_cache()
            self.assertEqual(get_master_key(), raw_32)

        # Invalid lengths (e.g. 16 bytes, 31 bytes)
        with patch.dict(os.environ, {"MASTER_KEY": "too-short"}):
            reset_master_key_cache()
            with self.assertRaises(ValueError):
                get_master_key()

    def test_unicode_binary_and_large_secrets(self):
        """Vault must cleanly handle emojis, CJK, binary payloads, and 1MB payloads."""
        key = os.urandom(32)

        # Unicode with multi-byte and emojis
        unicode_str = "🔑 Token 深度学习 🤖 العربية Русский \t\n"
        enc_u = encrypt_secret(unicode_str, key=key)
        self.assertEqual(decrypt_secret(enc_u, key=key), unicode_str)

        # Binary data encoded as bytes containing valid UTF-8
        binary_utf8 = "binary_bytes_sample_\x00\x01\x02".encode("utf-8")
        enc_b = encrypt_secret(binary_utf8, key=key)
        self.assertEqual(decrypt_secret(enc_b, key=key), binary_utf8.decode("utf-8"))

        # Non-UTF8 raw binary bytes: encrypt succeeds, decrypt_secret raises DecryptionError
        raw_non_utf8 = b"\x00\x01\x02\xff\xfe\xfd"
        enc_raw = encrypt_secret(raw_non_utf8, key=key)
        with self.assertRaises(DecryptionError) as ctx:
            decrypt_secret(enc_raw, key=key)
        self.assertIn("codec can't decode", str(ctx.exception))

        # 1MB large secret
        large_str = "A" * (1024 * 1024)
        enc_large = encrypt_secret(large_str, key=key)
        self.assertEqual(decrypt_secret(enc_large, key=key), large_str)


class TestDatabaseWALAndForeignKeysStress(unittest.TestCase):
    """Stress-test SQLite WAL mode, foreign key enforcement, cascade deletion, and set-null."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_db_stress_")
        self.db_path = os.path.join(self.test_dir, "stress_manager.db")
        init_db(self.db_path)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_wal_mode_and_foreign_keys_active(self):
        """Verify SQLite is running in WAL mode and foreign keys pragma is ON."""
        with get_db(self.db_path) as conn:
            journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")

            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            self.assertEqual(fk, 1)

    def test_all_8_tables_exist(self):
        """Verify exact 8 tables exist in schema."""
        health = check_db_health(self.db_path)
        expected_tables = {
            "accounts",
            "jobs",
            "notification_channels",
            "secrets",
            "sessions",
            "storage_profiles",
            "telemetry_samples",
            "templates",
        }
        self.assertEqual(set(health["tables"]), expected_tables)
        self.assertEqual(health["table_count"], 8)

    def test_cascade_deletion_of_sessions_and_telemetry(self):
        """Deleting an account must cascade-delete its sessions and associated telemetry."""
        account_id = f"acc_{uuid.uuid4().hex[:8]}"
        session_id = f"sess_{uuid.uuid4().hex[:8]}"

        with get_db(self.db_path) as conn:
            # Insert Account
            conn.execute(
                "INSERT INTO accounts (id, email, oauth_creds_enc) VALUES (?, ?, ?);",
                (account_id, "user@example.com", "enc_creds"),
            )
            # Insert Session referencing Account
            conn.execute(
                """INSERT INTO sessions
                   (id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc, token_expires_at, accelerator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '+2 hours'), ?);""",
                (session_id, "test-session", account_id, "nb_hash", "ep_1", "https://proxy", "enc_token", "T4"),
            )
            # Insert Telemetry Sample referencing Session
            conn.execute(
                "INSERT INTO telemetry_samples (session_id, cpu_pct, ram_used_mb) VALUES (?, ?, ?);",
                (session_id, 45.5, 2048.0),
            )

        # Verify rows exist
        with get_db(self.db_path) as conn:
            s_row = conn.execute("SELECT id FROM sessions WHERE id = ?;", (session_id,)).fetchone()
            self.assertIsNotNone(s_row)
            t_row = conn.execute("SELECT id FROM telemetry_samples WHERE session_id = ?;", (session_id,)).fetchone()
            self.assertIsNotNone(t_row)

        # Delete Account
        with get_db(self.db_path) as conn:
            conn.execute("DELETE FROM accounts WHERE id = ?;", (account_id,))

        # Verify Session and Telemetry were CASCADE deleted
        with get_db(self.db_path) as conn:
            s_row = conn.execute("SELECT id FROM sessions WHERE id = ?;", (session_id,)).fetchone()
            self.assertIsNone(s_row, "Session should have been cascade deleted when Account was deleted")
            t_row = conn.execute("SELECT id FROM telemetry_samples WHERE session_id = ?;", (session_id,)).fetchone()
            self.assertIsNone(t_row, "Telemetry sample should have been cascade deleted when Session was deleted")

    def test_set_null_behavior_on_template_deletion(self):
        """Deleting a template must SET NULL on referencing session.template_id and job.template_id."""
        account_id = f"acc_{uuid.uuid4().hex[:8]}"
        template_id = f"tpl_{uuid.uuid4().hex[:8]}"
        session_id = f"sess_{uuid.uuid4().hex[:8]}"
        job_id = f"job_{uuid.uuid4().hex[:8]}"

        with get_db(self.db_path) as conn:
            conn.execute(
                "INSERT INTO accounts (id, email, oauth_creds_enc) VALUES (?, ?, ?);",
                (account_id, "dev@example.com", "enc_creds"),
            )
            conn.execute(
                "INSERT INTO templates (id, name, startup_script) VALUES (?, ?, ?);",
                (template_id, "PyTorch Deep Learning", "pip install torch"),
            )
            conn.execute(
                """INSERT INTO sessions
                   (id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc, token_expires_at, accelerator, template_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '+2 hours'), ?, ?);""",
                (session_id, "dev-session", account_id, "nb_hash", "ep_2", "https://proxy", "enc_token", "T4", template_id),
            )
            conn.execute(
                """INSERT INTO jobs
                   (id, name, session_id, target_accelerator, template_id, job_type, entry_path_or_cmd, log_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
                (job_id, "train-job", session_id, "T4", template_id, "python", "train.py", "/data/logs/train.log"),
            )

        # Verify initial template_id values
        with get_db(self.db_path) as conn:
            s_row = conn.execute("SELECT template_id FROM sessions WHERE id = ?;", (session_id,)).fetchone()
            self.assertEqual(s_row[0], template_id)
            j_row = conn.execute("SELECT template_id FROM jobs WHERE id = ?;", (job_id,)).fetchone()
            self.assertEqual(j_row[0], template_id)

        # Delete Template
        with get_db(self.db_path) as conn:
            conn.execute("DELETE FROM templates WHERE id = ?;", (template_id,))

        # Verify template_id was SET NULL on both session and job
        with get_db(self.db_path) as conn:
            s_row = conn.execute("SELECT template_id FROM sessions WHERE id = ?;", (session_id,)).fetchone()
            self.assertIsNone(s_row[0], "session.template_id should be SET NULL after template deletion")
            j_row = conn.execute("SELECT template_id FROM jobs WHERE id = ?;", (job_id,)).fetchone()
            self.assertIsNone(j_row[0], "job.template_id should be SET NULL after template deletion")

    def test_foreign_key_violation_raises_error(self):
        """Inserting a session referencing a non-existent account must raise an IntegrityError."""
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            with get_db(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO sessions
                       (id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc, token_expires_at, accelerator)
                       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', '+2 hours'), ?);""",
                    ("sess_bad", "bad-session", "non_existent_acc", "nb", "ep", "https://p", "tok", "T4"),
                )


class TestAuthenticationAndJWTStress(unittest.TestCase):
    """Stress-test JWT authentication: expired tokens, invalid signatures, malformed headers, 401s."""

    def setUp(self):
        self.client = TestClient(app)

    def test_admin_login_happy_path_and_rejection(self):
        """Admin login succeeds with correct password and rejects invalid password with 401."""
        resp = self.client.post("/api/auth/login", json={"password": settings.ADMIN_PASSWORD})
        self.assertEqual(resp.status_code, 200)
        token = resp.json().get("token")
        self.assertTrue(token)

        # Wrong password
        resp_bad = self.client.post("/api/auth/login", json={"password": "wrong_password_12345"})
        self.assertEqual(resp_bad.status_code, 401)

    def test_expired_jwt_token_returns_401(self):
        """Expired JWT token must be rejected with 401 Unauthorized."""
        expired_token = create_access_token({"sub": "admin", "role": "admin"}, expires_delta_seconds=-3600)
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("expired", resp.text.lower())

    def test_invalid_signature_returns_401(self):
        """JWT token signed with a foreign key must be rejected with 401 Unauthorized."""
        import jwt
        fake_token = jwt.encode(
            {"sub": "admin", "role": "admin", "exp": int(time.time()) + 3600},
            "completely_foreign_untrusted_signing_key_32_bytes!!",
            algorithm="HS256",
        )
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {fake_token}"})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("validation failed", resp.text.lower())

    def test_malformed_authorization_headers_return_401(self):
        """Malformed, missing, or non-Bearer headers must return 401 Unauthorized."""
        headers_to_test = [
            {},  # Missing Authorization header
            {"Authorization": "Bearer"},  # Missing token
            {"Authorization": "Basic dXNlcjpwYXNz"},  # Basic auth
            {"Authorization": "Token 12345"},  # Token scheme
            {"Authorization": "Bearer abc.def"},  # Incomplete JWT segments
            {"Authorization": "Bearer not-a-valid-token"},  # Garbage token
        ]
        for header in headers_to_test:
            resp = self.client.get("/api/auth/me", headers=header)
            self.assertEqual(
                resp.status_code,
                401,
                f"Expected 401 for header {header}, got {resp.status_code}",
            )


class TestColabAdapterErrorHandlingAndShapeRules(unittest.TestCase):
    """Stress-test Colab client error mapping (HTTP 400, 412), XSSI prefix, and shape rules."""

    def setUp(self):
        self.client = ColabClient(base_url="https://mock.colab.test")

    def test_xssi_prefix_stripping(self):
        """Verify strip_xssi_prefix cleanly removes )]}'\n without touching normal JSON."""
        with_prefix = ")]}'\n{\"status\": \"ok\", \"count\": 42}"
        without_prefix = "{\"status\": \"ok\", \"count\": 42}"
        self.assertEqual(strip_xssi_prefix(with_prefix), without_prefix)
        self.assertEqual(strip_xssi_prefix(without_prefix), without_prefix)

    def test_uuid_web_safe_base64_transformation(self):
        """Verify UUID transformation replaces hyphens with underscores and pads to 44 characters."""
        test_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        result = uuid_to_web_safe_base64(test_uuid)
        self.assertEqual(len(result), 44)
        self.assertNotIn("-", result)
        self.assertTrue(result.endswith("."))

    def test_accelerator_shape_rule_omits_shape_hm_for_l4_v5e1_v6e1(self):
        """Accelerator shape rule: shape=hm must be omitted for L4, V5E1, V6E1."""
        captured_params = {}

        def mock_get(url, params=None, **kwargs):
            nonlocal captured_params
            captured_params = params or {}
            resp = MagicMock()
            resp.status_code = 200
            resp.ok = True
            resp.text = ")]}'\n{\"token\": \"mock_xsrf_token\"}"
            return resp

        def mock_post(url, params=None, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.ok = True
            resp.text = ")]}'\n{\"endpoint\": \"ep1\", \"runtimeProxyInfo\": {\"url\": \"u\", \"token\": \"t\"}}"
            return resp

        with patch.object(self.client.session, "get", side_effect=mock_get):
            with patch.object(self.client.session, "post", side_effect=mock_post):
                # Test L4
                self.client.assign("dummy_token", "hash1", accelerator="L4", shape="hm")
                self.assertNotIn("shape", captured_params, "shape=hm should be omitted for L4")

                # Test V5E1
                self.client.assign("dummy_token", "hash2", accelerator="V5E1", shape="hm")
                self.assertNotIn("shape", captured_params, "shape=hm should be omitted for V5E1")

                # Test V6E1
                self.client.assign("dummy_token", "hash3", accelerator="V6E1", shape="hm")
                self.assertNotIn("shape", captured_params, "shape=hm should be omitted for V6E1")

                # Test T4 (shape=hm SHOULD be included)
                self.client.assign("dummy_token", "hash4", accelerator="T4", shape="hm")
                self.assertEqual(captured_params.get("shape"), "hm", "shape=hm should be preserved for T4")

                # Test A100 (shape=hm SHOULD be included)
                self.client.assign("dummy_token", "hash5", accelerator="A100", shape="hm")
                self.assertEqual(captured_params.get("shape"), "hm", "shape=hm should be preserved for A100")

    def test_colab_http_400_raises_quota_exceeded_error(self):
        """Colab HTTP 400 during assign must map to QuotaExceededError."""
        # On GET probe
        resp_400 = MagicMock()
        resp_400.status_code = 400
        resp_400.ok = False
        resp_400.text = ")]}'\n{\"error\": \"No quota available for requested accelerator\"}"

        with patch.object(self.client.session, "get", return_value=resp_400):
            with self.assertRaises(QuotaExceededError) as ctx:
                self.client.assign("dummy_token", "hash1", accelerator="A100")
            self.assertEqual(ctx.exception.status_code, 400)

        # On POST commit
        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.ok = True
        resp_200.text = ")]}'\n{\"token\": \"valid_xsrf\"}"

        with patch.object(self.client.session, "get", return_value=resp_200):
            with patch.object(self.client.session, "post", return_value=resp_400):
                with self.assertRaises(QuotaExceededError) as ctx:
                    self.client.assign("dummy_token", "hash1", accelerator="A100")
                self.assertEqual(ctx.exception.status_code, 400)

    def test_colab_http_412_raises_too_many_assignments_error(self):
        """Colab HTTP 412 during assign must map to TooManyAssignmentsError."""
        # On GET probe
        resp_412 = MagicMock()
        resp_412.status_code = 412
        resp_412.ok = False
        resp_412.text = ")]}'\n{\"error\": \"Active assignment limit reached for user\"}"

        with patch.object(self.client.session, "get", return_value=resp_412):
            with self.assertRaises(TooManyAssignmentsError) as ctx:
                self.client.assign("dummy_token", "hash1", accelerator="T4")
            self.assertEqual(ctx.exception.status_code, 412)

        # On POST commit
        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.ok = True
        resp_200.text = ")]}'\n{\"token\": \"valid_xsrf\"}"

        with patch.object(self.client.session, "get", return_value=resp_200):
            with patch.object(self.client.session, "post", return_value=resp_412):
                with self.assertRaises(TooManyAssignmentsError) as ctx:
                    self.client.assign("dummy_token", "hash1", accelerator="T4")
                self.assertEqual(ctx.exception.status_code, 412)


if __name__ == "__main__":
    unittest.main()
