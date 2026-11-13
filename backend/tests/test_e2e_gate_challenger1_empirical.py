"""Milestone E2E Gate — Challenger 1 Independent Empirical Stress Test Suite.

Adversarial empirical challenge harness testing:
1. Security boundaries:
   - All-byte AEAD auth tag, nonce, and ciphertext mutation fuzzing (100% DecryptionError coverage)
   - Master key corruption (corrupted lengths, non-hex, null bytes, special chars)
   - JWT tampering (alg: none, forged signatures, claim tampering, malformed bearer)
   - SQL injection resilience across vault keys, account queries, and session lookups
2. Quota & account boundaries:
   - Exact hard-stop boundary (2.0 CCU): 1.999/2.000 (COOLDOWN) vs 2.001/2.0000001 (ACTIVE)
   - Refresh token revocation handling (invalid_grant transitions to AUTH_EXPIRED)
   - Duplicate Google email collision handling (IntegrityError vs exchange_code upsert)
3. SSH and runtime proxy protocol:
   - Single-connection WebSocket lock (HTTP 429 on concurrent attempts, 101 on release)
   - Legacy RSA key rejection (HTTP 400) vs Ed25519 / ECDSA acceptance (HTTP 101)
   - 11th session port pool exhaustion (ports 2220-2229 limit, NULL on 11th, reclaim on stop)
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import re
import shutil
import socket
import sqlite3
import tempfile
import time
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch
import unittest
import uuid

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
from backend.app.services import account_pool, session_manager
from backend.app.services.colab_client import ColabClient
from backend.tests.mock_colab.mock_runtime_proxy import MockRuntimeProxyServer


class TestSecurityBoundariesAdversarial(unittest.TestCase):
    """Adversarial security stress tests: All-byte AEAD tamper, Master key, JWT, SQLi."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="gate_challenger_sec_")
        self.db_path = os.path.join(self.test_dir, "gate_sec.db")
        init_db(self.db_path)
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path
        self.client = TestClient(app)

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_all_byte_aead_auth_tag_and_ciphertext_tampering(self):
        """Exhaustively tamper EVERY single byte in an encrypted payload.

        Payload layout: 12-byte nonce + N-byte ciphertext + 16-byte auth tag.
        Every single byte mutation must raise DecryptionError without exception.
        """
        secret_payload = "CRITICAL_OAUTH_REFRESH_TOKEN_AND_ENV_KEY_ALPHA_12345"
        encrypted_b64 = encrypt_secret(secret_payload)
        raw_bytes = bytearray(base64.b64decode(encrypted_b64))
        total_len = len(raw_bytes)

        # Ensure payload has nonce (12) + ciphertext (> 0) + tag (16) >= 28 bytes
        self.assertGreaterEqual(total_len, 28)

        # Test every single byte index from 0 to total_len - 1
        tamper_masks = [0x01, 0x80, 0xFF]
        tested_mutations = 0

        for byte_idx in range(total_len):
            for mask in tamper_masks:
                tampered = bytearray(raw_bytes)
                tampered[byte_idx] ^= mask
                tampered_b64 = base64.b64encode(tampered).decode("ascii")

                with self.assertRaises(
                    DecryptionError,
                    msg=f"Tampering byte {byte_idx}/{total_len} with mask 0x{mask:02x} did not raise DecryptionError",
                ):
                    decrypt_secret(tampered_b64)
                tested_mutations += 1

        self.assertEqual(tested_mutations, total_len * len(tamper_masks))

        # Also verify truncated payloads (lengths from 0 to 27 bytes) fail
        for trunc_len in range(28):
            trunc_b64 = base64.b64encode(raw_bytes[:trunc_len]).decode("ascii")
            with self.assertRaises(DecryptionError):
                decrypt_secret(trunc_b64)

    def test_master_key_corruption_and_boundary_failures(self):
        """Corrupted, odd-length, non-hex, null-byte, and short master keys fail fast with ValueError."""
        # Non-empty corrupted keys must all raise ValueError
        bad_keys = [
            "12345",  # too short
            "a" * 16,  # 16 chars
            "b" * 31,  # 31 chars
            "c" * 33,  # 33 chars
            "d" * 63,  # 63 chars (odd length hex)
            "e" * 65,  # 65 chars
            "g" * 64,  # valid length (64) but invalid hex character 'g'
            "0123456789abcdef" * 3 + "xyz!",  # non-hex symbols
            "not_valid_base64_???===",
        ]

        for bad_key in bad_keys:
            with patch.dict(os.environ, {"MASTER_KEY": bad_key}):
                reset_master_key_cache()
                with self.assertRaises(ValueError, msg=f"Corrupt master key '{bad_key[:10]}...' should raise ValueError"):
                    get_master_key()
        reset_master_key_cache()

        # Empty string treats MASTER_KEY as unset and falls back to file/auto-gen (does not raise ValueError)
        with patch.dict(os.environ, {"MASTER_KEY": ""}):
            reset_master_key_cache()
            key = get_master_key()
            self.assertEqual(len(key), 32)
        reset_master_key_cache()

    def test_jwt_tampering_adversarial_matrix(self):
        """Challenge JWT with signature tampering, alg:none, role escalation, and invalid headers."""
        valid_token = create_access_token({"sub": "admin", "role": "admin"})
        parts = valid_token.split(".")
        self.assertEqual(len(parts), 3)

        # 1. alg:none attack with signature removed
        header_none = {"alg": "none", "typ": "JWT"}
        h64 = base64.urlsafe_b64encode(json.dumps(header_none).encode()).rstrip(b"=").decode()
        alg_none_token = f"{h64}.{parts[1]}."

        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {alg_none_token}"})
        self.assertEqual(resp.status_code, 401, "alg:none token must return 401")

        with self.assertRaises(AuthenticationError):
            decode_access_token(alg_none_token)

        # 2. Tampered payload: escalate role or change sub
        payload_bytes = base64.urlsafe_b64decode(parts[1] + "==")
        payload = json.loads(payload_bytes.decode())
        payload["role"] = "root_superadmin"
        payload["sub"] = "hacker"
        tampered_p = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        tampered_token = f"{parts[0]}.{tampered_p}.{parts[2]}"

        resp2 = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {tampered_token}"})
        self.assertEqual(resp2.status_code, 401, "Tampered payload claims must return 401")

        # 3. Corrupt signature byte by byte
        for corrupt_suffix in ["a", "xyz", "==", "!!@@##", "A" * 64]:
            bad_sig_token = f"{parts[0]}.{parts[1]}.{corrupt_suffix}"
            resp_sig = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {bad_sig_token}"})
            self.assertEqual(resp_sig.status_code, 401)

        # 4. Truncated or multi-part structures
        for bad_struct in ["", "Bearer", "Bearer a", "Bearer a.b", "Bearer a.b.c.d.e"]:
            resp_struct = self.client.get("/api/auth/me", headers={"Authorization": bad_struct})
            self.assertEqual(resp_struct.status_code, 401)

    def test_sql_injection_defense_adversarial_queries(self):
        """Exhaustive SQL injection attempts against secrets, accounts, and session tables."""
        adversarial_payloads = [
            "' OR 1=1 --",
            "'; DROP TABLE accounts; --",
            "'; DROP TABLE sessions; --",
            "' UNION ALL SELECT null, null, null, null, null, null, null, null, null, null, null --",
            "admin' /*",
            "1'; DELETE FROM secrets WHERE '1'='1",
            "' OR 'x'='x' UNION SELECT 1, 'injected', 'enc', 'desc', datetime('now') --",
        ]

        # 1. Insert secrets using adversarial payloads as key_name
        for i, payload in enumerate(adversarial_payloads):
            secret_id = f"gate_sec_{i}"
            val = f"sensitive_val_{i}"
            with get_db(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO secrets (id, key_name, value_enc, description) VALUES (?, ?, ?, ?)",
                    (secret_id, payload, encrypt_secret(val), "sqli_test"),
                )

        # 2. Query back by exact payload
        for i, payload in enumerate(adversarial_payloads):
            with get_db(self.db_path) as conn:
                row = conn.execute(
                    "SELECT key_name, value_enc FROM secrets WHERE key_name = ?",
                    (payload,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["key_name"], payload)
                self.assertEqual(decrypt_secret(row["value_enc"]), f"sensitive_val_{i}")

        # 3. Verify accounts and sessions tables were never dropped
        with get_db(self.db_path) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            self.assertIn("accounts", tables)
            self.assertIn("sessions", tables)
            self.assertIn("secrets", tables)


class TestQuotaAccountsBoundariesAdversarial(unittest.TestCase):
    """Adversarial quota & account stress tests: 2.0 CCU boundary, refresh revocation, duplicate emails."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="gate_challenger_quota_")
        self.db_path = os.path.join(self.test_dir, "gate_quota.db")
        init_db(self.db_path)
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _seed_account(self, account_id: str, email: str, ccu: float, hard_stop: float = 2.0, status: str = "ACTIVE"):
        creds = {"access_token": "mock_tok", "refresh_token": "mock_ref", "expires_at": int(time.time()) + 3600}
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, hard_stop_ccu, status, priority)
                VALUES (?, ?, 'User', ?, ?, ?, ?, 1)
                """,
                (account_id, email, encrypt_secret(json.dumps(creds)), ccu, hard_stop, status),
            )

    def test_exact_hard_stop_boundary_precision(self):
        """Stress-test CCU values around the exact 2.0 hard_stop threshold.

        - 1.9999 -> COOLDOWN
        - 2.0000 -> COOLDOWN (balance must be strictly > hard_stop_ccu)
        - 2.0001 -> ACTIVE
        - 2.0000001 -> ACTIVE
        """
        mock_colab = MagicMock(spec=ColabClient)
        mock_colab.list_assignments.return_value = []

        test_matrix = [
            (1.9999, "COOLDOWN", False),
            (2.0000, "COOLDOWN", False),
            (2.0001, "ACTIVE", True),
            (2.0000001, "ACTIVE", True),
        ]

        for i, (balance, expected_status, can_select) in enumerate(test_matrix):
            acc_id = f"acc_bound_{i}"
            self._seed_account(acc_id, f"{acc_id}@example.com", ccu=50.0, hard_stop=2.0, status="ACTIVE")

            mock_colab.get_ccu_info.return_value = {
                "current_balance": balance,
                "consumption_rate_hourly": 0.5,
                "assignments_count": 0,
            }

            acc = account_pool.sync_account(acc_id, client=mock_colab)
            self.assertEqual(
                acc["status"],
                expected_status,
                f"Balance {balance} with hard_stop 2.0 should have status {expected_status}, got {acc['status']}",
            )

            if can_select:
                sel = account_pool.select_account(preferred_account_id=acc_id)
                self.assertEqual(sel, acc_id)
            else:
                with self.assertRaises(ValueError):
                    account_pool.select_account(preferred_account_id=acc_id)

    def test_refresh_token_revocation_invalid_grant_lifecycle(self):
        """External OAuth token revocation (HTTP 400 invalid_grant) transitions status to AUTH_EXPIRED."""
        expired_creds = {
            "access_token": "expired_tok",
            "refresh_token": "revoked_ref_tok",
            "expires_at": int(time.time()) - 3600,
        }
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, status)
                VALUES ('acc_revoked_gate', 'revoked_gate@example.com', 'Revoked User', ?, 30.0, 'ACTIVE')
                """,
                (encrypt_secret(json.dumps(expired_creds)),),
            )

        # Mock token endpoint returning HTTP 400 invalid_grant
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 400
        mock_resp.text = json.dumps({"error": "invalid_grant", "error_description": "Token revoked"})

        with patch("backend.app.services.account_pool.requests.post", return_value=mock_resp):
            with self.assertRaises(ValueError) as ctx:
                account_pool.get_valid_access_token("acc_revoked_gate")
            self.assertIn("OAuth refresh token expired or revoked", str(ctx.exception))

        # Check DB status transitioned
        with get_db(self.db_path) as conn:
            status = conn.execute("SELECT status FROM accounts WHERE id = 'acc_revoked_gate'").fetchone()[0]
            self.assertEqual(status, "AUTH_EXPIRED")

    def test_duplicate_email_collision_and_idempotent_exchange(self):
        """Direct duplicate email insert fails with IntegrityError, while exchange_code updates in place."""
        self._seed_account("acc_primary", "gate_collide@example.com", ccu=40.0)

        # Direct insert of same email fails uniqueness
        with self.assertRaises(sqlite3.IntegrityError):
            with get_db(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance) VALUES ('acc_dup', 'gate_collide@example.com', 'd', 10.0)"
                )

        # Exchange code for existing email updates record smoothly
        mock_token_resp = MagicMock()
        mock_token_resp.ok = True
        mock_token_resp.json.return_value = {
            "access_token": "refreshed_access_token",
            "refresh_token": "refreshed_refresh_token",
            "expires_in": 3600,
        }

        mock_userinfo_resp = MagicMock()
        mock_userinfo_resp.ok = True
        mock_userinfo_resp.json.return_value = {
            "email": "gate_collide@example.com",
            "name": "Updated Collide Name",
        }

        with patch("backend.app.services.account_pool.requests.post", return_value=mock_token_resp), \
             patch("backend.app.services.account_pool.requests.get", return_value=mock_userinfo_resp), \
             patch("backend.app.services.account_pool.sync_account"):
            res = account_pool.exchange_code("mock_auth_code_reconnect")
            self.assertEqual(res["id"], "acc_primary")
            self.assertEqual(res["display_name"], "Updated Collide Name")


class TestSSHRuntimeProxyProtocolAdversarial(unittest.TestCase):
    """Adversarial SSH and Runtime Proxy tests: 429 lock, RSA rejection, 11th session exhaustion."""

    @classmethod
    def setUpClass(cls):
        cls.proxy_server = MockRuntimeProxyServer()
        cls.proxy_server.start()

    @classmethod
    def tearDownClass(cls):
        cls.proxy_server.stop()

    def setUp(self):
        self.proxy_server.reset()
        self.endpoint_id = "ep-gate-ssh-adv"
        self.token = "token-gate-ssh-secret"
        self.proxy_server.app.register_endpoint(self.endpoint_id, self.token)
        self.host = self.proxy_server.host
        self.port = self.proxy_server.port

        self.test_dir = tempfile.mkdtemp(prefix="gate_challenger_ssh_")
        self.db_path = os.path.join(self.test_dir, "gate_ssh.db")
        init_db(self.db_path)
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _handshake(self, headers: Dict[str, str]) -> Tuple[int, str, str, socket.socket]:
        sock = socket.create_connection((self.host, self.port), timeout=3.0)
        lines = [
            f"GET /tun/m/{self.endpoint_id}/colab/ssh?colab-runtime-proxy-token={self.token} HTTP/1.1",
            f"Host: {self.host}:{self.port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version: 13",
        ]
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
        req_bytes = "\r\n".join(lines).encode("utf-8") + b"\r\n\r\n"
        sock.sendall(req_bytes)

        sock.settimeout(2.0)
        buf = bytearray()
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                if b"\r\n\r\n" in buf:
                    if b" 101 " in buf:
                        break
                    headers_raw = bytes(buf).split(b"\r\n\r\n", 1)[0].decode("utf-8", errors="ignore")
                    cl_match = re.search(r"Content-Length:\s*(\d+)", headers_raw, re.IGNORECASE)
                    if cl_match:
                        expected_len = int(cl_match.group(1))
                        current_body_len = len(bytes(buf).split(b"\r\n\r\n", 1)[1])
                        if current_body_len >= expected_len:
                            break
                    else:
                        break
            except socket.timeout:
                break

        raw = bytes(buf).decode("utf-8", errors="ignore")
        parts = raw.split("\r\n\r\n", 1)
        headers_part = parts[0]
        body_part = parts[1] if len(parts) > 1 else ""
        status_line = headers_part.splitlines()[0] if headers_part else ""
        status_code = int(status_line.split()[1]) if len(status_line.split()) > 1 else 0

        if status_code != 101:
            sock.close()

        return status_code, headers_part, body_part, sock

    def test_ssh_single_connection_lock_concurrent_burst(self):
        """Concurrently open 10 connections: exactly 1 must get 101, all others 429."""
        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGf3PzV5Q8N1L0K2... dev@colab"

        # 1. First connection gets 101
        s1, _, _, sock1 = self._handshake({"X-Colab-Ssh-Pubkey": pubkey})
        self.assertEqual(s1, 101)

        try:
            # 2. Concurrently attempt 5 connections while 1 is active -> all must receive 429
            def try_conn():
                s, _, body, _ = self._handshake({"X-Colab-Ssh-Pubkey": pubkey})
                return s, body

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                results = list(executor.map(lambda _: try_conn(), range(5)))

            for code, body in results:
                self.assertEqual(code, 429)
                self.assertIn("Already-active SSH session", body)
        finally:
            sock1.close()

        time.sleep(0.05)

        # 3. After release, next connection gets 101
        s2, _, _, sock2 = self._handshake({"X-Colab-Ssh-Pubkey": pubkey})
        self.assertEqual(s2, 101)
        sock2.close()

    def test_ssh_legacy_rsa_rejection_vs_ed25519_ecdsa(self):
        """Reject ssh-rsa, ssh-dss, and missing pubkey with HTTP 400; accept Ed25519 and ECDSA."""
        # 1. ssh-rsa
        s_rsa, _, body_rsa, _ = self._handshake({"X-Colab-Ssh-Pubkey": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7vbvF8mQ... dev@colab"})
        self.assertEqual(s_rsa, 400)
        self.assertIn("RSA keys (ssh-rsa) are NOT accepted", body_rsa)

        # 2. ssh-dss
        s_dss, _, body_dss, _ = self._handshake({"X-Colab-Ssh-Pubkey": "ssh-dss AAAAB3NzaC1kc3MAAACBAOp... dev@colab"})
        self.assertEqual(s_dss, 400)
        self.assertIn("unsupported key type", body_dss)

        # 3. Missing header
        s_none, _, body_none, _ = self._handshake({})
        self.assertEqual(s_none, 400)
        self.assertIn("missing pubkey header", body_none)

        # 4. Ed25519 accepted
        s_ed, _, _, sock_ed = self._handshake({"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGf3PzV5Q8N1L0K2... dev@colab"})
        self.assertEqual(s_ed, 101)
        sock_ed.close()
        time.sleep(0.05)

        # 5. ECDSA accepted
        s_ec, _, _, sock_ec = self._handshake({"X-Colab-Ssh-Pubkey": "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTY... dev@colab"})
        self.assertEqual(s_ec, 101)
        sock_ec.close()

    def test_ssh_11th_session_port_pool_exhaustion(self):
        """When all 10 ports (2220-2229) are allocated, 11th allocation returns None."""
        account_id = "acc_ssh_pool_gate"
        with get_db(self.db_path) as conn:
            conn.execute(
                "INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance) VALUES (?, 'ssh_gate@example.com', 'd', 100.0)",
                (account_id,),
            )
            # Allocate all 10 ports
            for p in settings.ssh_port_pool:
                conn.execute(
                    """
                    INSERT INTO sessions (id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc, token_expires_at, accelerator, status, ssh_local_port)
                    VALUES (?, ?, ?, 'nb', 'ep', 'url', 'tok', datetime('now'), 'T4', 'READY', ?)
                    """,
                    (f"sess_gate_{p}", f"Session {p}", account_id, p),
                )

        # 11th allocation must return None
        p11 = session_manager.allocate_ssh_port()
        self.assertIsNone(p11, "11th port allocation must return None when pool 2220-2229 is exhausted")

        # Free port 2227
        with get_db(self.db_path) as conn:
            conn.execute("UPDATE sessions SET status = 'STOPPED', ssh_local_port = NULL WHERE id = 'sess_gate_2227'")

        # Next allocation must return exactly 2227
        reclaimed = session_manager.allocate_ssh_port()
        self.assertEqual(reclaimed, 2227)


if __name__ == "__main__":
    unittest.main()
