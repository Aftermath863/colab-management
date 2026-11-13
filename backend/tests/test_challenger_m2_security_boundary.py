"""Milestone 2 Adversarial Security, Vault & Sync Boundary Challenger Test Suite.

Adversarially challenges:
1. Secrets Vault & Redaction:
   - AES-256-GCM tamper detection across nonce, ciphertext, and tag.
   - Truncated and corrupted ciphertext payloads.
   - Cross-key decryption failure.
   - LogAndTelemetrySanitizer with multiline secrets, regex special characters, overlapping tokens, and nested telemetry.
   - GET endpoints zero-leakage verification (secrets, storage profiles, and templates).
2. Workspace Incremental Sync & Traversal Defenses:
   - Pull sync directory traversal injection (../ traversal escaping workspace).
   - Push sync symlink traversal (symlinks pointing outside workspace).
   - Session workspace subpath traversal.
   - Exclusion filtering on large files (>50MB, *.pt, *.safetensors, *.bin, *.onnx, *.ckpt, *.pth, *.gguf).
   - Exclusion of version control internals (.git, __pycache__, .venv).
   - Nested directory pre-creation hierarchy integrity.
3. Cloud Storage & Google Drive Handshake:
   - Missing or invalid XSRF tokens during 2-step credentials propagation.
   - Redirect URI extraction on unauthorized consent response.
   - 2-step handshake dryrun vs final commit transitions.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import (
    SecretCreate,
    StorageProfileCreate,
    TemplateCreate,
    SessionCreateRequest,
    SyncPushRequest,
    SyncPullRequest,
)
from backend.app.core.security import (
    DecryptionError,
    create_access_token,
    decrypt_secret,
    encrypt_secret,
    get_master_key,
    reset_master_key_cache,
)
from backend.app.main import app
from backend.app.services import session_manager, storage_wizard, template_service, workspace_sync
from backend.app.services.colab_client import ColabClient, ColabClientError
from backend.app.services.template_service import LogAndTelemetrySanitizer
from backend.app.services.workspace_sync import WorkspaceSyncEngine
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestMilestone2SecurityAndBoundaries(unittest.TestCase):
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

        self.workspace_dir = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(self.workspace_dir, exist_ok=True)
        settings.WORKSPACE_DIR = self.workspace_dir

        self.account_id = "acc_challenger_m2"
        self.email = "challenger_m2@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Challenger M2",
            sub="sub_challenger_m2",
            code="code_challenger_m2",
            access_token="ya29.mock_challenger_token",
            refresh_token="1//mock_challenger_refresh",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_challenger_token",
            "refresh_token": "1//mock_challenger_refresh",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Challenger M2', ?, 100.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        self.token = create_access_token({"sub": "admin", "role": "admin"})
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.client = TestClient(app)

    def tearDown(self):
        self.tmpdir.cleanup()
        reset_master_key_cache()

    # =========================================================================
    # Section 1: Secrets Vault & Redaction Defenses
    # =========================================================================

    def test_aes_256_gcm_tamper_detection_exhaustive(self):
        """Adversarially mutate every byte segment of AES-256-GCM ciphertext."""
        plaintext = "SUPER_CONFIDENTIAL_SECRET_TOKEN_!@#$%"
        enc_b64 = encrypt_secret(plaintext)
        payload = base64.b64decode(enc_b64)

        # Structure: 12 bytes nonce + N bytes ciphertext + 16 bytes tag
        self.assertGreater(len(payload), 28)
        nonce_len = 12
        tag_len = 16
        ciphertext_len = len(payload) - nonce_len - tag_len

        # 1. Tamper with Nonce (bytes 0..11)
        for i in range(nonce_len):
            tampered = bytearray(payload)
            tampered[i] ^= 0x01
            tampered_b64 = base64.b64encode(tampered).decode("ascii")
            with self.assertRaises(DecryptionError, msg=f"Nonce byte {i} tamper not caught"):
                decrypt_secret(tampered_b64)

        # 2. Tamper with Ciphertext (middle bytes)
        for i in range(nonce_len, nonce_len + ciphertext_len):
            tampered = bytearray(payload)
            tampered[i] ^= 0x01
            tampered_b64 = base64.b64encode(tampered).decode("ascii")
            with self.assertRaises(DecryptionError, msg=f"Ciphertext byte {i} tamper not caught"):
                decrypt_secret(tampered_b64)

        # 3. Tamper with Authentication Tag (last 16 bytes)
        for i in range(len(payload) - tag_len, len(payload)):
            tampered = bytearray(payload)
            tampered[i] ^= 0x01
            tampered_b64 = base64.b64encode(tampered).decode("ascii")
            with self.assertRaises(DecryptionError, msg=f"Tag byte {i} tamper not caught"):
                decrypt_secret(tampered_b64)

        # 4. Truncation attacks (less than minimum 28 bytes)
        for length in [0, 1, 10, 12, 27]:
            trunc = payload[:length]
            trunc_b64 = base64.b64encode(trunc).decode("ascii")
            with self.assertRaises(DecryptionError, msg=f"Truncated {length} bytes not caught"):
                decrypt_secret(trunc_b64)

        # 5. Non-base64 garbage and random byte injections
        with self.assertRaises(DecryptionError):
            decrypt_secret("not-base64-garbage!@#$%^&*()")

        # 6. Cross-key attack: decrypting with a different valid 32-byte key must fail tag check
        foreign_key = os.urandom(32)
        with self.assertRaises(DecryptionError):
            decrypt_secret(enc_b64, key=foreign_key)

    def test_log_sanitizer_regex_special_characters(self):
        """Challenge sanitizer against regex metacharacters in secret values."""
        special_secrets = {
            "REGEX_META_1": r"token$123^.*+?()[]{}|\\",
            "REGEX_META_2": r"foo\g<0>bar\1baz",
            "EQUALS_AND_SLOT": r"key===slot//\\&&&",
        }
        sanitizer = LogAndTelemetrySanitizer(special_secrets)

        log_input = (
            r"Starting worker with token$123^.*+?()[]{}|\\ in environment. "
            r"Also found foo\g<0>bar\1baz and key===slot//\\&&& in log stream."
        )

        sanitized = sanitizer.sanitize_text(log_input)
        for key, val in special_secrets.items():
            self.assertNotIn(val, sanitized, f"Secret value {val} leaked into sanitized text!")
            self.assertIn(f"[REDACTED:{key}]", sanitized)

    def test_log_sanitizer_multiline_secrets(self):
        """Challenge sanitizer against multiline secrets (e.g. RSA / OpenSSH private keys)."""
        multiline_key = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0Y3...\n"
            "abcdef1234567890/+\n"
            "-----END RSA PRIVATE KEY-----"
        )
        secrets = {"SSH_KEY": multiline_key}
        sanitizer = LogAndTelemetrySanitizer(secrets)

        log_text = (
            "Loaded identity file:\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0Y3...\n"
            "abcdef1234567890/+\n"
            "-----END RSA PRIVATE KEY-----\n"
            "Certificate valid."
        )

        clean = sanitizer.sanitize_text(log_text)
        self.assertNotIn("MIIEowIBAAKCAQEA0Y3...", clean)
        self.assertIn("[REDACTED:SSH_KEY]", clean)

    def test_log_sanitizer_overlapping_token_ordering(self):
        """Challenge sanitizer when one secret is a strict prefix/substring of another."""
        # When longer secret is evaluated first
        secrets_desc = {
            "FULL_KEY": "supersecret123456",
            "PREFIX_KEY": "supersecret",
        }
        sanitizer_desc = LogAndTelemetrySanitizer(secrets_desc)
        text_desc = "User token is supersecret123456."
        res_desc = sanitizer_desc.sanitize_text(text_desc)
        self.assertNotIn("supersecret123456", res_desc)
        self.assertIn("[REDACTED:FULL_KEY]", res_desc)

        # When shorter secret is inserted first, length descending sorting still prioritizes full key
        secrets_asc = {
            "PREFIX_KEY": "supersecret",
            "FULL_KEY": "supersecret123456",
        }
        sanitizer_asc = LogAndTelemetrySanitizer(secrets_asc)
        text_asc = "User token is supersecret123456."
        res_asc = sanitizer_asc.sanitize_text(text_asc)
        self.assertNotIn("supersecret123456", res_asc)
        self.assertIn("[REDACTED:FULL_KEY]", res_asc)

    def test_log_sanitizer_nested_telemetry_payload_boundary(self):
        """Test telemetry sanitization behavior on nested dictionaries and arrays."""
        secret_val = "secret_telemetry_token_999"
        sanitizer = LogAndTelemetrySanitizer({"SECRET_TEL": secret_val})

        payload = {
            "flat_str": f"Bearer {secret_val}",
            "nested_dict": {"inner_key": f"Bearer {secret_val}"},
            "array_list": [f"token={secret_val}"],
        }

        cleaned = sanitizer.sanitize_telemetry_payload(payload)
        # Top-level string is sanitized
        self.assertNotIn(secret_val, cleaned["flat_str"])
        self.assertIn("[REDACTED:SECRET_TEL]", cleaned["flat_str"])

        # Nested structures are recursively sanitized
        has_nested_leak = secret_val in str(cleaned["nested_dict"]) or secret_val in str(cleaned["array_list"])
        self.assertFalse(has_nested_leak, "Nested secrets should be redacted in telemetry payload")
        self.assertIn("[REDACTED:SECRET_TEL]", str(cleaned["nested_dict"]))
        self.assertIn("[REDACTED:SECRET_TEL]", str(cleaned["array_list"]))

    def test_get_endpoints_never_leak_plaintext_credentials(self):
        """Exhaustively verify GET endpoints for secrets, storage profiles, and templates."""
        # 1. Create a Secret with sensitive value
        sec_resp = self.client.post(
            "/api/secrets",
            json={
                "key_name": "LIVE_PROD_API_KEY",
                "value": "super_secret_plaintext_value_9999",
                "description": "Production API token",
            },
            headers=self.headers,
        )
        self.assertEqual(sec_resp.status_code, 201)
        sec_id = sec_resp.json()["id"]

        # GET /api/secrets
        list_sec = self.client.get("/api/secrets", headers=self.headers)
        self.assertEqual(list_sec.status_code, 200)
        list_sec_raw = list_sec.text
        self.assertNotIn("super_secret_plaintext_value_9999", list_sec_raw)
        self.assertNotIn("value_enc", list_sec_raw)
        for s in list_sec.json():
            self.assertNotIn("value", s)
            self.assertNotIn("value_enc", s)

        # GET /api/secrets/{id}
        single_sec = self.client.get(f"/api/secrets/{sec_id}", headers=self.headers)
        self.assertEqual(single_sec.status_code, 200)
        single_sec_raw = single_sec.text
        self.assertNotIn("super_secret_plaintext_value_9999", single_sec_raw)
        self.assertNotIn("value_enc", single_sec_raw)
        self.assertNotIn("value", single_sec.json())

        # 2. Create Storage Profiles (S3 and HF)
        s3_prof = self.client.post(
            "/api/storage/profiles",
            json={
                "name": "Classified S3 Bucket",
                "type": "s3",
                "mount_path": "/content/s3",
                "config": {
                    "access_key_id": "AKIA_LEAK_TEST_ID",
                    "secret_access_key": "AWS_SECRET_KEY_MUST_NOT_LEAK",
                    "region": "us-east-1",
                },
            },
            headers=self.headers,
        )
        self.assertEqual(s3_prof.status_code, 201)
        s3_id = s3_prof.json()["id"]

        # GET /api/storage/profiles
        list_prof = self.client.get("/api/storage/profiles", headers=self.headers)
        self.assertEqual(list_prof.status_code, 200)
        list_prof_raw = list_prof.text
        self.assertNotIn("AKIA_LEAK_TEST_ID", list_prof_raw)
        self.assertNotIn("AWS_SECRET_KEY_MUST_NOT_LEAK", list_prof_raw)
        self.assertNotIn("config_enc", list_prof_raw)
        for p in list_prof.json():
            self.assertNotIn("config", p)
            self.assertNotIn("config_enc", p)

        # GET /api/storage/profiles/{id}
        single_prof = self.client.get(f"/api/storage/profiles/{s3_id}", headers=self.headers)
        self.assertEqual(single_prof.status_code, 200)
        single_prof_raw = single_prof.text
        self.assertNotIn("AKIA_LEAK_TEST_ID", single_prof_raw)
        self.assertNotIn("AWS_SECRET_KEY_MUST_NOT_LEAK", single_prof_raw)
        self.assertNotIn("config_enc", single_prof_raw)
        self.assertNotIn("config", single_prof.json())

        # 3. Create Template referencing secret and profile
        tpl_resp = self.client.post(
            "/api/templates",
            json={
                "name": "Confidential Template",
                "packages": ["torch"],
                "secret_ids": [sec_id],
                "storage_profile_ids": [s3_id],
                "startup_script": "echo hello",
            },
            headers=self.headers,
        )
        self.assertEqual(tpl_resp.status_code, 201)
        tpl_id = tpl_resp.json()["id"]

        # GET /api/templates and GET /api/templates/{id}
        tpl_list = self.client.get("/api/templates", headers=self.headers)
        self.assertNotIn("super_secret_plaintext_value_9999", tpl_list.text)
        self.assertNotIn("AWS_SECRET_KEY_MUST_NOT_LEAK", tpl_list.text)

        tpl_single = self.client.get(f"/api/templates/{tpl_id}", headers=self.headers)
        self.assertNotIn("super_secret_plaintext_value_9999", tpl_single.text)
        self.assertNotIn("AWS_SECRET_KEY_MUST_NOT_LEAK", tpl_single.text)

    # =========================================================================
    # Section 2: Workspace Incremental Sync & Traversal Defenses
    # =========================================================================

    def test_sync_exclusion_filter_on_large_files_and_weights(self):
        """Stress-test exclusion of large files (>50MB, *.pt, *.safetensors, etc.)."""
        engine = WorkspaceSyncEngine()

        # Files above 50MB (50MB = 52,428,800 bytes)
        fifty_mb_plus_one = 50 * 1024 * 1024 + 1
        fifty_mb_exact = 50 * 1024 * 1024
        one_kb = 1024

        # Model extensions exceeding 50MB must be rejected
        for ext in ["pt", "safetensors", "bin", "onnx", "ckpt", "pth", "gguf"]:
            self.assertTrue(
                engine.is_large_model_file(f"weights.{ext}", fifty_mb_plus_one),
                f"File weights.{ext} ({fifty_mb_plus_one} bytes) was NOT marked large!",
            )

        # Model extensions at or below 50MB must be permitted
        for ext in ["pt", "safetensors", "bin", "onnx", "ckpt", "pth", "gguf"]:
            self.assertFalse(
                engine.is_large_model_file(f"weights.{ext}", fifty_mb_exact),
                f"File weights.{ext} ({fifty_mb_exact} bytes) was incorrectly rejected!",
            )
            self.assertFalse(
                engine.is_large_model_file(f"weights.{ext}", one_kb),
                f"File weights.{ext} (1KB) was incorrectly rejected!",
            )

        # Non-model files exceeding 50MB must NOT be marked as large model file
        self.assertFalse(
            engine.is_large_model_file("dataset.csv", fifty_mb_plus_one),
            "Non-model CSV was incorrectly marked as a large model file!",
        )

    def test_sync_exclusion_patterns_comprehensive(self):
        """Test exclusion patterns for version control, virtualenvs, bytecode, and caches."""
        engine = WorkspaceSyncEngine()

        excluded_paths = [
            (".git", True),
            (".git/config", False),
            ("subproject/.git/HEAD", False),
            ("__pycache__", True),
            ("__pycache__/cache.pyc", False),
            ("app/__pycache__/module.pyc", False),
            (".venv", True),
            (".venv/bin/python", False),
            ("venv/lib/site-packages", True),
            ("node_modules/express/index.js", False),
            (".pytest_cache/v/cache/nodeids", False),
            (".idea/workspace.xml", False),
            (".vscode/settings.json", False),
            (".ipynb_checkpoints/Untitled-checkpoint.ipynb", False),
            ("module.pyc", False),
            ("module.pyo", False),
            (".DS_Store", False),
            (".env", False),
            (".env.local", False),
        ]

        for path, is_dir in excluded_paths:
            self.assertTrue(
                engine.should_exclude_path(path, is_dir=is_dir),
                f"Path '{path}' (is_dir={is_dir}) should have been excluded but was not!",
            )

        # Valid source code files must NOT be excluded
        allowed_paths = [
            ("src/main.py", False),
            ("models/transformer.py", False),
            ("configs/train.yaml", False),
            ("data/sample.json", False),
            ("README.md", False),
            ("requirements.txt", False),
        ]

        for path, is_dir in allowed_paths:
            self.assertFalse(
                engine.should_exclude_path(path, is_dir=is_dir),
                f"Path '{path}' was incorrectly excluded!",
            )

    def test_nested_directory_pre_creation(self):
        """Verify ensure_remote_directory creates parent hierarchy in proper sequence."""
        mock_client = MagicMock(spec=ColabClient)
        mock_client.session = MagicMock()
        mock_client.timeout = 10

        proxy_url = "http://mock-proxy:1113"
        proxy_token = "mock-token"

        # Test deep nested directory
        storage_wizard.ensure_remote_directory(
            mock_client, proxy_url, proxy_token, "deep/nested/sub/folder"
        )

        # Should call PUT for: 'deep', 'deep/nested', 'deep/nested/sub', 'deep/nested/sub/folder'
        calls = mock_client.session.put.call_args_list
        self.assertEqual(len(calls), 4)

        expected_paths = [
            "http://mock-proxy:1113/api/contents/deep",
            "http://mock-proxy:1113/api/contents/deep/nested",
            "http://mock-proxy:1113/api/contents/deep/nested/sub",
            "http://mock-proxy:1113/api/contents/deep/nested/sub/folder",
        ]
        actual_urls = [c[0][0] for c in calls]
        self.assertEqual(actual_urls, expected_paths)

        # Edge cases: root or empty directory should NOT trigger any requests
        mock_client.session.put.reset_mock()
        storage_wizard.ensure_remote_directory(mock_client, proxy_url, proxy_token, "")
        storage_wizard.ensure_remote_directory(mock_client, proxy_url, proxy_token, ".")
        storage_wizard.ensure_remote_directory(mock_client, proxy_url, proxy_token, "/")
        self.assertEqual(mock_client.session.put.call_count, 0)

    def test_pull_workspace_directory_traversal_behavior(self):
        """Empirically test whether pull_workspace allows remote files to escape local workspace."""
        mock_client = MagicMock(spec=ColabClient)

        # Simulate remote Colab listing containing directory traversal payloads
        mock_client.list_contents.return_value = {
            "type": "directory",
            "content": [
                {
                    "name": "legit.py",
                    "path": "workspace/legit.py",
                    "type": "file",
                    "size": 100,
                },
                {
                    "name": "../../escaped.txt",
                    "path": "workspace/../../escaped.txt",
                    "type": "file",
                    "size": 50,
                },
            ],
        }

        mock_client.download_file.return_value = {
            "name": "test",
            "format": "text",
            "content": "traversal payload content",
        }

        engine = WorkspaceSyncEngine(mock_client)
        local_ws = os.path.join(self.tmpdir.name, "pull_target", "workspace")
        os.makedirs(local_ws, exist_ok=True)

        escaped_file = os.path.abspath(os.path.join(local_ws, "../../escaped.txt"))
        if os.path.exists(escaped_file):
            os.remove(escaped_file)

        # Run pull_workspace
        downloaded = engine.pull_workspace(
            local_dir=local_ws,
            proxy_url="http://mock-proxy",
            proxy_token="token",
            remote_prefix="workspace",
        )

        # Check if the file escaped outside local_ws:
        escaped_existed = os.path.exists(escaped_file)
        if escaped_existed:
            os.remove(escaped_file)

        # Defense verification: path traversal must be rejected
        self.assertFalse(
            escaped_existed,
            "pull_workspace must not write outside local_dir when remote path contains ../ traversal",
        )

    def test_push_workspace_symlink_traversal_behavior(self):
        """Empirically test whether push_workspace follows symlinks outside workspace."""
        mock_client = MagicMock(spec=ColabClient)
        mock_client.session = MagicMock()
        mock_client.timeout = 10

        # Create external file outside workspace
        external_file = os.path.join(self.tmpdir.name, "host_secret.txt")
        with open(external_file, "w") as f:
            f.write("SECRET_HOST_CONTENT")

        # Create symlink inside workspace pointing to external file
        symlink_path = os.path.join(self.workspace_dir, "symlink_secret.txt")
        try:
            os.symlink(external_file, symlink_path)
        except OSError:
            return  # Skip if symlinks not supported

        engine = WorkspaceSyncEngine(mock_client)
        uploaded = engine.push_workspace(
            local_dir=self.workspace_dir,
            proxy_url="http://mock-proxy",
            proxy_token="token",
            remote_prefix="workspace",
        )

        symlink_pushed = any("symlink_secret" in p for p in uploaded)
        self.assertFalse(
            symlink_pushed,
            "push_workspace must skip symlinks pointing outside workspace directory",
        )

    # =========================================================================
    # Section 3: Cloud Storage & Google Drive Handshake
    # =========================================================================

    def test_gdrive_propagation_missing_xsrf_token_fail_fast(self):
        """Verify fail-fast behavior when Step 1 probe GET omits XSRF token."""
        # Create a session
        sess = session_manager.create_session(
            SessionCreateRequest(
                name="gdrive-test-session",
                accelerator="T4",
                account_id=self.account_id,
            )
        )
        session_id = sess["id"]

        mock_client = MagicMock(spec=ColabClient)
        mock_client.base_url = "http://mock-colab"
        mock_client.timeout = 10
        mock_client.session = MagicMock()

        # Step 1 returns response without 'token'
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.text = ')]}\'\n{"error": "no token found"}'
        mock_client.session.get.return_value = mock_resp

        with self.assertRaises(ColabClientError) as cm:
            storage_wizard.propagate_gdrive_credentials(
                session_id, dryrun=False, client=mock_client
            )
        self.assertIn("Missing token", str(cm.exception))

    def test_gdrive_propagation_probe_post_failure_fail_fast(self):
        """Verify fail-fast behavior when Step 2 probe POST fails with HTTP 403/400."""
        sess = session_manager.create_session(
            SessionCreateRequest(
                name="gdrive-test-session-2",
                accelerator="T4",
                account_id=self.account_id,
            )
        )
        session_id = sess["id"]

        mock_client = MagicMock(spec=ColabClient)
        mock_client.base_url = "http://mock-colab"
        mock_client.timeout = 10
        mock_client.session = MagicMock()

        # Step 1 GET succeeds with valid token
        resp1 = MagicMock()
        resp1.ok = True
        resp1.status_code = 200
        resp1.text = ')]}\'\n{"token": "mock-xsrf-token-12345"}'
        mock_client.session.get.return_value = resp1

        # Step 2 POST fails with HTTP 403 Forbidden (e.g. invalid XSRF token)
        resp2 = MagicMock()
        resp2.ok = False
        resp2.status_code = 403
        resp2.text = "Forbidden"
        mock_client.session.post.return_value = resp2

        with self.assertRaises(ColabClientError) as cm:
            storage_wizard.propagate_gdrive_credentials(
                session_id, dryrun=False, client=mock_client
            )
        self.assertIn("HTTP 403", str(cm.exception))

    def test_gdrive_propagation_redirect_uri_extraction_on_consent_required(self):
        """Verify proper extraction of unauthorized_redirect_uri when consent is needed."""
        sess = session_manager.create_session(
            SessionCreateRequest(
                name="gdrive-test-session-3",
                accelerator="T4",
                account_id=self.account_id,
            )
        )
        session_id = sess["id"]

        mock_client = MagicMock(spec=ColabClient)
        mock_client.base_url = "http://mock-colab"
        mock_client.timeout = 10
        mock_client.session = MagicMock()

        # Step 1 GET succeeds
        resp1 = MagicMock()
        resp1.ok = True
        resp1.status_code = 200
        resp1.text = ')]}\'\n{"token": "valid-xsrf-token"}'
        mock_client.session.get.return_value = resp1

        # Step 2 POST returns unauthorized_redirect_uri (consent needed)
        consent_url = "https://accounts.google.com/o/oauth2/v2/auth?client_id=colab&scope=drive"
        resp2 = MagicMock()
        resp2.ok = True
        resp2.status_code = 200
        resp2.text = f')]}}\'\n{{"success": false, "unauthorized_redirect_uri": "{consent_url}"}}'
        mock_client.session.post.return_value = resp2

        res = storage_wizard.propagate_gdrive_credentials(
            session_id, dryrun=False, client=mock_client
        )

        self.assertEqual(res["status"], "AUTH_REQUIRED")
        self.assertFalse(res["success"])
        self.assertEqual(res["redirect_url"], consent_url)

    def test_gdrive_propagation_full_dryrun_and_commit_flow(self):
        """Verify transitions between Step 2 probe dryrun and Step 3 final commit."""
        sess = session_manager.create_session(
            SessionCreateRequest(
                name="gdrive-test-session-4",
                accelerator="T4",
                account_id=self.account_id,
            )
        )
        session_id = sess["id"]

        mock_client = MagicMock(spec=ColabClient)
        mock_client.base_url = "http://mock-colab"
        mock_client.timeout = 10
        mock_client.session = MagicMock()

        # Step 1 GET succeeds
        resp1 = MagicMock()
        resp1.ok = True
        resp1.status_code = 200
        resp1.text = ')]}\'\n{"token": "xsrf-step1-token"}'
        mock_client.session.get.return_value = resp1

        # Dryrun invocation: Step 2 POST succeeds
        resp2 = MagicMock()
        resp2.ok = True
        resp2.status_code = 200
        resp2.text = ')]}\'\n{"success": true}'
        mock_client.session.post.return_value = resp2

        dryrun_res = storage_wizard.propagate_gdrive_credentials(
            session_id, dryrun=True, client=mock_client
        )
        self.assertEqual(dryrun_res["status"], "AUTHORIZED")
        self.assertTrue(dryrun_res["success"])
        # Should only have called POST once for dryrun
        self.assertEqual(mock_client.session.post.call_count, 1)

        # Full commit invocation (dryrun=False): Step 2 then Step 3
        mock_client.session.post.reset_mock()
        resp3 = MagicMock()
        resp3.ok = True
        resp3.status_code = 200
        resp3.text = ')]}\'\n{"success": true, "mounted": true}'
        # Return resp2 for first POST (dryrun=true probe), resp3 for second POST (dryrun=false commit)
        mock_client.session.post.side_effect = [resp2, resp3]

        commit_res = storage_wizard.propagate_gdrive_credentials(
            session_id, dryrun=False, client=mock_client
        )
        self.assertEqual(commit_res["status"], "PROPAGATED")
        self.assertTrue(commit_res["success"])
        self.assertEqual(mock_client.session.post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
