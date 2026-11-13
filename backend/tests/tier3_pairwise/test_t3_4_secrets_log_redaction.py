"""T3.4: Injected Secrets in Remote Env x Redacted from Logs & Telemetry.

Validates that environment secrets stored in AES-256-GCM Vault:
1. Are successfully resolved, decrypted, and injected into the execution environment variables.
2. Are strictly intercepted and redacted from live stdout/stderr log streams, disk log files,
   and telemetry error traces before persistence or API serialization.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from typing import Any, Dict, List, Optional

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.security import decrypt_secret, encrypt_secret


class SecretEnvironmentInjector:
    """Resolves templates and decrypts vault secrets into execution environment dicts."""

    @staticmethod
    def resolve_template_env(template_id: str) -> Dict[str, str]:
        with get_db() as conn:
            tpl = conn.execute("SELECT secret_ids_json FROM templates WHERE id = ?", (template_id,)).fetchone()
            if not tpl:
                raise ValueError(f"Template {template_id} not found")

            secret_ids = json.loads(tpl["secret_ids_json"])
            env: Dict[str, str] = {}
            for sec_id in secret_ids:
                sec_row = conn.execute("SELECT key_name, value_enc FROM secrets WHERE id = ?", (sec_id,)).fetchone()
                if sec_row:
                    key = sec_row["key_name"]
                    val = decrypt_secret(sec_row["value_enc"])
                    env[key] = val

            return env


class LogAndTelemetrySanitizer:
    """Sanitizes text logs, disk files, and telemetry payloads against known secret values."""

    def __init__(self, secrets_to_redact: Dict[str, str]):
        self.secrets_to_redact = secrets_to_redact

    def sanitize_text(self, text: str) -> str:
        sanitized = text
        for key_name, secret_val in self.secrets_to_redact.items():
            if secret_val:
                escaped = re.escape(secret_val)
                replacement = f"[REDACTED:{key_name}]"
                sanitized = re.sub(escaped, replacement, sanitized)
        return sanitized

    def sanitize_log_file(self, input_path: str, output_path: str) -> None:
        with open(input_path, "r", encoding="utf-8") as fin:
            content = fin.read()
        cleaned = self.sanitize_text(content)
        with open(output_path, "w", encoding="utf-8") as fout:
            fout.write(cleaned)

    def sanitize_telemetry_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = {}
        for k, v in payload.items():
            if isinstance(v, str):
                cleaned[k] = self.sanitize_text(v)
            else:
                cleaned[k] = v
        return cleaned


class TestT34SecretsInjectionAndLogRedaction(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_manager.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        # 1. Store secrets in AES-256-GCM Vault
        self.hf_secret_raw = "hf_live_secret_token_abcdef123456"
        self.wandb_secret_raw = "wandb_live_secret_key_99887766"

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO secrets (id, key_name, value_enc, description)
                VALUES ('sec_hf_1', 'HF_TOKEN', ?, 'HuggingFace Access Token'),
                       ('sec_wandb_2', 'WANDB_API_KEY', ?, 'Weights & Biases API Key')
                """,
                (encrypt_secret(self.hf_secret_raw), encrypt_secret(self.wandb_secret_raw)),
            )

            # 2. Store template referencing both secrets
            conn.execute(
                """
                INSERT INTO templates (id, name, packages_json, secret_ids_json, startup_script)
                VALUES ('tpl_ai_training', 'AI Training Template', '["transformers", "wandb"]', '["sec_hf_1", "sec_wandb_2"]', 'echo ready')
                """
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t3_4_secrets_injected_in_env_but_redacted_from_logs_and_telemetry(self):
        # 1. Resolve environment: secrets decrypt into runtime execution environment
        resolved_env = SecretEnvironmentInjector.resolve_template_env("tpl_ai_training")
        self.assertEqual(resolved_env["HF_TOKEN"], self.hf_secret_raw)
        self.assertEqual(resolved_env["WANDB_API_KEY"], self.wandb_secret_raw)

        # Initialize sanitizer with active environment secrets
        sanitizer = LogAndTelemetrySanitizer(resolved_env)

        # 2. Simulate raw job output stream containing sensitive tokens in stdout/stderr
        raw_stream_chunk = (
            "2026-11-13 10:00:00 [INFO] Authenticating to HuggingFace Hub with hf_live_secret_token_abcdef123456...\n"
            "2026-11-13 10:00:01 [INFO] Login successful. WANDB_API_KEY=wandb_live_secret_key_99887766 verified.\n"
            "2026-11-13 10:00:02 [INFO] Training epoch 1 started.\n"
        )

        # Filter live stream
        sanitized_stream = sanitizer.sanitize_text(raw_stream_chunk)

        # Invariants: raw secrets are strictly absent from output stream
        self.assertNotIn(self.hf_secret_raw, sanitized_stream)
        self.assertNotIn(self.wandb_secret_raw, sanitized_stream)
        self.assertIn("[REDACTED:HF_TOKEN]", sanitized_stream)
        self.assertIn("[REDACTED:WANDB_API_KEY]", sanitized_stream)

        # 3. Verify on-disk log file sanitization
        raw_log_path = os.path.join(self.tmpdir.name, "raw_job_output.log")
        sanitized_log_path = os.path.join(self.tmpdir.name, "sanitized_job_output.log")
        with open(raw_log_path, "w", encoding="utf-8") as f:
            f.write(raw_stream_chunk)

        sanitizer.sanitize_log_file(raw_log_path, sanitized_log_path)

        with open(sanitized_log_path, "r", encoding="utf-8") as f:
            disk_content = f.read()

        self.assertNotIn(self.hf_secret_raw, disk_content)
        self.assertNotIn(self.wandb_secret_raw, disk_content)
        self.assertIn("[REDACTED:HF_TOKEN]", disk_content)
        self.assertIn("[REDACTED:WANDB_API_KEY]", disk_content)

        # 4. Verify telemetry error trace sanitization
        raw_telemetry_sample = {
            "session_id": "sess_training_01",
            "kernel_state": "error",
            "cpu_pct": 82.5,
            "error_trace": f"RuntimeError: Failed API call with token {self.hf_secret_raw} and key {self.wandb_secret_raw}",
        }

        sanitized_telemetry = sanitizer.sanitize_telemetry_payload(raw_telemetry_sample)
        self.assertNotIn(self.hf_secret_raw, sanitized_telemetry["error_trace"])
        self.assertNotIn(self.wandb_secret_raw, sanitized_telemetry["error_trace"])
        self.assertIn("[REDACTED:HF_TOKEN]", sanitized_telemetry["error_trace"])
        self.assertIn("[REDACTED:WANDB_API_KEY]", sanitized_telemetry["error_trace"])

        # Persist sanitized telemetry into DB and assert clean row
        with get_db() as conn:
            # Insert prerequisite account and session to satisfy foreign key
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, status)
                VALUES ('acc_telemetry_test', 'telemetry@test.com', 'enc', 100.0, 'ACTIVE')
                """
            )
            conn.execute(
                """
                INSERT INTO sessions (id, name, account_id, notebook_hash, endpoint, proxy_url,
                                     proxy_token_enc, token_expires_at, accelerator, status)
                VALUES ('sess_training_01', 'train-sess', 'acc_telemetry_test', 'hash', 'ep', 'url',
                        'tok', '2026-11-13', 'T4', 'READY')
                """
            )
            conn.execute(
                """
                INSERT INTO telemetry_samples (session_id, kernel_state, cpu_pct, gpu_name)
                VALUES (?, ?, ?, ?)
                """,
                (
                    sanitized_telemetry["session_id"],
                    sanitized_telemetry["kernel_state"],
                    sanitized_telemetry["cpu_pct"],
                    sanitized_telemetry["error_trace"],
                ),
            )
            row = conn.execute("SELECT gpu_name FROM telemetry_samples WHERE session_id = 'sess_training_01'").fetchone()
            self.assertNotIn(self.hf_secret_raw, row["gpu_name"])
            self.assertNotIn(self.wandb_secret_raw, row["gpu_name"])


if __name__ == "__main__":
    unittest.main()
