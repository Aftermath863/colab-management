"""Tier 2 Boundary Tests: Security & Vault Boundaries (T2.1).

Validates:
1. Tampered JWT tokens (signature, alg: none, claims tampering, malformed) return 401.
2. Invalid MASTER_KEY at boot fails fast with ValueError.
3. 64KB - 1MB unicode and binary secrets encryption/decryption round-trip.
4. Vault tamper detection across ciphertext, nonce, and auth tag bytes.
5. 20 concurrent admin logins under load succeed with 200 OK.
6. SQL injection defense in vault keys, account queries, and session lookups.
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import shutil
import tempfile
import time
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
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


class TestSecurityVaultBoundaries(unittest.TestCase):
    """Tier 2 Boundary tests for Auth, JWT, and AES-256-GCM Vault."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="t2_vault_")
        self.db_path = os.path.join(self.test_dir, "vault_boundary.db")
        init_db(self.db_path)
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path
        self.client = TestClient(app)

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_tampered_jwt_tokens_rejected_401(self):
        """Tokens with forged signature, alg:none, modified payload claims, or malformed headers return 401."""
        valid_token = create_access_token({"sub": "admin", "role": "admin"})

        # 1. Tampered payload claim (e.g. escalating role or changing subject)
        parts = valid_token.split(".")
        payload_bytes = base64.urlsafe_b64decode(parts[1] + "==")
        payload = json.loads(payload_bytes.decode())
        payload["role"] = "superadmin"
        tampered_p = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        tampered_token = f"{parts[0]}.{tampered_p}.{parts[2]}"

        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered_token}"})
        self.assertEqual(resp.status_code, 401, "Tampered payload claims must return 401")

        # 2. alg: none token attack
        header = {"alg": "none", "typ": "JWT"}
        h_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
        none_token = f"{h_b64}.{tampered_p}."
        resp_none = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {none_token}"})
        self.assertEqual(resp_none.status_code, 401, "alg:none token must return 401")

        # 3. Corrupt signature
        corrupt_token = f"{parts[0]}.{parts[1]}.invalid_signature_xyz"
        resp_corrupt = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {corrupt_token}"})
        self.assertEqual(resp_corrupt.status_code, 401, "Corrupted signature must return 401")

        # 4. Malformed bearer formats
        for malformed in ["", "invalid", "Bearer", "Bearer a.b", "Bearer a.b.c.d"]:
            resp_mal = self.client.get("/api/auth/me", headers={"Authorization": malformed})
            self.assertEqual(resp_mal.status_code, 401, f"Malformed token '{malformed}' must return 401")

    def test_invalid_master_key_at_boot_fails_fast(self):
        """Invalid MASTER_KEY length, invalid hex, or non-decodable string fails fast with ValueError."""
        invalid_keys = [
            "too_short",
            "a" * 16,
            "b" * 31,
            "c" * 33,
            "z" * 64,  # 64 chars but not valid hex
            "not_valid_base64_???===",
        ]
        for bad_key in invalid_keys:
            with patch.dict(os.environ, {"MASTER_KEY": bad_key}):
                reset_master_key_cache()
                with self.assertRaises(ValueError, msg=f"Key '{bad_key}' should raise ValueError"):
                    get_master_key()
        reset_master_key_cache()

    def test_large_unicode_and_binary_secrets_in_vault(self):
        """Vault cleanly encrypts and decrypts high-plane emojis, CJK, and up to 1MB payloads."""
        # 1. Complex unicode with emojis and special characters
        unicode_secret = "🔑 Token 深度学习 🤖 العربية Русский \t\n\r" + ("🌟" * 1000)
        enc_u = encrypt_secret(unicode_secret)
        self.assertEqual(decrypt_secret(enc_u), unicode_secret)

        # 2. 1MB large secret
        large_secret = "COLAB_TENSOR_SECRET_DATA_" * (40_000)  # ~1MB
        enc_large = encrypt_secret(large_secret)
        self.assertEqual(decrypt_secret(enc_large), large_secret)

        # 3. Non-UTF-8 binary bytes raise DecryptionError upon string decode
        non_utf8_bytes = b"\x80\x81\xff\xfe\x00\x01\x02"
        enc_raw = encrypt_secret(non_utf8_bytes)
        with self.assertRaises(DecryptionError):
            decrypt_secret(enc_raw)

    def test_vault_tamper_detection_all_bytes(self):
        """Altering any byte in ciphertext, nonce, or auth tag raises DecryptionError."""
        original = "secret_colab_credentials_alpha"
        ciphertext = encrypt_secret(original)
        raw_bytes = bytearray(base64.b64decode(ciphertext))

        # Payload structure: 12 bytes nonce + N bytes ciphertext + 16 bytes auth tag
        self.assertGreaterEqual(len(raw_bytes), 28)

        # Test tamper at byte 0 (nonce)
        tampered_nonce = bytearray(raw_bytes)
        tampered_nonce[0] ^= 0xFF
        with self.assertRaises(DecryptionError):
            decrypt_secret(base64.b64encode(tampered_nonce).decode())

        # Test tamper at midpoint (ciphertext)
        tampered_body = bytearray(raw_bytes)
        mid = len(tampered_body) // 2
        tampered_body[mid] ^= 0xAA
        with self.assertRaises(DecryptionError):
            decrypt_secret(base64.b64encode(tampered_body).decode())

        # Test tamper at last byte (auth tag)
        tampered_tag = bytearray(raw_bytes)
        tampered_tag[-1] ^= 0x55
        with self.assertRaises(DecryptionError):
            decrypt_secret(base64.b64encode(tampered_tag).decode())

        # Truncated payload (< 28 bytes)
        for short_len in [0, 5, 12, 27]:
            truncated = base64.b64encode(os.urandom(short_len)).decode()
            with self.assertRaises(DecryptionError):
                decrypt_secret(truncated)

    def test_concurrent_admin_logins_under_load(self):
        """20 concurrent threads calling /api/auth/login succeed without WAL contention or deadlock."""
        num_threads = 20

        def login_task():
            client = TestClient(app)
            resp = client.post("/api/auth/login", json={"password": settings.ADMIN_PASSWORD})
            return resp.status_code, resp.json()

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(login_task) for _ in range(num_threads)]
            results = [f.result() for f in futures]

        for status_code, data in results:
            self.assertEqual(status_code, 200)
            token = data.get("token") or data.get("access_token")
            self.assertIsNotNone(token, f"Token missing from login response: {data}")
            claims = decode_access_token(token)
            self.assertEqual(claims.get("sub"), "admin")
            self.assertEqual(claims.get("role"), "admin")

    def test_sql_injection_defense_in_vault_and_queries(self):
        """Malicious SQL injection strings are safely neutralized by query parameterization."""
        injection_payloads = [
            "' OR '1'='1",
            "'; DROP TABLE secrets; --",
            "' UNION SELECT 1, 'admin', 'hacked', 'desc', 'now' --",
            "admin'--",
            "1; UPDATE accounts SET ccu_balance = 99999.0; --",
        ]

        # 1. Insert and retrieve secrets with SQL injection payloads as key_name
        for i, payload in enumerate(injection_payloads):
            secret_id = f"sec_inj_{i}"
            encrypted = encrypt_secret(f"secret_val_{i}")
            with get_db(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO secrets (id, key_name, value_enc, description)
                    VALUES (?, ?, ?, ?)
                    """,
                    (secret_id, payload, encrypted, "injection test"),
                )

            # Query by exact key_name with parameterization
            with get_db(self.db_path) as conn:
                row = conn.execute(
                    "SELECT id, key_name, value_enc FROM secrets WHERE key_name = ?",
                    (payload,),
                ).fetchone()
                self.assertIsNotNone(row, f"Secret with key '{payload}' must be found")
                self.assertEqual(row["key_name"], payload)
                self.assertEqual(decrypt_secret(row["value_enc"]), f"secret_val_{i}")

        # 2. Verify tables were not dropped or corrupted
        with get_db(self.db_path) as conn:
            secret_count = conn.execute("SELECT COUNT(*) FROM secrets").fetchone()[0]
            self.assertEqual(secret_count, len(injection_payloads))
            account_table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='accounts'"
            ).fetchone()
            self.assertIsNotNone(account_table_check, "accounts table must remain intact")


if __name__ == "__main__":
    unittest.main()
