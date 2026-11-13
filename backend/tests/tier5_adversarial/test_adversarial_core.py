"""Milestone 4 Phase 2: Tier 5 Adversarial Coverage Hardening Test Suite.

Exhaustive white-box adversarial stress testing across core modules:
1. Auth & AES-256-GCM Vault (backend/app/core/security.py, backend/app/api/routes_auth.py)
2. Stateless Colab Client & Error Mappings (backend/app/services/colab_client.py)
3. Account Pool & Orphan Reconciliation (backend/app/services/account_pool.py)
4. Session Allocation & Deallocation (backend/app/services/session_manager.py)
5. SSH Multiplexer Gateway Ports 2220-2229 (backend/app/services/ssh_multiplexer.py)
6. Lifecycle Engine & Quota Circuit Breaker (backend/app/services/lifecycle_engine.py)

Hermetic offline execution with MockColab suite and isolated SQLite instances.
"""

from __future__ import annotations

import asyncio
import base64
import calendar
import concurrent.futures
from datetime import datetime, timezone
import json
import logging
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.parse
import uuid

from fastapi.testclient import TestClient
import requests

from backend.app.config import settings
from backend.app.core.database import (
    check_db_health,
    get_db,
    get_db_connection,
    init_db,
)
from backend.app.core.models import SessionCreateRequest
from backend.app.core.security import (
    AuthenticationError,
    DecryptionError,
    SecurityError,
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
from backend.app.services import account_pool, session_manager
from backend.app.services.account_pool import (
    delete_account,
    get_account,
    get_valid_access_token,
    list_accounts,
    select_account,
    sync_account,
)
from backend.app.services.colab_client import (
    HIGH_MEM_ONLY_ACCELERATORS,
    ColabClient,
    ColabClientError,
    QuotaExceededError,
    TooManyAssignmentsError,
    generate_colab_notebook_url,
    strip_xssi_prefix,
    uuid_to_web_safe_base64,
)
from backend.app.services.lifecycle_engine import (
    InteractiveIdleWatchdog,
    LifecycleEngine,
    QuotaCircuitBreakerWatchdog,
    lifecycle_engine,
)
from backend.app.services.ssh_multiplexer import (
    ColabSSHGateway,
    SSHMultiplexerService,
    SSHPortManager,
    UpstreamSSHAuthError,
    UpstreamSSHBusyError,
    UpstreamSSHError,
    ssh_multiplexer_service,
)
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


# ============================================================================
# 1. Auth & AES-256-GCM Vault Adversarial Stress
# ============================================================================

class TestAuthVaultAdversarialStress(unittest.TestCase):
    """Adversarial stress testing for AES-256-GCM Vault and JWT Authentication."""

    def setUp(self):
        reset_master_key_cache()
        self.test_dir = tempfile.mkdtemp(prefix="adv_vault_")
        self.key_file = os.path.join(self.test_dir, ".master_key")
        self.orig_key_path = settings.MASTER_KEY_PATH
        self.orig_master_key_env = os.environ.get("MASTER_KEY")
        settings.MASTER_KEY_PATH = self.key_file
        if "MASTER_KEY" in os.environ:
            del os.environ["MASTER_KEY"]

        # Setup temporary isolated DB
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)
        self.client = TestClient(app)

    def tearDown(self):
        settings.MASTER_KEY_PATH = self.orig_key_path
        settings.DATABASE_PATH = self.orig_db
        if self.orig_master_key_env is not None:
            os.environ["MASTER_KEY"] = self.orig_master_key_env
        elif "MASTER_KEY" in os.environ:
            del os.environ["MASTER_KEY"]
        reset_master_key_cache()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_key_derivation_formats_and_corner_cases(self):
        """Adversarial key derivation: hex, raw 32 bytes, base64, whitespace, and invalid formats."""
        # Case A: 64-char hex string (lowercase and uppercase)
        raw_key = os.urandom(32)
        hex_key = raw_key.hex()
        os.environ["MASTER_KEY"] = hex_key
        reset_master_key_cache()
        self.assertEqual(get_master_key(), raw_key)

        # Case B: Whitespace padded hex key
        os.environ["MASTER_KEY"] = f"  {hex_key.upper()}  \n"
        reset_master_key_cache()
        self.assertEqual(get_master_key(), raw_key)

        # Case C: 32 raw bytes UTF-8 string
        raw_32_str = "12345678901234567890123456789012"
        os.environ["MASTER_KEY"] = raw_32_str
        reset_master_key_cache()
        self.assertEqual(get_master_key(), raw_32_str.encode("utf-8"))

        # Case D: Base64-encoded 32 bytes
        b64_key = base64.b64encode(raw_key).decode("ascii")
        os.environ["MASTER_KEY"] = b64_key
        reset_master_key_cache()
        self.assertEqual(get_master_key(), raw_key)

        # Case E: Invalid lengths & encodings must fail fast with ValueError
        invalid_keys = [
            "short_key",
            "a" * 63,  # 63 hex chars (odd/incomplete)
            "a" * 65,  # 65 hex chars (overflow)
            "z" * 64,  # Non-hex characters
            base64.b64encode(os.urandom(16)).decode("ascii"),  # 16 bytes base64
            base64.b64encode(os.urandom(48)).decode("ascii"),  # 48 bytes base64
            "invalid!@#$%^&*()_+base64===",
        ]
        for inv in invalid_keys:
            os.environ["MASTER_KEY"] = inv
            reset_master_key_cache()
            with self.assertRaises(ValueError, msg=f"Invalid key '{inv}' did not raise ValueError"):
                get_master_key()

    def test_iv_reuse_prevention_1000_encryptions(self):
        """Verify strict IV uniqueness: 1,000 encryptions of identical plaintext must yield 1,000 unique nonces."""
        key = os.urandom(32)
        plaintext = "sensitive-oauth-refresh-token-xyz-12345"
        nonces = set()
        ciphertexts = set()

        for _ in range(1000):
            encrypted = encrypt_secret(plaintext, key=key)
            raw = base64.b64decode(encrypted.encode("utf-8"))
            nonce = raw[:12]
            nonces.add(nonce)
            ciphertexts.add(encrypted)

        self.assertEqual(len(nonces), 1000, "IV collision detected! Nonces must be strictly unique.")
        self.assertEqual(len(ciphertexts), 1000, "Ciphertext collision detected for identical plaintext.")

    def test_tampering_adversarial_bitflips_and_payload_truncation(self):
        """Adversarial bit flips and length boundary checks across nonce, ciphertext, and tag."""
        key = os.urandom(32)
        plaintext = "super-secret-vault-data-payload-123"
        encrypted = encrypt_secret(plaintext, key=key)
        raw = base64.b64decode(encrypted.encode("utf-8"))

        # 1. Flip bits in every segment
        # First 12 bytes = Nonce
        for i in range(12):
            tampered = bytearray(raw)
            tampered[i] ^= 0x01
            tampered_b64 = base64.b64encode(tampered).decode("utf-8")
            with self.assertRaises(DecryptionError, msg=f"Nonce byte {i} tamper uncaught"):
                decrypt_secret(tampered_b64, key=key)

        # Ciphertext body
        for i in range(12, len(raw) - 16):
            tampered = bytearray(raw)
            tampered[i] ^= 0x80
            tampered_b64 = base64.b64encode(tampered).decode("utf-8")
            with self.assertRaises(DecryptionError, msg=f"Ciphertext byte {i} tamper uncaught"):
                decrypt_secret(tampered_b64, key=key)

        # Last 16 bytes = Auth Tag
        for i in range(len(raw) - 16, len(raw)):
            tampered = bytearray(raw)
            tampered[i] ^= 0xFF
            tampered_b64 = base64.b64encode(tampered).decode("utf-8")
            with self.assertRaises(DecryptionError, msg=f"Tag byte {i} tamper uncaught"):
                decrypt_secret(tampered_b64, key=key)

        # 2. Length boundary truncation: 0, 1, 11, 12, 27 bytes
        for length in [0, 1, 11, 12, 27]:
            truncated = raw[:length]
            b64_trunc = base64.b64encode(truncated).decode("utf-8")
            with self.assertRaises(DecryptionError, msg=f"Truncation to {length} bytes uncaught"):
                decrypt_secret(b64_trunc, key=key)

        # 3. Exactly 28 bytes = 12 nonce + 16 tag (empty plaintext payload)
        empty_enc = encrypt_secret("", key=key)
        empty_raw = base64.b64decode(empty_enc.encode("utf-8"))
        self.assertEqual(len(empty_raw), 28)
        self.assertEqual(decrypt_secret(empty_enc, key=key), "")

        # 4. Corrupted Base64 encoding
        with self.assertRaises(DecryptionError):
            decrypt_secret("not-valid-base64-!@#$%", key=key)

    def test_corrupted_vault_master_key_file_and_permissions(self):
        """Corrupted .master_key file contents, permission checks, and persistence."""
        # 1. Unset env so key file is read
        if "MASTER_KEY" in os.environ:
            del os.environ["MASTER_KEY"]
        reset_master_key_cache()

        # Case A: Unreadable key file raises SecurityError
        with open(self.key_file, "w") as f:
            f.write("corrupted_content\n")
        os.chmod(self.key_file, 0o000)
        try:
            with self.assertRaises(SecurityError):
                get_master_key()
        finally:
            os.chmod(self.key_file, 0o600)

        # Case B: Non-32/64 length content raises SecurityError (hardened against silent fallthrough)
        reset_master_key_cache()
        with open(self.key_file, "w") as f:
            f.write("corrupted_short_key\n")
        with self.assertRaises(SecurityError) as ctx:
            get_master_key()
        self.assertIn("Corrupted master key file", str(ctx.exception))

        # 2. Clean generation with 0600 permissions
        os.remove(self.key_file)
        reset_master_key_cache()
        key = get_master_key()
        self.assertEqual(len(key), 32)
        self.assertTrue(os.path.exists(self.key_file))

        # Check POSIX file permissions (0600 = user read/write only)
        file_mode = os.stat(self.key_file).st_mode & 0o777
        self.assertEqual(file_mode, 0o600, f"Master key file permissions should be 0600, got {oct(file_mode)}")

    def test_master_key_rotation_and_vault_reencryption(self):
        """Simulate master key rotation: old secrets fail under new key until re-encrypted."""
        key_old = os.urandom(32)
        key_new = os.urandom(32)

        secrets = {
            "oauth_token": "ya29.a0AfH6SMB-test-token-value-12345",
            "db_pass": "super_secret_db_pass_987",
            "api_key": "sk-ant-api03-abcdef123456789",
        }

        # Encrypt with key_old
        vault_old = {k: encrypt_secret(v, key=key_old) for k, v in secrets.items()}

        # Decrypting with key_new must fail with DecryptionError
        for k, enc_val in vault_old.items():
            with self.assertRaises(DecryptionError):
                decrypt_secret(enc_val, key=key_new)

        # Migration routine: decrypt with old, re-encrypt with new
        vault_new = {}
        for k, enc_val in vault_old.items():
            decrypted = decrypt_secret(enc_val, key=key_old)
            vault_new[k] = encrypt_secret(decrypted, key=key_new)

        # Decrypting with key_new must now succeed and match original plaintext
        for k, orig_plain in secrets.items():
            self.assertEqual(decrypt_secret(vault_new[k], key=key_new), orig_plain)

    def test_jwt_expiration_invalid_claims_and_algorithm_tampering(self):
        """Adversarial JWT stress: expired tokens, alg tampering, role spoofing, malformed headers."""
        # 1. Expired token (exp in past)
        expired_token = create_access_token({"sub": "admin", "role": "admin"}, expires_delta_seconds=-60)
        with self.assertRaises(AuthenticationError):
            decode_access_token(expired_token)

        # API endpoint /api/auth/me returns 401
        resp = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
        self.assertEqual(resp.status_code, 401)

        # 2. alg: none attack
        valid_token = create_access_token({"sub": "admin", "role": "admin"})
        parts = valid_token.split(".")
        header_none = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        none_token = f"{header_none}.{parts[1]}."
        resp_none = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {none_token}"})
        self.assertEqual(resp_none.status_code, 401)

        # 3. Role spoofing (role != "admin")
        viewer_token = create_access_token({"sub": "attacker", "role": "viewer"})
        resp_forbidden = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {viewer_token}"})
        self.assertEqual(resp_forbidden.status_code, 403, "Non-admin role must return 403 Forbidden")

        # 4. Malformed Authorization headers
        for malformed in ["", "Bearer", "Bearer ", "Basic dXNlcjpwYXNz", "Bearer invalid.format.three.parts.extra"]:
            resp_bad = self.client.get("/api/auth/me", headers={"Authorization": malformed})
            self.assertEqual(resp_bad.status_code, 401)

        # 5. Wrong admin password login rejection
        resp_login_fail = self.client.post("/api/auth/login", json={"password": "wrong_password_123"})
        self.assertEqual(resp_login_fail.status_code, 401)


