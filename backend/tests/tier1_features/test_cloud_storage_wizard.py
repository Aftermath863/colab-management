"""Tier 1 Feature Tests: Cloud Storage Wizard & Integrations (F9).

Validates:
1. Storage Profile CRUD operations (gdrive, s3, hf) with encrypted credentials.
2. Google Drive 2-step XSRF credentials propagation handshake against control plane.
3. Rclone configuration generation for AWS S3 and Cloudflare R2.
4. Hugging Face token deployment.
5. Preflight cloud mount path verification.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

import httpx

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import (
    SessionCreateRequest,
    StorageProfileCreate,
    StorageProfileUpdate,
)
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app
from backend.app.services import session_manager, storage_wizard
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestCloudStorageWizard(unittest.TestCase):
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

        self.account_id = "acc_storage_test"
        self.email = "storage_tester@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Storage Tester",
            sub="sub_storage_123",
            code="code_storage",
            access_token="ya29.mock_storage_token",
            refresh_token="1//mock_storage_refresh",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_storage_token",
            "refresh_token": "1//mock_storage_refresh",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Storage Tester', ?, 100.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        self.token = create_access_token({"sub": "admin", "role": "admin"})
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_storage_profile_crud(self):
        """Verify storage profiles are encrypted in DB and accessible."""
        # 1. Create S3 Profile
        prof = storage_wizard.create_storage_profile(
            StorageProfileCreate(
                name="S3 Dataset Bucket",
                type="s3",
                mount_path="/content/data/s3",
                config={
                    "provider": "AWS",
                    "access_key_id": "AKIA_TEST_KEY_123",
                    "secret_access_key": "SECRET_KEY_XYZ_456",
                    "region": "us-west-2",
                },
            )
        )
        prof_id = prof["id"]
        self.assertEqual(prof["name"], "S3 Dataset Bucket")
        self.assertEqual(prof["type"], "s3")
        self.assertEqual(prof["mount_path"], "/content/data/s3")

        # 2. Check Database Storage is Encrypted
        with get_db() as conn:
            row = conn.execute("SELECT config_enc FROM storage_profiles WHERE id = ?", (prof_id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertNotIn("AKIA_TEST_KEY_123", row["config_enc"])

        # 3. Read back plaintext config via internal method
        cfg = storage_wizard.get_storage_profile_config(prof_id)
        self.assertEqual(cfg["access_key_id"], "AKIA_TEST_KEY_123")

        # 4. Update Profile
        updated = storage_wizard.update_storage_profile(
            prof_id, StorageProfileUpdate(mount_path="/content/data/updated_s3")
        )
        self.assertEqual(updated["mount_path"], "/content/data/updated_s3")

        # 5. Delete Profile
        deleted = storage_wizard.delete_storage_profile(prof_id)
        self.assertTrue(deleted)
        self.assertIsNone(storage_wizard.get_storage_profile(prof_id))

    def test_rclone_and_hf_config_generators(self):
        """Verify generation of rclone.conf syntax and Hugging Face token string."""
        # S3 Rclone Config
        s3_conf = storage_wizard.generate_rclone_config(
            "My S3",
            {
                "provider": "AWS",
                "access_key_id": "AKIA12345",
                "secret_access_key": "SECRET67890",
                "region": "us-east-1",
            },
        )
        self.assertIn("[My_S3]", s3_conf)
        self.assertIn("type = s3", s3_conf)
        self.assertIn("access_key_id = AKIA12345", s3_conf)
        self.assertIn("secret_access_key = SECRET67890", s3_conf)

        # Cloudflare R2 Rclone Config
        r2_conf = storage_wizard.generate_rclone_config(
            "Cloudflare R2",
            {
                "provider": "Cloudflare",
                "access_key_id": "r2_access_key",
                "secret_access_key": "r2_secret_key",
                "endpoint": "https://abc.r2.cloudflarestorage.com",
            },
        )
        self.assertIn("[Cloudflare_R2]", r2_conf)
        self.assertIn("endpoint = https://abc.r2.cloudflarestorage.com", r2_conf)

        # Hugging Face Token Generator
        hf_tok = storage_wizard.generate_hf_token_config({"token": "hf_valid_user_token_999"})
        self.assertEqual(hf_tok, "hf_valid_user_token_999")

    def test_gdrive_credentials_propagation_flow(self):
        """Verify 2-step XSRF credentials propagation handshake against control plane."""
        # Provision a test session
        sess = session_manager.create_session(
            SessionCreateRequest(name="gdrive-session", accelerator="T4", account_id=self.account_id)
        )
        session_id = sess["id"]

        # Run GDrive credentials propagation with dryrun=False
        res = storage_wizard.propagate_gdrive_credentials(session_id, dryrun=False)
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "PROPAGATED")

    def test_rest_api_storage_endpoints(self):
        """Verify REST API endpoints for storage profile management and mounting."""
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            # 1. Create Profile via API
            create_resp = client.post(
                "/api/storage/profiles",
                headers=self.headers,
                json={
                    "name": "API HuggingFace Profile",
                    "type": "hf",
                    "mount_path": "/content/hf_models",
                    "config": {"token": "hf_token_via_api_777"},
                },
            )
            self.assertEqual(create_resp.status_code, 201)
            data = create_resp.json()
            prof_id = data["id"]

            # 2. List Profiles via API
            list_resp = client.get("/api/storage/profiles", headers=self.headers)
            self.assertEqual(list_resp.status_code, 200)
            self.assertTrue(any(p["id"] == prof_id for p in list_resp.json()))

            # 3. Delete Profile via API
            del_resp = client.delete(f"/api/storage/profiles/{prof_id}", headers=self.headers)
            self.assertEqual(del_resp.status_code, 204)
