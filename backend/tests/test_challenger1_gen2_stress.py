"""Milestone 1 Phase 1 — Challenger 1 (Gen 2) Empirical Security & Stress Test Suite.

Adversarial stress testing for:
1. AES-256-GCM Vault: auth tag corruption, ciphertext tampering, nonce manipulation,
   truncated payloads, corrupted keys, key rotation, and persistence.
2. SQLite WAL Concurrency: high-concurrency multi-threaded read/write stress,
   deadlock & lock contention resilience, rollback integrity, and foreign key enforcement.
3. JWT Security: 'alg: none' rejection, tampered payload claims, invalid signatures,
   expired tokens, malformed headers, and non-admin role rejection.
4. Session Management: idempotent stop, active_assignments floor at zero,
   non-existent session handling, and concurrent port allocation.
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock
import uuid
from typing import List

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
    hash_password,
    reset_master_key_cache,
    verify_admin_password,
    verify_password,
)
from backend.app.main import app
from backend.app.services import session_manager


class TestVaultAES256GCMAdversarialStress(unittest.TestCase):
    """Exhaustive adversarial testing for AES-256-GCM Vault."""

    def setUp(self):
        reset_master_key_cache()
        self.test_dir = tempfile.mkdtemp(prefix="challenger_vault_")
        self.key_file = os.path.join(self.test_dir, ".master_key")
        self.orig_key_path = settings.MASTER_KEY_PATH
        settings.MASTER_KEY_PATH = self.key_file

    def tearDown(self):
        settings.MASTER_KEY_PATH = self.orig_key_path
        reset_master_key_cache()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_auth_tag_all_bytes_tamper_detection(self):
        """Every single byte in the 16-byte GCM authentication tag must trigger DecryptionError when tampered."""
        key = os.urandom(32)
        plaintext = "adversarial-crypto-target-data-sensitive"
        enc = encrypt_secret(plaintext, key=key)
        raw = base64.b64decode(enc.encode("utf-8"))

        # GCM tag is the final 16 bytes: indices from len(raw) - 16 to len(raw) - 1
        tag_start = len(raw) - 16
        for offset in range(tag_start, len(raw)):
            tampered = bytearray(raw)
            tampered[offset] ^= 0x5A  # Invert multiple bits
            b64_tampered = base64.b64encode(tampered).decode("utf-8")
            with self.assertRaises(
                DecryptionError,
                msg=f"Tampering at tag offset {offset} failed to raise DecryptionError",
            ):
                decrypt_secret(b64_tampered, key=key)

    def test_nonce_all_bytes_tamper_detection(self):
        """Every single byte in the 12-byte initialization nonce must trigger DecryptionError when tampered."""
        key = os.urandom(32)
        plaintext = "target-data-for-nonce-tampering"
        enc = encrypt_secret(plaintext, key=key)
        raw = base64.b64decode(enc.encode("utf-8"))

        # Nonce is the first 12 bytes: indices 0 to 11
        for offset in range(12):
            tampered = bytearray(raw)
            tampered[offset] ^= 0xFF
            b64_tampered = base64.b64encode(tampered).decode("utf-8")
            with self.assertRaises(
                DecryptionError,
                msg=f"Tampering at nonce offset {offset} failed to raise DecryptionError",
            ):
                decrypt_secret(b64_tampered, key=key)

    def test_ciphertext_boundaries_tamper_detection(self):
        """First byte, middle byte, and last byte of ciphertext must trigger DecryptionError when tampered."""
        key = os.urandom(32)
        plaintext = "A" * 64
        enc = encrypt_secret(plaintext, key=key)
        raw = bytearray(base64.b64decode(enc.encode("utf-8")))

        ciphertext_indices = [12, 12 + 10, 12 + 32, len(raw) - 17]
        for idx in ciphertext_indices:
            tampered = bytearray(raw)
            tampered[idx] ^= 0x01
            b64_tampered = base64.b64encode(tampered).decode("utf-8")
            with self.assertRaises(
                DecryptionError,
                msg=f"Tampering at ciphertext index {idx} did not raise DecryptionError",
            ):
                decrypt_secret(b64_tampered, key=key)

    def test_truncated_payloads_do_not_crash(self):
        """Truncated payloads of length 0..27 bytes must raise DecryptionError cleanly without unhandled exceptions."""
        key = os.urandom(32)
        for length in [0, 1, 5, 11, 12, 15, 20, 27]:
            raw = os.urandom(length)
            b64_payload = base64.b64encode(raw).decode("utf-8")
            with self.assertRaises(DecryptionError):
                decrypt_secret(b64_payload, key=key)

    def test_invalid_base64_payloads(self):
        """Non-base64 strings and malformed paddings must raise DecryptionError cleanly."""
        key = os.urandom(32)
        invalid_payloads = [
            "invalid!base64!characters====",
            "A" * 31,  # Invalid length without padding
            "%",
            "\x00\x01\x02",
        ]
        for payload in invalid_payloads:
            with self.assertRaises(DecryptionError):
                decrypt_secret(payload, key=key)

    def test_corrupted_key_length_validation(self):
        """Passing keys not exactly 32 bytes must raise ValueError."""
        plaintext = "secret"
        for bad_len in [0, 1, 16, 24, 31, 33, 64]:
            bad_key = os.urandom(bad_len)
            with self.assertRaises(ValueError):
                encrypt_secret(plaintext, key=bad_key)
            with self.assertRaises(ValueError):
                decrypt_secret("dummy_base64", key=bad_key)

    def test_key_rotation_and_mismatch(self):
        """Verify secrets encrypted with old key fail with new key, and can be rotated."""
        old_key = os.urandom(32)
        new_key = os.urandom(32)
        original_secret = "oauth-refresh-token-xyz-123"

        # Encrypt with old key
        enc_old = encrypt_secret(original_secret, key=old_key)

        # Attempt to decrypt with new key must fail
        with self.assertRaises(DecryptionError):
            decrypt_secret(enc_old, key=new_key)

        # Rotate: decrypt with old key, re-encrypt with new key
        decrypted = decrypt_secret(enc_old, key=old_key)
        self.assertEqual(decrypted, original_secret)

        enc_new = encrypt_secret(decrypted, key=new_key)
        self.assertNotEqual(enc_old, enc_new)
        self.assertEqual(decrypt_secret(enc_new, key=new_key), original_secret)

    def test_large_payload_encryption(self):
        """Encrypt and decrypt a 2MB payload to verify throughput and integrity."""
        key = os.urandom(32)
        large_secret = "colab_tensor_data_" * (100_000)  # ~1.8 MB
        enc = encrypt_secret(large_secret, key=key)
        decrypted = decrypt_secret(enc, key=key)
        self.assertEqual(decrypted, large_secret)

    def test_vault_zero_byte_plaintext(self):
        """Encrypting empty string yields exactly 28 bytes (12 nonce + 16 tag) and decrypts to empty string."""
        key = os.urandom(32)
        enc = encrypt_secret("", key=key)
        raw = base64.b64decode(enc.encode("utf-8"))
        self.assertEqual(len(raw), 28)
        self.assertEqual(decrypt_secret(enc, key=key), "")

    def test_vault_auth_tag_truncation_boundary(self):
        """Payloads between 13 and 27 bytes (valid nonce + truncated tag) must raise DecryptionError."""
        key = os.urandom(32)
        for sub_len in range(13, 28):
            truncated_raw = os.urandom(sub_len)
            truncated_b64 = base64.b64encode(truncated_raw).decode("utf-8")
            with self.assertRaises(DecryptionError):
                decrypt_secret(truncated_b64, key=key)


class TestSQLiteWALConcurrencyStress(unittest.TestCase):
    """Stress test SQLite WAL mode under heavy multi-threaded concurrent read/write loads."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="challenger_wal_stress_")
        self.db_path = os.path.join(self.test_dir, "wal_stress.db")
        init_db(self.db_path)

        # Insert 3 test accounts
        with get_db(self.db_path) as conn:
            for i in range(3):
                conn.execute(
                    """
                    INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance)
                    VALUES (?, ?, ?, 100.0)
                    """,
                    (f"acc_{i}", f"user{i}@example.com", "enc_creds"),
                )

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_high_concurrency_multi_threaded_reads_and_writes(self):
        """Run 20 concurrent reader threads and 10 concurrent writer threads.

        Verifies SQLite WAL concurrency without 'database is locked' errors.
        """
        num_readers = 20
        num_writers = 10
        iterations_per_thread = 25
        errors: List[Exception] = []

        def reader_task(reader_id: int):
            try:
                for _ in range(iterations_per_thread):
                    with get_db(self.db_path) as conn:
                        rows = conn.execute("SELECT id, email, ccu_balance FROM accounts").fetchall()
                        self.assertEqual(len(rows), 3)
                        # Also read telemetry
                        conn.execute("SELECT COUNT(*) FROM telemetry_samples").fetchone()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def writer_task(writer_id: int):
            try:
                for i in range(iterations_per_thread):
                    # Alternate between updating account and inserting telemetry
                    with get_db(self.db_path) as conn:
                        conn.execute(
                            "UPDATE accounts SET ccu_balance = ccu_balance + 0.1 WHERE id = ?",
                            (f"acc_{writer_id % 3}",),
                        )
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads: List[threading.Thread] = []
        for r in range(num_readers):
            t = threading.Thread(target=reader_task, args=(r,))
            threads.append(t)
        for w in range(num_writers):
            t = threading.Thread(target=writer_task, args=(w,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        self.assertEqual(
            len(errors),
            0,
            f"Encountered {len(errors)} concurrency errors during SQLite WAL stress: {errors[:5]}",
        )

        # Verify account balance reflects all writer increments
        with get_db(self.db_path) as conn:
            total_ccu = conn.execute("SELECT SUM(ccu_balance) FROM accounts").fetchone()[0]
            expected_total = 300.0 + (num_writers * iterations_per_thread * 0.1)
            self.assertAlmostEqual(total_ccu, expected_total, places=2)

    def test_wal_heavy_burst_stress_50_threads_1000_txns(self):
        """Burst stress test with 50 threads executing 1,000 mixed concurrent transactions."""
        errors: List[Exception] = []
        num_threads = 50
        txns_per_thread = 20

        def burst_worker(worker_id: int):
            try:
                for i in range(txns_per_thread):
                    if worker_id % 2 == 0:
                        # Writer transaction
                        with get_db(self.db_path) as conn:
                            conn.execute(
                                "UPDATE accounts SET burn_rate_hourly = burn_rate_hourly + 0.01 WHERE id = ?",
                                (f"acc_{worker_id % 3}",),
                            )
                    else:
                        # Reader transaction
                        with get_db(self.db_path) as conn:
                            rows = conn.execute("SELECT id, burn_rate_hourly FROM accounts").fetchall()
                            self.assertEqual(len(rows), 3)
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(burst_worker, i) for i in range(num_threads)]
            concurrent.futures.wait(futures)

        self.assertEqual(
            len(errors),
            0,
            f"Encountered {len(errors)} concurrency errors in 50-thread burst: {errors[:5]}",
        )

    def test_transaction_rollback_preserves_atomic_state(self):
        """Exceptions inside get_db() context manager must cleanly rollback changes."""
        account_id = "acc_0"
        with get_db(self.db_path) as conn:
            initial_balance = conn.execute(
                "SELECT ccu_balance FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]

        # Attempt transaction that modifies balance then raises an exception
        with self.assertRaises(RuntimeError):
            with get_db(self.db_path) as conn:
                conn.execute(
                    "UPDATE accounts SET ccu_balance = 999999.0 WHERE id = ?", (account_id,)
                )
                raise RuntimeError("Simulated mid-transaction failure")

        # Verify balance was NOT committed
        with get_db(self.db_path) as conn:
            post_balance = conn.execute(
                "SELECT ccu_balance FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()[0]
            self.assertEqual(initial_balance, post_balance)

    def test_foreign_key_enforcement_on_connection(self):
        """Foreign key constraint must be strictly enforced on each acquired connection."""
        with self.assertRaises(Exception):
            with get_db(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
                    """,
                    ("sess_invalid", "test", "non_existent_account_xyz", "nb", "ep", "u", "tok", "T4"),
                )


class TestJWTAuthenticationSecurityStress(unittest.TestCase):
    """Adversarial security tests for JWT authentication."""

    def setUp(self):
        self.client = TestClient(app)

    def test_jwt_alg_none_rejection(self):
        """Tokens with header {"alg": "none"} must be rejected with 401."""
        header = {"alg": "none", "typ": "JWT"}
        payload = {"sub": "admin", "role": "admin", "exp": int(time.time()) + 3600}
        h_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
        p_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()

        none_token = f"{h_b64}.{p_b64}."
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {none_token}"})
        self.assertEqual(resp.status_code, 401)

    def test_jwt_tampered_payload_claims(self):
        """Tokens where payload was tampered without updating signature must be rejected with 401."""
        valid_token = create_access_token({"sub": "admin", "role": "admin"})
        parts = valid_token.split(".")
        self.assertEqual(len(parts), 3)

        # Alter payload to change role to superuser or change expiry
        raw_payload = json.loads(base64.urlsafe_b64decode(parts[1] + "==").decode())
        raw_payload["role"] = "superadmin"
        tampered_p_b64 = base64.urlsafe_b64encode(json.dumps(raw_payload).encode()).rstrip(b"=").decode()
        tampered_token = f"{parts[0]}.{tampered_p_b64}.{parts[2]}"

        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered_token}"})
        self.assertEqual(resp.status_code, 401)

    def test_jwt_insufficient_permissions_non_admin_role_returns_403(self):
        """Tokens signed with master key but having role != 'admin' must return 403 Forbidden."""
        user_token = create_access_token({"sub": "guest_user", "role": "viewer"})
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {user_token}"})
        self.assertEqual(resp.status_code, 403)
        self.assertIn("insufficient permissions", resp.text.lower())

    def test_jwt_expired_token_boundaries(self):
        """Tokens expired 1 second ago must be rejected with 401."""
        expired_token = create_access_token({"sub": "admin", "role": "admin"}, expires_delta_seconds=-1)
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("expired", resp.text.lower())

    def test_jwt_malformed_formats_clean_rejection(self):
        """Edge case token inputs must not trigger unhandled 500 errors."""
        malformed_inputs = [
            "",
            ".",
            "..",
            "...",
            "header.payload",
            "a.b.c.d",
            "Bearer ",
            "\x00\x00\x00",
            "invalid_base64.invalid_base64.invalid_base64",
        ]
        for bad_token in malformed_inputs:
            resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {bad_token}"})
            self.assertEqual(resp.status_code, 401)

    def test_jwt_different_algorithms_rejected(self):
        """Tokens using unsupported algorithms (HS384, HS512, none) must be rejected."""
        import jwt
        key = get_master_key().decode("latin1", errors="ignore")
        # Token signed with HS384
        hs384_token = jwt.encode({"sub": "admin", "role": "admin", "exp": int(time.time()) + 3600}, key, algorithm="HS384")
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {hs384_token}"})
        self.assertEqual(resp.status_code, 401)

        # Token signed with HS512
        hs512_token = jwt.encode({"sub": "admin", "role": "admin", "exp": int(time.time()) + 3600}, key, algorithm="HS512")
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {hs512_token}"})
        self.assertEqual(resp.status_code, 401)

    def test_password_hashing_and_admin_auth_stress(self):
        """Stress verify password hashing against malformed PBKDF2 strings and timing safety."""
        # 1. Valid PBKDF2 hash
        pwd = "SecretAdminPassword123!"
        hashed = hash_password(pwd)
        self.assertTrue(verify_password(pwd, hashed))
        self.assertFalse(verify_password("wrong-password", hashed))

        # 2. Malformed PBKDF2 hash strings must return False without crashing
        malformed_hashes = [
            "",
            "invalid",
            "pbkdf2:sha256",
            "pbkdf2:sha256$only_two_parts",
            "pbkdf2:sha256:not_a_number$salt$hash",
            "pbkdf2:sha256:1000$nothex$nothex",
            "pbkdf2:unsupportedalgo:1000$abcd$1234",
            "pbkdf2:sha256:1000$$",
        ]
        for bad_hash in malformed_hashes:
            self.assertFalse(verify_password("any_password", bad_hash))

        # 3. Very long password string (100KB)
        long_pwd = "A" * 100_000
        self.assertFalse(verify_admin_password(long_pwd))


class TestSessionManagementAdversarial(unittest.TestCase):
    """Stress testing session state management, idempotency, and counters."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="challenger_sess_")
        self.db_path = os.path.join(self.test_dir, "sess_manager.db")
        init_db(self.db_path)
        self.orig_db_path = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path

        # Seed account
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, active_assignments)
                VALUES ('acc_test', 'test@example.com', 'dummy_creds', 1)
                """
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, status, ssh_local_port
                ) VALUES (
                    'sess_active', 'Active Session', 'acc_test', 'nb_hash', 'ep_1', 'https://proxy',
                    'enc_token', datetime('now', '+1 hour'), 'T4', 'READY', 2220
                )
                """
            )

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db_path
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_stop_session_idempotent_and_assignments_floor(self):
        """Calling stop_session multiple times must succeed and NEVER decrement active_assignments below 0."""
        # Mock ColabClient to avoid real network call
        mock_colab = MagicMock()

        # First stop
        result1 = session_manager.stop_session("sess_active", client=mock_colab)
        self.assertTrue(result1)

        with get_db(self.db_path) as conn:
            sess = conn.execute("SELECT status, ssh_local_port FROM sessions WHERE id = 'sess_active'").fetchone()
            self.assertEqual(sess["status"], "STOPPED")
            self.assertIsNone(sess["ssh_local_port"])
            acc = conn.execute("SELECT active_assignments FROM accounts WHERE id = 'acc_test'").fetchone()
            self.assertEqual(acc["active_assignments"], 0)

        # Second stop (idempotent call on already STOPPED session)
        result2 = session_manager.stop_session("sess_active", client=mock_colab)
        self.assertTrue(result2)

        with get_db(self.db_path) as conn:
            acc = conn.execute("SELECT active_assignments FROM accounts WHERE id = 'acc_test'").fetchone()
            self.assertEqual(acc["active_assignments"], 0, "active_assignments must not fall below 0")

    def test_stop_non_existent_session_raises_value_error(self):
        """Stopping a non-existent session must raise ValueError."""
        mock_colab = MagicMock()
        with self.assertRaises(ValueError):
            session_manager.stop_session("sess_does_not_exist", client=mock_colab)

    def test_ssh_port_pool_exhaustion_behavior(self):
        """When all ports 2220-2229 are occupied, allocate_ssh_port returns None gracefully."""
        with get_db(self.db_path) as conn:
            # Fill remaining ports in settings.ssh_port_pool (port 2220 is already used in setUp)
            for port in settings.ssh_port_pool:
                if port == 2220:
                    continue
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator, status, ssh_local_port
                    ) VALUES (?, ?, 'acc_test', 'nb', 'ep', 'u', 'tok', datetime('now'), 'T4', 'READY', ?)
                    """,
                    (f"sess_port_{port}", f"Session {port}", port),
                )

        allocated_port = session_manager.allocate_ssh_port()
        self.assertIsNone(allocated_port, "allocate_ssh_port should return None when pool is exhausted")


if __name__ == "__main__":
    unittest.main()
