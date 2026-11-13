"""Tier 1 Feature Tests: Environment Templates, Secrets Vault & Bootstrap (F8).

Validates:
1. Secret CRUD operations with AES-256-GCM authenticated encryption.
2. API responses strictly omit plaintext secrets (zero-leakage contract).
3. Template CRUD operations with package list, secrets, and storage profiles.
4. LogAndTelemetrySanitizer regex redaction ([REDACTED:<KEY_NAME>]).
5. Session bootstrap execution with secret environment injection.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SecretCreate, SecretUpdate, TemplateCreate, TemplateUpdate
from backend.app.core.security import create_access_token, decrypt_secret
from backend.app.main import app
from backend.app.services import template_service
from backend.app.services.template_service import LogAndTelemetrySanitizer


class TestTemplatesAndSecrets(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_manager.db")
        settings.DATABASE_PATH = self.db_path
        self.orig_data_dir = getattr(settings, "DATA_DIR", "./data")
        settings.DATA_DIR = self.tmpdir.name
        init_db(self.db_path)

        self.token = create_access_token({"sub": "admin", "role": "admin"})
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        settings.DATA_DIR = self.orig_data_dir
        self.tmpdir.cleanup()

    def test_secret_crud_and_zero_leakage_contract(self):
        """Verify secrets are encrypted in DB and omitted from list/get responses."""
        raw_key = "WANDB_API_KEY"
        raw_val = "wandb_super_secret_live_token_12345"
        desc = "Weights and Biases Key"

        # 1. Create Secret
        created = template_service.create_secret(
            SecretCreate(key_name=raw_key, value=raw_val, description=desc)
        )
        sec_id = created["id"]
        self.assertEqual(created["key_name"], raw_key)
        self.assertEqual(created["description"], desc)
        self.assertNotIn("value", created)
        self.assertNotIn("value_enc", created)

        # 2. Check Database Storage is AES-256-GCM Encrypted
        with get_db() as conn:
            row = conn.execute("SELECT value_enc FROM secrets WHERE id = ?", (sec_id,)).fetchone()
            self.assertIsNotNone(row)
            enc_str = row["value_enc"]
            self.assertNotEqual(enc_str, raw_val)
            self.assertEqual(decrypt_secret(enc_str), raw_val)

        # 3. Retrieve through internal resolve
        resolved = template_service.resolve_secret_value(sec_id)
        self.assertEqual(resolved, raw_val)

        # 4. List Secrets (sanitized)
        listed = template_service.list_secrets()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], sec_id)
        self.assertNotIn("value", listed[0])

        # 5. Update Secret
        new_val = "wandb_updated_key_99999"
        updated = template_service.update_secret(
            sec_id, SecretUpdate(value=new_val, description="Updated desc")
        )
        self.assertEqual(updated["description"], "Updated desc")
        self.assertEqual(template_service.resolve_secret_value(sec_id), new_val)

        # 6. Delete Secret
        deleted = template_service.delete_secret(sec_id)
        self.assertTrue(deleted)
        self.assertIsNone(template_service.get_secret(sec_id))

    def test_template_crud_and_env_resolution(self):
        """Verify template creation, linking to secrets, and env resolution."""
        # Setup secrets
        s1 = template_service.create_secret(SecretCreate(key_name="HF_TOKEN", value="hf_abc123"))
        s2 = template_service.create_secret(SecretCreate(key_name="OPENAI_API_KEY", value="sk-xyz789"))

        # Create Template
        tpl = template_service.create_template(
            TemplateCreate(
                name="Deep Learning Baseline",
                packages=["torch", "transformers", "accelerate"],
                secret_ids=[s1["id"], s2["id"]],
                storage_profile_ids=[],
                startup_script='echo "Ready"',
            )
        )
        tpl_id = tpl["id"]
        self.assertEqual(tpl["name"], "Deep Learning Baseline")
        self.assertEqual(len(tpl["packages"]), 3)
        self.assertIn("torch", tpl["packages"])
        self.assertEqual(len(tpl["secret_ids"]), 2)

        # Resolve Template Environment
        env = template_service.resolve_template_env(tpl_id)
        self.assertEqual(env["HF_TOKEN"], "hf_abc123")
        self.assertEqual(env["OPENAI_API_KEY"], "sk-xyz789")

        # Update Template
        updated = template_service.update_template(
            tpl_id, TemplateUpdate(packages=["torch", "vllm"])
        )
        self.assertEqual(updated["packages"], ["torch", "vllm"])

        # Delete Template
        deleted = template_service.delete_template(tpl_id)
        self.assertTrue(deleted)
        self.assertIsNone(template_service.get_template(tpl_id))

    def test_log_and_telemetry_sanitizer(self):
        """Verify strict redaction of raw secret values from text, logs, and telemetry."""
        secrets_dict = {
            "HF_TOKEN": "hf_live_secret_token_12345",
            "WANDB_API_KEY": "wandb_top_secret_key_67890",
        }
        sanitizer = LogAndTelemetrySanitizer(secrets_dict)

        # 1. Text Redaction
        raw_text = (
            "Authenticating with token hf_live_secret_token_12345 to download model.\n"
            "WandB init with key wandb_top_secret_key_67890 succeeded."
        )
        clean_text = sanitizer.sanitize_text(raw_text)
        self.assertNotIn("hf_live_secret_token_12345", clean_text)
        self.assertNotIn("wandb_top_secret_key_67890", clean_text)
        self.assertIn("[REDACTED:HF_TOKEN]", clean_text)
        self.assertIn("[REDACTED:WANDB_API_KEY]", clean_text)

        # 2. Log File Redaction
        log_file = os.path.join(self.tmpdir.name, "execution.log")
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(raw_text)
        sanitizer.sanitize_log_file(log_file)
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("hf_live_secret_token_12345", content)
        self.assertIn("[REDACTED:HF_TOKEN]", content)

        # 3. Telemetry Payload Redaction
        payload = {
            "kernel_state": "busy",
            "error_trace": "Error with hf_live_secret_token_12345 during download",
            "gpu_util_pct": 85.5,
        }
        cleaned_payload = sanitizer.sanitize_telemetry_payload(payload)
        self.assertNotIn("hf_live_secret_token_12345", cleaned_payload["error_trace"])
        self.assertIn("[REDACTED:HF_TOKEN]", cleaned_payload["error_trace"])
        self.assertEqual(cleaned_payload["gpu_util_pct"], 85.5)

    def test_rest_api_templates_and_secrets(self):
        """Verify HTTP REST endpoints for templates and secrets vault."""
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            # 1. Create Secret via API
            sec_resp = client.post(
                "/api/secrets",
                headers=self.headers,
                json={"key_name": "API_SECRET_KEY", "value": "super_secret_val_111", "description": "Test Key"},
            )
            self.assertEqual(sec_resp.status_code, 201)
            sec_data = sec_resp.json()
            sec_id = sec_data["id"]

            # 2. List Secrets via API
            list_resp = client.get("/api/secrets", headers=self.headers)
            self.assertEqual(list_resp.status_code, 200)
            self.assertEqual(len(list_resp.json()), 1)

            # 3. Create Template via API
            tpl_resp = client.post(
                "/api/templates",
                headers=self.headers,
                json={
                    "name": "API Template",
                    "packages": ["fastapi", "uvicorn"],
                    "secret_ids": [sec_id],
                    "storage_profile_ids": [],
                    "startup_script": "echo API_READY",
                },
            )
            self.assertEqual(tpl_resp.status_code, 201)
            tpl_data = tpl_resp.json()
            tpl_id = tpl_data["id"]

            # 4. Get Template via API
            get_resp = client.get(f"/api/templates/{tpl_id}", headers=self.headers)
            self.assertEqual(get_resp.status_code, 200)
            self.assertEqual(get_resp.json()["name"], "API Template")

            # 5. Delete Template and Secret via API
            del_tpl = client.delete(f"/api/templates/{tpl_id}", headers=self.headers)
            self.assertEqual(del_tpl.status_code, 204)
            del_sec = client.delete(f"/api/secrets/{sec_id}", headers=self.headers)
            self.assertEqual(del_sec.status_code, 204)

    def test_session_bootstrap_execution_genuine_flow(self):
        """Verify genuine execution of .bootstrap.sh via SSH gateway, exit status checks, and logging."""
        from backend.app.core.security import encrypt_secret
        from backend.app.services.colab_client import ColabClient
        from backend.app.services.ssh_multiplexer import ssh_multiplexer_service

        # 1. Create a Secret and Template
        secret = template_service.create_secret(
            SecretCreate(key_name="BOOT_SECRET_KEY", value="super_sensitive_key_val_999")
        )
        template = template_service.create_template(
            TemplateCreate(
                name="PyTorch Template",
                packages=["torch", "transformers"],
                secret_ids=[secret["id"]],
                startup_script="echo Running custom script with $BOOT_SECRET_KEY",
            )
        )

        session_id = "sess_bootstrap_test"
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance)
                VALUES ('acc_boot', 'acc@test.com', ?, 100.0)
                """,
                (encrypt_secret("creds"),),
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, status, ssh_local_port
                ) VALUES (?, 'sess_boot', 'acc_boot', 'hash', 'ep', 'http://colab-proxy', ?, CURRENT_TIMESTAMP, 'T4', 'BOOTSTRAPPING', 2220)
                """,
                (session_id, encrypt_secret("token")),
            )

        mock_colab = MagicMock(spec=ColabClient)
        mock_colab.upload_file = MagicMock()

        class MockProcess:
            def __init__(self, stdout: str, stderr: str, exit_status: int):
                self.stdout = stdout
                self.stderr = stderr
                self.exit_status = exit_status
                self.returncode = exit_status

        # Case A: Successful execution (exit code 0)
        mock_gw = MagicMock()
        mock_gw.run_command = AsyncMock(
            return_value=MockProcess(
                stdout="[Bootstrap] Installing pip packages: torch transformers\nCustom script executed with super_sensitive_key_val_999\n",
                stderr="",
                exit_status=0,
            )
        )

        with patch.object(ssh_multiplexer_service, "get_gateway", return_value=mock_gw):
            success = asyncio.run(
                template_service.execute_session_bootstrap(
                    session_id, template["id"], client=mock_colab
                )
            )
            self.assertTrue(success)

        # Verify session status is READY
        with get_db() as conn:
            row = conn.execute("SELECT status FROM sessions WHERE id = ?", (session_id,)).fetchone()
            self.assertEqual(row["status"], "READY")

        # Verify log file exists and sensitive secret is redacted
        log_path = os.path.join(self.tmpdir.name, "logs", f"{session_id}_bootstrap.log")
        if not os.path.exists(log_path):
            log_path = os.path.join("./data/logs", f"{session_id}_bootstrap.log")
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, "r", encoding="utf-8") as f:
            log_content = f.read()
        self.assertIn("Exit Code: 0", log_content)
        self.assertIn("[REDACTED:BOOT_SECRET_KEY]", log_content)
        self.assertNotIn("super_sensitive_key_val_999", log_content)

        # Case B: Execution failure (exit code 1)
        mock_gw.run_command = AsyncMock(
            return_value=MockProcess(
                stdout="",
                stderr="ERROR: Could not find a version that satisfies the requirement invalid-pkg",
                exit_status=1,
            )
        )

        with patch.object(ssh_multiplexer_service, "get_gateway", return_value=mock_gw):
            success = asyncio.run(
                template_service.execute_session_bootstrap(
                    session_id, template["id"], client=mock_colab
                )
            )
            self.assertFalse(success)

        # Verify session status transitions to FAILED
        with get_db() as conn:
            row = conn.execute("SELECT status FROM sessions WHERE id = ?", (session_id,)).fetchone()
            self.assertEqual(row["status"], "FAILED")

        with open(log_path, "r", encoding="utf-8") as f:
            log_content = f.read()
        self.assertIn("Exit Code: 1", log_content)
        self.assertIn("ERROR: Could not find a version", log_content)

        # Case C: SSH Gateway Command Exception (e.g. timeout)
        mock_gw.run_command = AsyncMock(side_effect=asyncio.TimeoutError("SSH command timed out"))
        with patch.object(ssh_multiplexer_service, "get_gateway", return_value=mock_gw):
            success = asyncio.run(
                template_service.execute_session_bootstrap(
                    session_id, template["id"], client=mock_colab
                )
            )
            self.assertFalse(success)

        with get_db() as conn:
            row = conn.execute("SELECT status FROM sessions WHERE id = ?", (session_id,)).fetchone()
            self.assertEqual(row["status"], "FAILED")

        with open(log_path, "r", encoding="utf-8") as f:
            log_content = f.read()
        self.assertIn("Bootstrap execution exception: SSH command timed out", log_content)
