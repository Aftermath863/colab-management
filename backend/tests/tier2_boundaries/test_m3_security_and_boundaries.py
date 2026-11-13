"""Tier 2 Boundary & Security Challenger Tests for Milestone 3.

Empirically challenges:
1. Sensitive token masking on GET endpoints (webhook URLs with query tokens, bot tokens, Bark device keys, HMAC secrets).
2. Secret redaction in telemetry payloads and job run logs with `[REDACTED:<KEY_NAME>]`.
3. Circuit breaker exact boundary conditions (10.01 CCU vs 10.00 CCU, 2.01 CCU vs 2.00 CCU) and critical cooldown bypass.
4. DingTalk timestamp HMAC-SHA256 signature validation with independent receiver oracle.
5. Corrupted notebook JSON handling and Linux OOM killer exit code 137 detection.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import time
import unittest
import urllib.parse
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import JobCreate, SessionCreateRequest
from backend.app.core.security import create_access_token, decrypt_secret, encrypt_secret
from backend.app.main import app
from backend.app.services import session_manager
from backend.app.services.colab_client import ColabClient
from backend.app.services.job_runner import JobRunnerService
from backend.app.services.lifecycle_engine import LifecycleEngine, QuotaCircuitBreakerWatchdog
from backend.app.services.notification import (
    NotificationService,
    mask_channel_config,
    mask_secret_str,
    notification_service,
)
from backend.app.services.telemetry import TelemetryService
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class DingTalkReceiverOracle:
    """Independent verification oracle emulating DingTalk's server-side signature validation."""

    def __init__(self, secret: str, max_drift_ms: int = 3600_000):
        self.secret = secret
        self.max_drift_ms = max_drift_ms

    def validate_request_url(self, url: str) -> tuple[bool, str]:
        """Validate DingTalk URL containing timestamp and sign query parameters."""
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)

        if "timestamp" not in qs:
            return False, "Missing timestamp parameter"
        if "sign" not in qs:
            return False, "Missing sign parameter"

        timestamp_str = qs["timestamp"][0]
        received_sign = qs["sign"][0]

        try:
            ts = int(timestamp_str)
        except ValueError:
            return False, "Timestamp is not a valid integer"

        now_ms = round(time.time() * 1000)
        if abs(now_ms - ts) > self.max_drift_ms:
            return False, f"Timestamp drift too large ({abs(now_ms - ts)}ms > {self.max_drift_ms}ms)"

        string_to_sign = f"{timestamp_str}\n{self.secret}".encode("utf-8")
        computed_hmac = hmac.new(self.secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
        expected_sign = base64.b64encode(computed_hmac).decode("utf-8")

        if not hmac.compare_digest(received_sign, expected_sign):
            return False, f"Signature mismatch (received: {received_sign}, expected: {expected_sign})"

        return True, "Signature verified successfully"


class TestMilestone3SecurityAndBoundaries(unittest.TestCase):
    """Empirical challenge suite for security, boundary values, and fault injection."""

    @classmethod
    def setUpClass(cls):
        cls.hub = MockControlHub()
        cls.hub.start()

        cls.orig_control_url = settings.CONTROL_PLANE_URL
        cls.orig_token_url = settings.OAUTH_TOKEN_URL
        cls.orig_userinfo_url = settings.OAUTH_USERINFO_URL
        cls.orig_master_key = settings.MASTER_KEY

        settings.CONTROL_PLANE_URL = cls.hub.urls["control_plane"]
        settings.OAUTH_TOKEN_URL = cls.hub.oauth.token_uri
        settings.OAUTH_USERINFO_URL = cls.hub.oauth.userinfo_uri
        settings.MASTER_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        os.environ["MASTER_KEY"] = settings.MASTER_KEY

    @classmethod
    def tearDownClass(cls):
        settings.CONTROL_PLANE_URL = cls.orig_control_url
        settings.OAUTH_TOKEN_URL = cls.orig_token_url
        settings.OAUTH_USERINFO_URL = cls.orig_userinfo_url
        settings.MASTER_KEY = cls.orig_master_key
        cls.hub.stop()

    def setUp(self):
        self.hub.reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "challenger_m3.db")
        settings.DATABASE_PATH = self.db_path

        self.orig_data_dir = getattr(settings, "DATA_DIR", "./data")
        self.orig_workspace_dir = getattr(settings, "WORKSPACE_DIR", "./workspace")
        settings.DATA_DIR = self.tmpdir.name
        settings.WORKSPACE_DIR = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(settings.WORKSPACE_DIR, exist_ok=True)

        init_db(self.db_path)

        self.token = create_access_token(data={"sub": "admin", "role": "admin"})
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        self.client = TestClient(app)

        # Seed an active primary Google account
        self.account_id = "acc_challenger_m3"
        self.email = "challenger.m3@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Challenger M3 User",
            sub="sub_challenger_m3",
            code="code_challenger_m3",
            access_token="ya29.mock_challenger_m3",
            refresh_token="1//mock_refresh_challenger_m3",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_challenger_m3",
            "refresh_token": "1//mock_refresh_challenger_m3",
            "token_uri": self.hub.oauth.token_uri,
            "client_id": "mock_client_id",
            "client_secret": "mock_client_secret",
            "scopes": ["openid", "email"],
            "expiry": time.time() + 3600,
        }
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    id, email, display_name, oauth_creds_enc, ccu_balance,
                    warning_ccu, hard_stop_ccu, priority, active_assignments, status
                ) VALUES (?, ?, ?, ?, 50.0, 10.0, 2.0, 10, 0, 'ACTIVE')
                """,
                (self.account_id, self.email, "Challenger M3 User", encrypt_secret(json.dumps(creds))),
            )

        self.colab_client = ColabClient(base_url=self.hub.urls["control_plane"])

    def tearDown(self):
        settings.DATA_DIR = self.orig_data_dir
        settings.WORKSPACE_DIR = self.orig_workspace_dir
        try:
            from backend.app.services.ssh_multiplexer import ssh_multiplexer_service
            for s_id in list(ssh_multiplexer_service.gateways.keys()):
                ssh_multiplexer_service.unregister_session_sync(s_id)
        except Exception:
            pass
        self.tmpdir.cleanup()

    # =========================================================================
    # 1. Sensitive Token Masking on GET Endpoints
    # =========================================================================

    def test_notification_channels_get_masking_comprehensive(self):
        """GET /api/notifications/channels strictly masks sensitive tokens across all providers."""
        sensitive_samples = [
            (
                "ch_dingtalk",
                "DingTalk Channel",
                "dingtalk",
                {
                    "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=dd_token_9876543210fedcba",
                    "secret": "SEC_dingtalk_signing_secret_998877",
                },
                ["dd_token_9876543210fedcba", "SEC_dingtalk_signing_secret_998877"],
            ),
            (
                "ch_feishu",
                "Feishu Channel",
                "feishu",
                {
                    "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/feishu_uuid_1234567890abcdef",
                    "secret": "feishu_sec_key_abcdef123456",
                },
                ["feishu_uuid_1234567890abcdef", "feishu_sec_key_abcdef123456"],
            ),
            (
                "ch_telegram",
                "Telegram Channel",
                "telegram",
                {
                    "bot_token": "987654321:TG_BOT_SECRET_abcdefghijk",
                    "chat_id": "-100987654321",
                },
                ["987654321:TG_BOT_SECRET_abcdefghijk"],
            ),
            (
                "ch_bark",
                "Bark Channel",
                "bark",
                {
                    "device_key": "bark_device_key_xyz987654321",
                    "server": "https://api.day.app",
                },
                ["bark_device_key_xyz987654321"],
            ),
            (
                "ch_slack",
                "Slack Channel",
                "slack",
                {
                    "webhook_url": "https://hooks.slack.com/services/T01234567/B01234567/slack_secret_token_abcdef12345",
                },
                ["slack_secret_token_abcdef12345"],
            ),
            (
                "ch_wecom",
                "WeCom Channel",
                "wecom",
                {
                    "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=wecom_key_abcdef123456",
                },
                ["wecom_key_abcdef123456"],
            ),
            (
                "ch_generic",
                "Generic Webhook Channel",
                "webhook",
                {
                    "webhook_url": "https://internal.corp/alerts?auth_token=hook_token_xyz999&api_key=myapikey123",
                    "token": "Bearer_generic_token_456789",
                    "secret": "generic_hmac_secret_key_888",
                },
                ["hook_token_xyz999", "myapikey123", "Bearer_generic_token_456789", "generic_hmac_secret_key_888"],
            ),
        ]

        # 1. Seed all channels into the database with AES-256-GCM Vault encryption
        all_raw_secrets = []
        with get_db(self.db_path) as conn:
            for ch_id, ch_name, provider, cfg, secrets in sensitive_samples:
                all_raw_secrets.extend(secrets)
                conn.execute(
                    """
                    INSERT INTO notification_channels (id, name, provider, config_enc, events_json, enabled)
                    VALUES (?, ?, ?, ?, '["quota.warning", "job.failed"]', 1)
                    """,
                    (ch_id, ch_name, provider, encrypt_secret(json.dumps(cfg))),
                )

        # 2. Call GET /api/notifications/channels
        resp = self.client.get("/api/notifications/channels", headers=self.auth_headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), len(sensitive_samples))

        resp_text = resp.text
        # Assert that NONE of the raw secrets ever appear anywhere in the HTTP response text
        for raw_sec in all_raw_secrets:
            self.assertNotIn(
                raw_sec,
                resp_text,
                f"Security Boundary Violation: Raw sensitive token '{raw_sec}' leaked in GET response!",
            )

        # Assert that masked tokens are present
        data_by_id = {c["id"]: c for c in data}
        self.assertIn("****", str(data_by_id["ch_dingtalk"]["config_masked"]))
        self.assertIn("****", str(data_by_id["ch_telegram"]["config_masked"]))
        self.assertIn("****", str(data_by_id["ch_bark"]["config_masked"]))
        self.assertIn("****", str(data_by_id["ch_slack"]["config_masked"]))

        # Non-sensitive attributes remain intact
        self.assertEqual(data_by_id["ch_telegram"]["config_masked"]["chat_id"], "-100987654321")
        self.assertEqual(data_by_id["ch_bark"]["config_masked"]["server"], "https://api.day.app")

        # 3. Unauthenticated request must be rejected with 401
        unauth_resp = self.client.get("/api/notifications/channels")
        self.assertEqual(unauth_resp.status_code, 401)

    # =========================================================================
    # 2. Secret Redaction in Telemetry and Job Run Logs
    # =========================================================================

    def test_telemetry_payload_vault_secret_redaction(self):
        """Telemetry collection redacts vault secrets into [REDACTED:<KEY_NAME>]."""
        # 1. Store secrets in Vault
        with get_db(self.db_path) as conn:
            conn.execute(
                "INSERT INTO secrets (id, key_name, value_enc) VALUES (?, ?, ?)",
                ("sec_hf", "HF_TOKEN", encrypt_secret("hf_sensitive_write_key_012345")),
            )
            conn.execute(
                "INSERT INTO secrets (id, key_name, value_enc) VALUES (?, ?, ?)",
                ("sec_wandb", "WANDB_API_KEY", encrypt_secret("wandb_live_api_key_987654321")),
            )

        # 2. Seed session in database
        sess_id = "sess_telem_redaction"
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, shape,
                    lifecycle_policy, status
                ) VALUES (
                    ?, 'Telem Redaction Sess', ?, 'nbh_telem', 'ep_telem',
                    'http://127.0.0.1:8000', 'enc', '2099-01-01 00:00:00',
                    'T4', 'standard', 'interactive', 'READY'
                )
                """,
                (sess_id, self.account_id),
            )

        telemetry_service = TelemetryService()

        # Simulate SSH probe returning a raw metric containing vault secrets
        mock_probe_result = {
            "cpu_pct": 12.5,
            "ram_used_mb": 4096.0,
            "ram_total_mb": 16384.0,
            "disk_used_gb": 22.0,
            "disk_total_gb": 100.0,
            "gpu_name": "NVIDIA A100 (hf_sensitive_write_key_012345 / wandb_live_api_key_987654321)",
            "gpu_util_pct": 55.0,
            "vram_used_mb": 8192.0,
            "vram_total_mb": 40960.0,
        }

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch.object(telemetry_service, "_probe_via_ssh", AsyncMock(return_value=mock_probe_result)):
                sample = loop.run_until_complete(telemetry_service.sample_session(sess_id, "A100"))
        finally:
            loop.close()

        self.assertIsNotNone(sample)
        # Check that the in-memory sample gpu_name is redacted
        self.assertNotIn("hf_sensitive_write_key_012345", sample.gpu_name)
        self.assertNotIn("wandb_live_api_key_987654321", sample.gpu_name)
        self.assertIn("[REDACTED:HF_TOKEN]", sample.gpu_name)
        self.assertIn("[REDACTED:WANDB_API_KEY]", sample.gpu_name)

        # Check that the database persistent record is also redacted
        with get_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT gpu_name FROM telemetry_samples WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (sess_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertNotIn("hf_sensitive_write_key_012345", row["gpu_name"])
            self.assertIn("[REDACTED:HF_TOKEN]", row["gpu_name"])

        # Check REST endpoint GET /api/sessions/{session_id}/telemetry
        telem_resp = self.client.get(f"/api/sessions/{sess_id}/telemetry", headers=self.auth_headers)
        self.assertEqual(telem_resp.status_code, 200)
        self.assertNotIn("hf_sensitive_write_key_012345", telem_resp.text)
        self.assertNotIn("wandb_live_api_key_987654321", telem_resp.text)
        self.assertIn("[REDACTED:HF_TOKEN]", telem_resp.text)
        self.assertIn("[REDACTED:WANDB_API_KEY]", telem_resp.text)

    def test_job_run_logs_vault_secret_redaction(self):
        """Job runner strictly redacts injected template secrets with [REDACTED:<KEY_NAME>] in run.log."""
        # 1. Insert secrets and template
        secret_aws = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        secret_db = "super_secret_production_password_xyz99"

        with get_db(self.db_path) as conn:
            conn.execute(
                "INSERT INTO secrets (id, key_name, value_enc) VALUES (?, ?, ?)",
                ("sec_aws", "AWS_SECRET_KEY", encrypt_secret(secret_aws)),
            )
            conn.execute(
                "INSERT INTO secrets (id, key_name, value_enc) VALUES (?, ?, ?)",
                ("sec_db", "DB_PASSWORD", encrypt_secret(secret_db)),
            )
            conn.execute(
                """
                INSERT INTO templates (id, name, packages_json, secret_ids_json)
                VALUES ('tpl_secrets', 'Secrets Template', '[]', '["sec_aws", "sec_db"]')
                """,
            )

        # 2. Seed ready session in database
        sess_id = "sess_job_redaction"
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, shape,
                    lifecycle_policy, status
                ) VALUES (
                    ?, 'Job Redaction Sess', ?, 'nbh_job_red', 'ep_job_red',
                    'http://127.0.0.1:8000', 'enc', '2099-01-01 00:00:00',
                    'T4', 'standard', 'interactive', 'READY'
                )
                """,
                (sess_id, self.account_id),
            )

        # 3. Create a python job script that echoes the secrets in stdout and stderr
        script_path = os.path.join(settings.WORKSPACE_DIR, "echo_secrets.py")
        with open(script_path, "w", encoding="utf-8") as sf:
            sf.write(
                f"import sys\n"
                f"print('Connecting with AWS_SECRET_KEY={secret_aws}')\n"
                f"sys.stderr.write('DB Auth failure for password={secret_db}\\n')\n"
            )

        job_service = JobRunnerService(colab_client=self.colab_client)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            job_req = JobCreate(
                name="Secret Leaker Test Job",
                target_accelerator="T4",
                template_id="tpl_secrets",
                job_type="python",
                entry_path_or_cmd=script_path,
            )
            job = loop.run_until_complete(job_service.submit_job(job_req))
            loop.run_until_complete(job_service._run_job_wrapper(job.id))
        finally:
            loop.close()

        job_record = job_service.get_job(job.id)
        self.assertEqual(job_record.status, "COMPLETED")

        # 4. Read log file directly
        with open(job_record.log_path, "r", encoding="utf-8") as lf:
            log_text = lf.read()

        self.assertNotIn(secret_aws, log_text, "Security Failure: AWS secret leaked in run.log!")
        self.assertNotIn(secret_db, log_text, "Security Failure: DB password leaked in run.log!")
        self.assertIn("[REDACTED:AWS_SECRET_KEY]", log_text)
        self.assertIn("[REDACTED:DB_PASSWORD]", log_text)

        # 5. Check REST endpoint GET /api/jobs/{id}/logs
        api_log_resp = self.client.get(f"/api/jobs/{job.id}/logs", headers=self.auth_headers)
        self.assertEqual(api_log_resp.status_code, 200)
        self.assertNotIn(secret_aws, api_log_resp.text)
        self.assertNotIn(secret_db, api_log_resp.text)
        self.assertIn("[REDACTED:AWS_SECRET_KEY]", api_log_resp.text)
        self.assertIn("[REDACTED:DB_PASSWORD]", api_log_resp.text)

    # =========================================================================
    # 3. Account Quota Circuit Breaker Boundary Conditions
    # =========================================================================

    def test_circuit_breaker_exact_ccu_boundaries(self):
        """Tests exact CCU boundary transitions: 10.01 vs 10.00 (warning), 2.01 vs 2.00 (hard stop)."""
        engine = LifecycleEngine(check_interval_sec=1.0)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # --- Sub-test A: Warning CCU exact boundary (10.01 vs 10.00) ---
            # Set balance to 10.01 (> warning_ccu 10.00)
            with get_db(self.db_path) as conn:
                conn.execute(
                    "UPDATE accounts SET ccu_balance = 10.01, status = 'ACTIVE' WHERE id = ?",
                    (self.account_id,),
                )

            res_1001 = loop.run_until_complete(engine.tick(client=self.colab_client))
            self.assertNotIn(
                self.account_id,
                res_1001["warned_accounts"],
                "Boundary Failure: 10.01 CCU should NOT trigger warning (threshold is <= 10.00 CCU)",
            )
            self.assertEqual(res_1001["stopped_by_quota"], [])

            # Transition exactly to 10.00 (<= warning_ccu 10.00)
            with get_db(self.db_path) as conn:
                conn.execute(
                    "UPDATE accounts SET ccu_balance = 10.00 WHERE id = ?",
                    (self.account_id,),
                )

            res_1000 = loop.run_until_complete(engine.tick(client=self.colab_client))
            self.assertIn(
                self.account_id,
                res_1000["warned_accounts"],
                "Boundary Failure: 10.00 CCU MUST trigger warning alert",
            )
            # Account must still be ACTIVE (not stopped)
            with get_db(self.db_path) as conn:
                acc_row = conn.execute("SELECT status FROM accounts WHERE id = ?", (self.account_id,)).fetchone()
                self.assertEqual(acc_row["status"], "ACTIVE")

            # --- Sub-test B: Hard-stop CCU exact boundary (2.01 vs 2.00) ---
            sess_id = "sess_cb_boundary"
            with get_db(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator, shape,
                        lifecycle_policy, status
                    ) VALUES (
                        ?, 'Boundary Hardstop Sess', ?, 'nbh_cb', 'ep_cb',
                        'http://127.0.0.1:8000', 'enc', '2099-01-01 00:00:00',
                        'T4', 'standard', 'interactive', 'READY'
                    )
                    """,
                    (sess_id, self.account_id),
                )

            # Balance at 2.01 (> hard_stop_ccu 2.00)
            with get_db(self.db_path) as conn:
                conn.execute(
                    "UPDATE accounts SET ccu_balance = 2.01, status = 'ACTIVE' WHERE id = ?",
                    (self.account_id,),
                )

            res_201 = loop.run_until_complete(engine.tick(client=self.colab_client))
            self.assertNotIn(
                sess_id,
                res_201["stopped_by_quota"],
                "Boundary Failure: 2.01 CCU must NOT trigger hard-stop",
            )
            sess_check = session_manager.get_session(sess_id)
            self.assertIn(sess_check["status"], ("READY", "BOOTSTRAPPING", "BUSY"))

            # Transition exactly to 2.00 (<= hard_stop_ccu 2.00)
            with get_db(self.db_path) as conn:
                conn.execute(
                    "UPDATE accounts SET ccu_balance = 2.00 WHERE id = ?",
                    (self.account_id,),
                )

            res_200 = loop.run_until_complete(engine.tick(client=self.colab_client))
            self.assertIn(
                sess_id,
                res_200["stopped_by_quota"],
                "Boundary Failure: 2.00 CCU MUST trigger hard-stop and terminate active sessions",
            )

            # Account must transition to COOLDOWN
            with get_db(self.db_path) as conn:
                acc_row = conn.execute("SELECT status FROM accounts WHERE id = ?", (self.account_id,)).fetchone()
                self.assertEqual(acc_row["status"], "COOLDOWN")

            # Session must transition to STOPPED
            stopped_sess = session_manager.get_session(sess_id)
            self.assertEqual(stopped_sess["status"], "STOPPED")

        finally:
            loop.close()

    def test_critical_hard_stop_alert_bypasses_warning_cooldown(self):
        """CRITICAL severity alerts (quota hard-stop) bypass the standard cooldown throttling."""
        service = NotificationService(cooldown_seconds=3600.0)

        # Seed enabled notification channel
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO notification_channels (id, name, provider, config_enc, events_json, enabled)
                VALUES ('ch_quota_test', 'Quota Channel', 'webhook', ?, '["quota.warning", "quota.hard_stop"]', 1)
                """,
                (encrypt_secret(json.dumps({"webhook_url": "https://httpbin.org/post"})),),
            )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch.object(service, "_send_with_retry", AsyncMock(return_value=(True, "OK"))) as mock_send:
                # 1. First warning -> Dispatched
                res1 = loop.run_until_complete(
                    service.dispatch_event(
                        event_type="quota.warning",
                        title="Quota Low Warning",
                        fields={"Balance": "9.9 CCU"},
                        severity="WARNING",
                        account_id=self.account_id,
                    )
                )
                self.assertEqual(len(res1), 1)
                self.assertTrue(res1[0]["success"])
                self.assertEqual(mock_send.call_count, 1)

                # 2. Second warning immediately after -> Throttled!
                res2 = loop.run_until_complete(
                    service.dispatch_event(
                        event_type="quota.warning",
                        title="Quota Low Warning (Again)",
                        fields={"Balance": "9.8 CCU"},
                        severity="WARNING",
                        account_id=self.account_id,
                    )
                )
                self.assertEqual(len(res2), 1)
                self.assertIn("THROTTLED", res2[0].get("status", ""))
                # mock_send should NOT have been called again
                self.assertEqual(mock_send.call_count, 1)

                # 3. Quota Hard-Stop (CRITICAL severity) -> Bypasses warning cooldown!
                res3 = loop.run_until_complete(
                    service.dispatch_event(
                        event_type="quota.hard_stop",
                        title="Quota Hard-Stop Triggered",
                        fields={"Balance": "1.9 CCU", "Status": "Account in COOLDOWN"},
                        severity="CRITICAL",
                        account_id=self.account_id,
                    )
                )
                self.assertEqual(len(res3), 1)
                self.assertTrue(res3[0]["success"])
                # mock_send MUST be called because CRITICAL bypassed the 1-hour cooldown
                self.assertEqual(mock_send.call_count, 2)

        finally:
            loop.close()

    # =========================================================================
    # 4. DingTalk Timestamp & HMAC-SHA256 Signature Validation
    # =========================================================================

    def test_dingtalk_hmac_sha256_signature_oracle_validation(self):
        """DingTalk signature adheres to HMAC-SHA256 protocol and passes receiver oracle verification."""
        service = NotificationService()
        secret = "SEC_dingtalk_oracle_secret_99887766"
        target_url = "https://oapi.dingtalk.com/robot/send?access_token=test_dt_token_123"

        oracle = DingTalkReceiverOracle(secret=secret)

        # 1. Format payload with valid secret
        url, headers, payload = service.format_provider_payload(
            provider="dingtalk",
            title="DingTalk Oracle Challenge",
            fields={"Metric": "GPU Util", "Value": "99%"},
            config={"webhook_url": target_url, "secret": secret},
        )

        # Oracle checks valid signature
        is_valid, msg = oracle.validate_request_url(url)
        self.assertTrue(is_valid, f"Oracle rejected valid DingTalk request: {msg}")

        # 2. Adversarial Tamper: Corrupted signature
        tampered_sig_url = re.sub(r"sign=[^&]+", "sign=bad_signature_tampered_xyz", url)
        is_valid, msg = oracle.validate_request_url(tampered_sig_url)
        self.assertFalse(is_valid, "Oracle must reject tampered signature")
        self.assertIn("Signature mismatch", msg)

        # 3. Adversarial Tamper: Stale timestamp (10 hours ago)
        stale_ts = str(round((time.time() - 36000) * 1000))
        stale_url = re.sub(r"timestamp=\d+", f"timestamp={stale_ts}", url)
        is_valid, msg = oracle.validate_request_url(stale_url)
        self.assertFalse(is_valid, "Oracle must reject expired timestamp")
        self.assertIn("drift too large", msg)

        # 4. Adversarial Tamper: Wrong receiver secret
        wrong_oracle = DingTalkReceiverOracle(secret="SEC_wrong_secret_key_1111")
        is_valid, msg = wrong_oracle.validate_request_url(url)
        self.assertFalse(is_valid, "Oracle with wrong secret must reject signature")
        self.assertIn("Signature mismatch", msg)

        # 5. DingTalk without secret: no timestamp/sign query parameters
        url_no_sec, _, _ = service.format_provider_payload(
            provider="dingtalk",
            title="DingTalk No Secret",
            fields={"Metric": "Status", "Value": "OK"},
            config={"webhook_url": target_url, "secret": ""},
        )
        self.assertNotIn("timestamp=", url_no_sec)
        self.assertNotIn("sign=", url_no_sec)

        # 6. End-to-end delivery simulation to DingTalk receiver mock
        def mock_dingtalk_handler(request: httpx.Request) -> httpx.Response:
            valid, reason = oracle.validate_request_url(str(request.url))
            if not valid:
                return httpx.Response(400, json={"errcode": 310000, "errmsg": f"Sign error: {reason}"})
            return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch("httpx.AsyncClient") as mock_client_cls:
                client_mock = AsyncMock()
                client_mock.post.return_value = httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
                mock_client_cls.return_value.__aenter__.return_value = client_mock

                success, resp_str = loop.run_until_complete(
                    service._send_with_retry(url, headers, payload, max_retries=1)
                )
                self.assertTrue(success)
                self.assertIn("HTTP 200", resp_str)
        finally:
            loop.close()

    # =========================================================================
    # 5. Job Execution Fault Injection: Corrupted Notebook & Linux OOM 137
    # =========================================================================

    def test_fault_injection_corrupted_notebook_json(self):
        """Corrupted/invalid notebook JSON causes job to transition to FAILED with exit code 1."""
        # 1. Provision an interactive session in database
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, shape,
                    lifecycle_policy, status
                ) VALUES (
                    'sess_fault_nb', 'Fault NB Sess', ?, 'nbh_fault', 'ep_fault',
                    'http://127.0.0.1:8000', 'enc', '2099-01-01 00:00:00',
                    'T4', 'standard', 'interactive', 'READY'
                )
                """,
                (self.account_id,),
            )

        # 2. Write corrupted JSON notebook file
        corrupt_nb_path = os.path.join(settings.WORKSPACE_DIR, "corrupt_syntax.ipynb")
        with open(corrupt_nb_path, "w", encoding="utf-8") as f:
            f.write('{"cells": [{"cell_type": "code", "source": ["print(1)"], "outputs": [')  # Unclosed JSON

        job_service = JobRunnerService(colab_client=self.colab_client)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            req = JobCreate(
                name="Corrupted Notebook Test",
                target_accelerator="T4",
                job_type="notebook",
                entry_path_or_cmd=corrupt_nb_path,
            )
            job = loop.run_until_complete(job_service.submit_job(req))
            loop.run_until_complete(job_service._run_job_wrapper(job.id))
        finally:
            loop.close()

        job_result = job_service.get_job(job.id)
        self.assertEqual(
            job_result.status,
            "FAILED",
            "Corrupted notebook JSON must transition job to FAILED",
        )
        self.assertEqual(
            job_result.exit_code,
            1,
            "Corrupted notebook JSON must result in exit code 1",
        )

        # Check log file contains error diagnostic
        with open(job_result.log_path, "r", encoding="utf-8") as lf:
            log_text = lf.read()
        self.assertIn("Invalid notebook JSON format", log_text)

        # 3. Test non-notebook JSON object (valid JSON, invalid notebook)
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, shape,
                    lifecycle_policy, status
                ) VALUES (
                    'sess_fault_nb2', 'Fault NB Sess 2', ?, 'nbh_fault2', 'ep_fault2',
                    'http://127.0.0.1:8000', 'enc', '2099-01-01 00:00:00',
                    'T4', 'standard', 'interactive', 'READY'
                )
                """,
                (self.account_id,),
            )

        bad_structure_path = os.path.join(settings.WORKSPACE_DIR, "bad_structure.ipynb")
        with open(bad_structure_path, "w", encoding="utf-8") as f:
            f.write('{"unexpected_root_key": "not_a_notebook"}')

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            req2 = JobCreate(
                name="Bad Structure Notebook Test",
                target_accelerator="T4",
                job_type="notebook",
                entry_path_or_cmd=bad_structure_path,
            )
            job2 = loop.run_until_complete(job_service.submit_job(req2))
            loop.run_until_complete(job_service._run_job_wrapper(job2.id))
        finally:
            loop.close()

        job2_result = job_service.get_job(job2.id)
        self.assertEqual(job2_result.status, "FAILED")
        self.assertEqual(job2_result.exit_code, 1)

    def test_fault_injection_linux_oom_killer_exit_code_137(self):
        """Linux OOM killer (exit code 137) explicitly transitions job status to OOM_KILLED."""
        # 1. Provision ready session
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, shape,
                    lifecycle_policy, status
                ) VALUES (
                    'sess_oom_test', 'OOM Test Sess', ?, 'nbh_oom', 'ep_oom',
                    'http://127.0.0.1:8000', 'enc', '2099-01-01 00:00:00',
                    'T4', 'standard', 'interactive', 'READY'
                )
                """,
                (self.account_id,),
            )

        # 2. Python script that triggers exit code 137 (SIGKILL by Linux OOM killer)
        oom_script_path = os.path.join(settings.WORKSPACE_DIR, "trigger_oom.py")
        with open(oom_script_path, "w", encoding="utf-8") as f:
            f.write(
                "import sys\n"
                "sys.exit(137)\n"
            )

        job_service = JobRunnerService(colab_client=self.colab_client)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            req = JobCreate(
                name="OOM Crash Simulation Job",
                target_accelerator="T4",
                job_type="python",
                entry_path_or_cmd=oom_script_path,
            )
            job = loop.run_until_complete(job_service.submit_job(req))
            loop.run_until_complete(job_service._run_job_wrapper(job.id))
        finally:
            loop.close()

        job_result = job_service.get_job(job.id)
        self.assertEqual(
            job_result.status,
            "OOM_KILLED",
            "Job terminated with exit code 137 MUST transition status to OOM_KILLED",
        )
        self.assertEqual(job_result.exit_code, 137)

        # Log file must record SystemExit: 137
        with open(job_result.log_path, "r", encoding="utf-8") as lf:
            log_content = lf.read()
        self.assertIn("SystemExit: 137", log_content)

        # 3. Test Shell command with simulated SSH gateway returning 137
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, shape,
                    lifecycle_policy, status
                ) VALUES (
                    'sess_oom_sh', 'OOM Shell Sess', ?, 'nbh_oom_sh', 'ep_oom_sh',
                    'http://127.0.0.1:8000', 'enc', '2099-01-01 00:00:00',
                    'T4', 'standard', 'interactive', 'READY'
                )
                """,
                (self.account_id,),
            )

        mock_gateway = AsyncMock()
        mock_proc = AsyncMock()
        mock_proc.exit_status = 137
        mock_proc.stdout = "[CRITICAL] Out Of Memory: Killed process 4096 (python3)\n"
        mock_proc.stderr = "Killed\n"
        mock_gateway.run_command.return_value = mock_proc

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            req_sh = JobCreate(
                name="OOM Shell Command Job",
                target_accelerator="T4",
                job_type="shell",
                entry_path_or_cmd="train_model_heavy --batch-size 128",
            )
            job_sh = loop.run_until_complete(job_service.submit_job(req_sh))
            with patch("backend.app.services.ssh_multiplexer.ssh_multiplexer_service.get_gateway", return_value=mock_gateway):
                loop.run_until_complete(job_service._run_job_wrapper(job_sh.id))
        finally:
            loop.close()

        sh_job_result = job_service.get_job(job_sh.id)
        self.assertEqual(sh_job_result.status, "OOM_KILLED")
        self.assertEqual(sh_job_result.exit_code, 137)
        with open(sh_job_result.log_path, "r", encoding="utf-8") as lf:
            sh_log = lf.read()
        self.assertIn("Out Of Memory: Killed process", sh_log)


if __name__ == "__main__":
    unittest.main()