# ============================================================================
# 2. Stateless Colab Client & Error Mappings Adversarial Stress
# ============================================================================

class TestColabClientAdversarialStress(unittest.TestCase):
    """Adversarial stress testing for Stateless Colab Client and protocol adapters."""

    @classmethod
    def setUpClass(cls):
        cls.hub = MockControlHub()
        cls.hub.start()
        cls.orig_control_url = settings.CONTROL_PLANE_URL
        settings.CONTROL_PLANE_URL = cls.hub.urls["control_plane"]

    @classmethod
    def tearDownClass(cls):
        settings.CONTROL_PLANE_URL = cls.orig_control_url
        cls.hub.stop()

    def setUp(self):
        self.hub.reset()
        self.client = ColabClient(base_url=settings.CONTROL_PLANE_URL, timeout=5.0)
        self.token = "mock-access-token-adversarial"

    def test_sudden_socket_disconnection_and_timeout(self):
        """Simulate sudden socket reset and timeout during assign probe and commit."""
        nbh = uuid_to_web_safe_base64(uuid.uuid4())

        # Test A: Network Timeout on GET probe
        with patch.object(self.client.session, "get", side_effect=requests.exceptions.Timeout("Read timed out")):
            with self.assertRaises(ColabClientError) as ctx:
                self.client.assign(self.token, nbh, max_retries=1, backoff_sec=0.01)
            self.assertIn("Network error", str(ctx.exception))

        # Test B: Connection Reset on GET probe
        with patch.object(self.client.session, "get", side_effect=requests.exceptions.ConnectionError("Connection reset by peer")):
            with self.assertRaises(ColabClientError) as ctx:
                self.client.assign(self.token, nbh, max_retries=1, backoff_sec=0.01)
            self.assertIn("Network error", str(ctx.exception))

    def test_malformed_and_truncated_http_payloads(self):
        """Malformed JSON, truncated streams, and missing XSRF tokens from Colab control plane."""
        # 1. Truncated JSON response
        mock_resp = requests.Response()
        mock_resp.status_code = 200
        mock_resp._content = b")]}'\n{\"assignments\": [{\"endpoint\": \"ep-123\", \"runtimeProxyInfo\": {"
        with patch.object(self.client.session, "get", return_value=mock_resp):
            with self.assertRaises(ColabClientError) as ctx:
                self.client.list_assignments(self.token)
            self.assertIn("Failed to parse JSON response", str(ctx.exception))

        # 2. Assign probe GET missing xsrf token
        mock_probe_resp = requests.Response()
        mock_probe_resp.status_code = 200
        mock_probe_resp._content = b")]}'\n{\"acc\": \"T4\", \"variant\": 1}"  # Missing "token"
        with patch.object(self.client.session, "get", return_value=mock_probe_resp):
            with self.assertRaises(ColabClientError) as ctx:
                self.client.assign(self.token, "nbh_test_missing_token", max_retries=0)
            self.assertIn("Missing token in GET assign probe response", str(ctx.exception))

        # 3. Empty 200 OK body
        mock_empty_resp = requests.Response()
        mock_empty_resp.status_code = 200
        mock_empty_resp._content = b""
        with patch.object(self.client.session, "get", return_value=mock_empty_resp):
            res = self.client._parse_response(mock_empty_resp)
            self.assertEqual(res, {})

    def test_http_429_412_400_error_mappings(self):
        """Verify strict mapping: 400 -> QuotaExceededError, 412 -> TooManyAssignmentsError, 429 -> ColabClientError(429)."""
        nbh = uuid_to_web_safe_base64(uuid.uuid4())

        # HTTP 400 Quota Exhaustion
        self.hub.trigger_quota_exhaustion("T4")
        with self.assertRaises(QuotaExceededError) as ctx:
            self.client.assign(self.token, nbh, accelerator="T4")
        self.assertEqual(ctx.exception.status_code, 400)
        self.hub.clear_quota_exhaustion()

        # HTTP 412 Concurrency Limit
        self.hub.trigger_concurrency_limit(0)
        with self.assertRaises(TooManyAssignmentsError) as ctx:
            self.client.assign(self.token, nbh, accelerator="T4")
        self.assertEqual(ctx.exception.status_code, 412)
        self.hub.trigger_concurrency_limit(None)

        # HTTP 429 Rate Limit from Colab control plane
        mock_429 = requests.Response()
        mock_429.status_code = 429
        mock_429._content = b")]}'\n{\"error\": \"Rate limit exceeded\"}"
        with patch.object(self.client.session, "get", return_value=mock_429):
            with self.assertRaises(ColabClientError) as ctx:
                self.client.get_ccu_info(self.token)
            self.assertEqual(ctx.exception.status_code, 429)

    def test_http_502_503_retry_and_reconciliation(self):
        """Simulate transient 502 Bad Gateway during assign commit and verify probe reconciliation."""
        nbh = uuid_to_web_safe_base64(uuid.uuid4())

        # Trigger 1 transient 502 on commit POST, but assignment is created in background (reconcile=True)
        self.hub.simulate_server_error(count=1, status_code=502, reconcile=True)

        # ColabClient should catch 502, back off, probe GET on attempt 2, discover existing assignment, and return it
        res = self.client.assign(self.token, nbh, accelerator="T4", max_retries=2, backoff_sec=0.01)
        self.assertIn("endpoint", res)
        self.assertIn("runtimeProxyInfo", res)

    def test_accelerator_shape_rule_omissions(self):
        """High-memory accelerators (L4, V5E1, V6E1) must strictly omit shape=hm."""
        captured_params = []

        def fake_get(url, params=None, **kwargs):
            captured_params.append(params.copy())
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b")]}'\n{\"token\": \"fake-xsrf\", \"acc\": \"L4\"}"
            return resp

        def fake_post(url, params=None, **kwargs):
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b")]}'\n{\"endpoint\": \"ep-l4\", \"runtimeProxyInfo\": {\"url\": \"http://proxy\", \"token\": \"pt\"}}"
            return resp

        with patch.object(self.client.session, "get", side_effect=fake_get), \
             patch.object(self.client.session, "post", side_effect=fake_post):
            # 1. L4 with shape="hm" -> shape MUST BE OMITTED
            self.client.assign(self.token, "nbh-l4", accelerator="L4", shape="hm")
            self.assertNotIn("shape", captured_params[0])

            # 2. T4 with shape="hm" -> shape MUST BE INCLUDED
            self.client.assign(self.token, "nbh-t4", accelerator="T4", shape="hm")
            self.assertEqual(captured_params[1].get("shape"), "hm")

    def test_runtime_data_plane_error_mappings(self):
        """Runtime VFS REST API error conditions: 401, 403, 404 idempotent delete."""
        endpoint = "ep-test"
        proxy_token = "valid-mock-proxy-token"
        self.hub.runtime_proxy_server.app.register_endpoint(endpoint, proxy_token)
        proxy_url = self.hub.urls["runtime_proxy"] + f"/tun/m/{endpoint}"

        # 1. Delete on non-existent path returns True (idempotent)
        res = self.client.delete_file(proxy_url, proxy_token, path="non_existent_file.txt")
        self.assertTrue(res)

        # 2. Download on non-existent path raises ColabClientError(404)
        with self.assertRaises(ColabClientError) as ctx:
            self.client.download_file(proxy_url, proxy_token, path="does_not_exist_404.txt")
        self.assertEqual(ctx.exception.status_code, 404)

        # 3. Request with wrong token returns 401
        with self.assertRaises(ColabClientError) as ctx:
            self.client.download_file(proxy_url, "invalid-proxy-token", path="sample_data/README.md")
        self.assertEqual(ctx.exception.status_code, 401)


# ============================================================================
# 3. Account Pool & Orphan Reconciliation Adversarial Stress
# ============================================================================

class TestAccountPoolAdversarialStress(unittest.TestCase):
    """Adversarial stress testing for Account Pool, thundering herd, and orphan discovery."""

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
        self.test_dir = tempfile.mkdtemp(prefix="adv_acc_")
        self.db_path = os.path.join(self.test_dir, "test_acc.db")
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _seed_account(
        self,
        acc_id: str,
        email: str,
        ccu_balance: float = 50.0,
        hard_stop: float = 2.0,
        priority: int = 1,
        active_assignments: int = 0,
        status: str = "ACTIVE",
        expires_in: int = 3600,
    ) -> str:
        creds = {
            "access_token": f"mock-token-{acc_id}",
            "refresh_token": f"mock-refresh-{acc_id}",
            "expires_at": int(time.time()) + expires_in,
        }
        enc_creds = encrypt_secret(json.dumps(creds))
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                    active_assignments, warning_ccu, hard_stop_ccu, priority, status
                ) VALUES (?, ?, ?, ?, ?, 1.5, ?, 10.0, ?, ?, ?)
                """,
                (acc_id, email, email.split("@")[0], enc_creds, ccu_balance, active_assignments, hard_stop, priority, status),
            )
        return acc_id

    def test_concurrent_account_selection_thundering_herd(self):
        """20 concurrent threads selecting accounts simultaneously under competing load."""
        # Seed 3 accounts with varying scores:
        # Score = (priority * 1000) + (ccu_balance * 10) - (active_assignments * 200)
        # acc_high: priority 3, ccu 100, active 0 -> Score = 3000 + 1000 = 4000
        # acc_med:  priority 1, ccu 50, active 0  -> Score = 1000 + 500 = 1500
        # acc_low:  priority 1, ccu 1.0 (<= hard_stop 2.0) -> Ineligible!
        self._seed_account("acc_high", "high@gmail.com", ccu_balance=100.0, priority=3)
        self._seed_account("acc_med", "med@gmail.com", ccu_balance=50.0, priority=1)
        self._seed_account("acc_low", "low@gmail.com", ccu_balance=1.0, hard_stop=2.0, priority=1)

        results = []
        errors = []

        def worker():
            try:
                selected = select_account()
                results.append(selected)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(len(results), 20)
        # All 20 threads must deterministically select acc_high (highest score)
        self.assertTrue(all(r == "acc_high" for r in results))

    def test_cooldown_timestamp_boundaries(self):
        """Exact boundary transitions: ccu_balance == hard_stop_ccu, <, and >."""
        acc_id = self._seed_account("acc_bnd", "boundary@gmail.com", ccu_balance=5.0, hard_stop=2.0)

        # Boundary A: ccu_balance == hard_stop_ccu (2.0) -> Status becomes COOLDOWN
        self.hub.control_plane_server.state.set_ccu(2.0, 1.0)
        sync_account(acc_id)
        acc = get_account(acc_id)
        self.assertEqual(acc["status"], "COOLDOWN")

        # Boundary B: ccu_balance < hard_stop_ccu (1.999) -> Remains COOLDOWN
        self.hub.control_plane_server.state.set_ccu(1.999, 1.0)
        sync_account(acc_id)
        acc = get_account(acc_id)
        self.assertEqual(acc["status"], "COOLDOWN")

        # Boundary C: Recharge above hard stop (2.001) -> Recovers to ACTIVE
        self.hub.control_plane_server.state.set_ccu(2.001, 1.0)
        sync_account(acc_id)
        acc = get_account(acc_id)
        self.assertEqual(acc["status"], "ACTIVE")

    def test_token_refresh_failures_mid_checkout(self):
        """Token expiring in < 300s triggers refresh; handle HTTP 400 invalid_grant."""
        # Seed account with token expiring in 10 seconds
        acc_id = self._seed_account("acc_exp", "exp@gmail.com", expires_in=10)

        # Configure mock OAuth server to reject refresh with HTTP 400
        mock_resp = requests.Response()
        mock_resp.status_code = 400
        mock_resp._content = b'{"error": "invalid_grant", "error_description": "Token revoked"}'

        with patch("requests.post", return_value=mock_resp):
            with self.assertRaises(ValueError) as ctx:
                get_valid_access_token(acc_id)
            self.assertIn("OAuth refresh token expired or revoked", str(ctx.exception))

            # Status must transition to AUTH_EXPIRED
            acc = get_account(acc_id)
            self.assertEqual(acc["status"], "AUTH_EXPIRED")

    def test_orphan_session_reconciliation_during_worker_crash_recovery(self):
        """Simulate daemon restart: discover running Colab instances not tracked in local DB."""
        acc_id = self._seed_account("acc_orphan", "colab-dev@gmail.com")

        # Inject 2 orphan assignments on Google Colab control plane with distinct 8-char prefixes
        ep1 = self.hub.control_plane_server.state.inject_orphan_assignment("alpha-ep-111", accelerator="T4")
        ep2 = self.hub.control_plane_server.state.inject_orphan_assignment("beta-ep-222", accelerator="A100")

        # Sync account
        sync_account(acc_id)

        # Verify both orphans were recorded into sessions table with status 'ORPHAN'
        with get_db() as conn:
            rows = conn.execute(
                "SELECT endpoint, accelerator, status FROM sessions WHERE status = 'ORPHAN'"
            ).fetchall()
            endpoints = {r["endpoint"]: r["accelerator"] for r in rows}

        self.assertIn("alpha-ep-111", endpoints)
        self.assertIn("beta-ep-222", endpoints)
        self.assertEqual(endpoints["alpha-ep-111"], "T4")
        self.assertEqual(endpoints["beta-ep-222"], "A100")

    def test_orphan_name_truncation_collision_vulnerability(self):
        """Hardened: endpoints sharing 8-char prefix generate unique session names without collision."""
        acc_id = self._seed_account("acc_orphan_vuln", "colab-dev@gmail.com")

        # Inject 2 orphan assignments sharing first 8 characters ('ep-node-')
        self.hub.control_plane_server.state.inject_orphan_assignment("ep-node-gpu-alpha", accelerator="T4")
        self.hub.control_plane_server.state.inject_orphan_assignment("ep-node-gpu-beta", accelerator="T4")

        # Hardened orphan naming uses unique orphan_id to avoid UNIQUE constraint collision
        sync_account(acc_id)

        with get_db() as conn:
            rows = conn.execute(
                "SELECT name, endpoint, status FROM sessions WHERE account_id = ? ORDER BY endpoint",
                (acc_id,),
            ).fetchall()

        self.assertEqual(len(rows), 2)
        names = [r["name"] for r in rows]
        self.assertEqual(len(set(names)), 2, "Session names must be unique despite shared endpoint prefix")
        for name in names:
            self.assertTrue(name.startswith("orphan-"))

    def test_sqlite_locking_and_busy_retry_under_load(self):
        """High-concurrency read/write stress against SQLite WAL engine without deadlocks."""
        # Seed 5 accounts
        for i in range(5):
            self._seed_account(f"acc_wal_{i}", f"wal_{i}@gmail.com", ccu_balance=float(10 + i * 5))

        errors = []

        def worker(idx: int):
            for step in range(10):
                try:
                    with get_db() as conn:
                        conn.execute(
                            "UPDATE accounts SET ccu_balance = ccu_balance + 0.1 WHERE id = ?",
                            (f"acc_wal_{idx % 5}",),
                        )
                        rows = conn.execute("SELECT id, ccu_balance FROM accounts").fetchall()
                        _ = len(rows)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"SQLite concurrency failed with errors: {errors}")
        health = check_db_health()
        self.assertEqual(health["journal_mode"].lower(), "wal")


# ============================================================================
# 4. Session Allocation & Deallocation Adversarial Stress
# ============================================================================

class TestSessionManagerAdversarialStress(unittest.TestCase):
    """Adversarial stress testing for Session Manager, multi-user race conditions, and state machine."""

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
        self.test_dir = tempfile.mkdtemp(prefix="adv_sess_")
        self.db_path = os.path.join(self.test_dir, "test_sess.db")
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        # Seed default test account
        creds = {
            "access_token": "mock-token-sess",
            "refresh_token": "mock-refresh-sess",
            "expires_at": int(time.time()) + 7200,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                    active_assignments, warning_ccu, hard_stop_ccu, priority, status
                ) VALUES ('acc_sess_1', 'sess_dev@gmail.com', 'SessDev', ?, 100.0, 1.5, 0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (encrypt_secret(json.dumps(creds)),),
            )

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_multi_user_race_condition_capacity_one(self):
        """Concurrent allocation race when account capacity is strictly 1 (HTTP 412 on subsequent)."""
        # Set concurrency limit = 1 on Mock Control Plane
        self.hub.trigger_concurrency_limit(1)

        req = SessionCreateRequest(
            name="race-session",
            account_id="acc_sess_1",
            accelerator="T4",
            lifecycle_policy="interactive",
        )

        successes = []
        failures = []

        def allocate_worker(idx: int):
            try:
                r = SessionCreateRequest(
                    name=f"race-{idx}",
                    account_id="acc_sess_1",
                    accelerator="T4",
                    lifecycle_policy="interactive",
                )
                sess = session_manager.create_session(r)
                successes.append(sess)
            except TooManyAssignmentsError as e:
                failures.append(e)
            except Exception as e:
                failures.append(e)

        threads = [threading.Thread(target=allocate_worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly 1 request succeeds; remaining 4 fail with TooManyAssignmentsError
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 4)
        for f in failures:
            self.assertIsInstance(f, TooManyAssignmentsError)

        # Account active_assignments counter must be exactly 1
        acc = get_account("acc_sess_1")
        self.assertEqual(acc["active_assignments"], 1)

    def test_state_machine_invalid_transitions_and_idempotency(self):
        """Idempotent teardown and safe handling of non-existent or repeated stops."""
        req = SessionCreateRequest(name="sess-state", account_id="acc_sess_1", accelerator="T4")
        sess = session_manager.create_session(req)
        sess_id = sess["id"]

        # 1. First stop succeeds
        self.assertTrue(session_manager.stop_session(sess_id))

        # 2. Second stop on already STOPPED session is idempotent and returns True
        self.assertTrue(session_manager.stop_session(sess_id))

        # 3. Third stop is also idempotent
        self.assertTrue(session_manager.stop_session(sess_id))

        # 4. Account active_assignments must never drop below 0
        acc = get_account("acc_sess_1")
        self.assertEqual(acc["active_assignments"], 0)

        # 5. Stop on non-existent session raises ValueError
        with self.assertRaises(ValueError) as ctx:
            session_manager.stop_session("sess_does_not_exist_999")
        self.assertIn("not found", str(ctx.exception))

    def test_rapid_attach_detach_reattach_cycles(self):
        """Execute 10 rapid create and stop cycles to verify counter accuracy and port recycling."""
        for i in range(10):
            req = SessionCreateRequest(name=f"rapid-{i}", account_id="acc_sess_1", accelerator="T4")
            sess = session_manager.create_session(req)
            self.assertIsNotNone(sess.get("ssh_local_port"))
            session_manager.stop_session(sess["id"])

        acc = get_account("acc_sess_1")
        self.assertEqual(acc["active_assignments"], 0)
        sessions = session_manager.list_sessions(status_filter="STOPPED")
        self.assertEqual(len(sessions), 10)

    def test_concurrent_ssh_port_allocation_no_collisions(self):
        """Concurrently allocate ports to verify zero collision across pool 2220-2229."""
        allocated_ports = []
        lock = threading.Lock()

        def port_worker(idx: int):
            try:
                req = SessionCreateRequest(name=f"port-sess-{idx}", account_id="acc_sess_1", accelerator="T4")
                sess = session_manager.create_session(req)
                with lock:
                    allocated_ports.append(sess.get("ssh_local_port"))
            except Exception:
                pass

        threads = [threading.Thread(target=port_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All allocated non-null ports must be strictly unique
        valid_ports = [p for p in allocated_ports if p is not None]
        self.assertEqual(len(valid_ports), len(set(valid_ports)))
        for p in valid_ports:
            self.assertIn(p, range(2220, 2230))


# ============================================================================
# 5. SSH Multiplexer Gateway (Ports 2220-2229) Adversarial Stress
# ============================================================================

class TestSSHMultiplexerAdversarialStress(unittest.TestCase):
    """Adversarial stress testing for SSH Multiplexer, port pool exhaustion, and conflict recovery."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="adv_ssh_")
        self.db_path = os.path.join(self.test_dir, "test_ssh.db")
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)
        self.port_mgr = SSHPortManager(2220, 2229)

        # Seed account for foreign key integrity
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, hard_stop_ccu, status)
                VALUES ('acc_1', 'ssh_test@gmail.com', 'enc_creds', 50.0, 2.0, 'ACTIVE')
                """
            )

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _insert_session_with_port(self, session_id: str, port: Optional[int], status: str = "READY"):
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, shape,
                    lifecycle_policy, idle_timeout_sec, ssh_local_port, status
                ) VALUES (?, ?, 'acc_1', 'nbh', 'ep-1', 'http://proxy', 'enc', '2026-11-13 12:00:00', 'T4', 'standard', 'interactive', 1800, ?, ?)
                """,
                (session_id, f"name-{session_id}", port, status),
            )

    def test_dynamic_port_pool_exhaustion_11th_port(self):
        """When all 10 ports (2220-2229) are active, 11th request returns None and config handles exhaustion."""
        # Occupy all 10 ports
        for i, port in enumerate(range(2220, 2230)):
            self._insert_session_with_port(f"sess_occupied_{i}", port, status="READY")

        # 11th allocation must return None
        allocated = session_manager.allocate_ssh_port()
        self.assertIsNone(allocated, "Port pool must return None when all 10 ports are occupied")

        # Session with None port returns graceful message
        self._insert_session_with_port("sess_no_port", None, status="READY")
        cfg = session_manager.get_session_ssh_config("sess_no_port")
        self.assertIsNone(cfg["port"])
        self.assertEqual(cfg["connection_command"], "SSH port pool exhausted")
        self.assertEqual(cfg["vscode_config"], "")

    def test_rapid_port_allocation_and_immediate_release(self):
        """Allocate all 10 ports, stop the sessions, and verify immediate re-allocation availability."""
        # 1. Allocate 10
        for i, port in enumerate(range(2220, 2230)):
            self._insert_session_with_port(f"sess_release_{i}", port, status="READY")

        self.assertIsNone(session_manager.allocate_ssh_port())

        # 2. Mark all as STOPPED
        with get_db() as conn:
            conn.execute("UPDATE sessions SET status = 'STOPPED', ssh_local_port = NULL")

        # 3. Next allocation must now succeed starting back at 2220
        new_port = session_manager.allocate_ssh_port()
        self.assertEqual(new_port, 2220)

    def test_orphaned_and_stopped_session_port_cleanup(self):
        """Sessions in STOPPED, FAILED, and LOST state must not hold ports."""
        self._insert_session_with_port("sess_stopped", 2220, status="STOPPED")
        self._insert_session_with_port("sess_failed", 2221, status="FAILED")
        self._insert_session_with_port("sess_lost", 2222, status="LOST")

        # Ports 2220, 2221, 2222 should be considered free because status is terminal
        next_port = session_manager.allocate_ssh_port()
        self.assertEqual(next_port, 2220)

    def test_external_os_port_conflict_skip(self):
        """When an external OS process binds port 2220, allocator must skip it and choose 2221."""
        # Bind port 2220 locally with standard socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", 2220))
            sock.listen(1)
        except OSError:
            self.skipTest("Host port 2220 is already bound by system or another process")

        try:
            allocated = session_manager.allocate_ssh_port()
            self.assertEqual(allocated, 2221, "Allocator must skip OS-occupied port 2220 and allocate 2221")
        finally:
            sock.close()

    def test_upstream_http_429_single_connection_lock_detection(self):
        """Upstream WebSocket handshake rejection with HTTP 429 maps to UpstreamSSHBusyError."""
        gateway = ColabSSHGateway("sess_test", 2220, MagicMock(), MagicMock())

        # Simulate exception with status_code=429
        class MockWSException(Exception):
            status_code = 429

        with patch("websockets.connect", side_effect=MockWSException("Too Many Requests")):
            with patch.object(gateway, "_connect_upstream", side_effect=UpstreamSSHBusyError("Active session HTTP 429")):
                with self.assertRaises(UpstreamSSHBusyError):
                    asyncio.run(gateway.get_or_connect_upstream())


# ============================================================================
# 6. Lifecycle Engine & Quota Circuit Breaker Adversarial Stress
# ============================================================================

class TestLifecycleCircuitBreakerAdversarialStress(unittest.TestCase):
    """Adversarial stress testing for Lifecycle Engine, idle timeouts under clock drift, and circuit breaker."""

    @classmethod
    def setUpClass(cls):
        cls.hub = MockControlHub()
        cls.hub.start()
        cls.orig_control_url = settings.CONTROL_PLANE_URL
        settings.CONTROL_PLANE_URL = cls.hub.urls["control_plane"]

    @classmethod
    def tearDownClass(cls):
        settings.CONTROL_PLANE_URL = cls.orig_control_url
        cls.hub.stop()

    def setUp(self):
        self.hub.reset()
        self.test_dir = tempfile.mkdtemp(prefix="adv_life_")
        self.db_path = os.path.join(self.test_dir, "test_life.db")
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)
        self.watchdog = InteractiveIdleWatchdog()
        self.quota_breaker = QuotaCircuitBreakerWatchdog()

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _seed_account_and_session(
        self,
        acc_id: str,
        sess_id: str,
        balance: float = 50.0,
        hard_stop: float = 2.0,
        policy: str = "interactive",
        idle_timeout: int = 600,
    ):
        creds = {"access_token": "mock-token", "expires_at": int(time.time()) + 3600}
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, hard_stop_ccu, status)
                VALUES (?, ?, ?, ?, ?, 'ACTIVE')
                """,
                (acc_id, f"{acc_id}@gmail.com", encrypt_secret(json.dumps(creds)), balance, hard_stop),
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc,
                    token_expires_at, accelerator, lifecycle_policy, idle_timeout_sec, status
                ) VALUES (?, ?, ?, 'nbh', 'ep-1', 'http://proxy', 'enc', '2026-11-13 12:00:00', 'T4', ?, ?, 'READY')
                """,
                (sess_id, f"name-{sess_id}", acc_id, policy, idle_timeout),
            )

    def test_extreme_rapid_quota_expenditure_and_hard_stop(self):
        """Account balance drops abruptly below hard stop: circuit breaker stops running sessions."""
        self._seed_account_and_session("acc_q1", "sess_q1", balance=50.0, hard_stop=2.0)
        self._seed_account_and_session("acc_q2", "sess_q2", balance=20.0, hard_stop=2.0)

        # Abrupt quota drop on acc_q1 to 0.5 CCU (<= 2.0)
        with get_db() as conn:
            conn.execute("UPDATE accounts SET ccu_balance = 0.5 WHERE id = 'acc_q1'")

        # Enforce circuit breaker
        stopped = self.quota_breaker.evaluate_and_enforce_hard_stops()

        self.assertIn("sess_q1", stopped)
        self.assertNotIn("sess_q2", stopped)

        # acc_q1 marked as COOLDOWN
        acc1 = get_account("acc_q1")
        self.assertEqual(acc1["status"], "COOLDOWN")

        # sess_q1 marked as STOPPED
        sess1 = session_manager.get_session("sess_q1")
        self.assertEqual(sess1["status"], "STOPPED")

        # sess_q2 remains READY
        sess2 = session_manager.get_session("sess_q2")
        self.assertEqual(sess2["status"], "READY")

    def test_idle_timeout_evaluation_under_clock_drift_and_jumps(self):
        """Adversarial clock drift: backward clock jump and massive forward jump."""
        self._seed_account_and_session("acc_clk", "sess_clk", idle_timeout=600)
        t0 = 1000000.0
        self.watchdog.register_session("sess_clk", initial_timestamp=t0)

        # Case A: Backward clock jump (now = t0 - 3600): idle duration is negative
        terminated = self.watchdog.evaluate_idle_sessions(current_timestamp=t0 - 3600.0)
        self.assertEqual(len(terminated), 0, "Backward clock jump must not trigger auto-stop")

        # Case B: Traffic activity recorded at t0 + 500
        self.watchdog.record_traffic("sess_clk", timestamp=t0 + 500.0)
        self.assertEqual(self.watchdog.get_last_activity("sess_clk"), t0 + 500.0)

        # Check at t0 + 800 (idle for 300s < 600s) -> Not terminated
        terminated = self.watchdog.evaluate_idle_sessions(current_timestamp=t0 + 800.0)
        self.assertEqual(len(terminated), 0)

        # Case C: Forward clock jump (now = t0 + 2000 > 500 + 600) -> Terminated!
        terminated = self.watchdog.evaluate_idle_sessions(current_timestamp=t0 + 2000.0)
        self.assertIn("sess_clk", terminated)
        sess = session_manager.get_session("sess_clk")
        self.assertEqual(sess["status"], "STOPPED")

    def test_auto_termination_race_conditions_concurrent_watchdogs(self):
        """Concurrent evaluation of idle watchdog, quota circuit breaker, and manual stop on same session."""
        self._seed_account_and_session("acc_race", "sess_race", balance=1.0, hard_stop=2.0, idle_timeout=10)
        self.watchdog.register_session("sess_race", initial_timestamp=100.0)

        errors = []

        def run_idle():
            try:
                self.watchdog.evaluate_idle_sessions(current_timestamp=1000.0)
            except Exception as e:
                errors.append(e)

        def run_quota():
            try:
                self.quota_breaker.evaluate_and_enforce_hard_stops()
            except Exception as e:
                errors.append(e)

        def run_manual():
            try:
                session_manager.stop_session("sess_race")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=run_idle),
            threading.Thread(target=run_quota),
            threading.Thread(target=run_manual),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent teardown raised errors: {errors}")
        sess = session_manager.get_session("sess_race")
        self.assertEqual(sess["status"], "STOPPED")

    def test_proxy_token_auto_renewal_boundary_and_malformed_timestamps(self):
        """Proxy token renewal parses ISO timestamps, handles malformed strings, and renews <= 300s margin."""
        now = time.time()
        # Session 1: Expires in 150 seconds (<= 300s) -> Needs renewal
        exp_soon = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 150))
        # Session 2: Expires in 2000 seconds (> 300s) -> Does not need renewal
        exp_later = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now + 2000))
        # Session 3: Malformed timestamp -> Must not crash renewal loop
        exp_bad = "invalid-corrupted-timestamp"

        creds = {"access_token": "mock-token-renewal", "expires_at": int(now) + 3600}
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, hard_stop_ccu, status)
                VALUES ('acc_renew', 'renew@gmail.com', ?, 50.0, 2.0, 'ACTIVE')
                """,
                (encrypt_secret(json.dumps(creds)),),
            )
            for sid, exp in [("sess_r1", exp_soon), ("sess_r2", exp_later), ("sess_r3", exp_bad)]:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc,
                        token_expires_at, accelerator, lifecycle_policy, status
                    ) VALUES (?, ?, 'acc_renew', 'nbh', 'ep-target', 'http://proxy', 'enc_tok', ?, 'T4', 'interactive', 'READY')
                    """,
                    (sid, f"name-{sid}", exp),
                )

        # Mock client.list_assignments returning fresh token
        mock_client = MagicMock()
        mock_client.list_assignments.return_value = [
            {
                "endpoint": "ep-target",
                "runtimeProxyInfo": {
                    "token": "brand-new-proxy-token-xyz",
                    "url": "http://proxy-renewed",
                    "tokenExpiresInSeconds": 3600,
                },
            }
        ]

        engine = LifecycleEngine()
        renewed = asyncio.run(engine._evaluate_proxy_token_renewals(now, client=mock_client))

        # Exactly sess_r1 was renewed; sess_r2 was skipped, sess_r3 was safely ignored
        self.assertIn("sess_r1", renewed)
        self.assertNotIn("sess_r2", renewed)
        self.assertNotIn("sess_r3", renewed)

        # Verify sess_r1 was updated in DB with new token
        sess_r1 = session_manager.get_session_internal("sess_r1")
        self.assertEqual(sess_r1["proxy_token"], "brand-new-proxy-token-xyz")
        self.assertEqual(sess_r1["proxy_url"], "http://proxy-renewed")


if __name__ == "__main__":
    unittest.main()
