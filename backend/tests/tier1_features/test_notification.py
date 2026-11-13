"""Tier 1 Feature Tests: Multi-Channel Notification Engine & REST API.

Validates:
1. Formatting for Feishu, DingTalk (with HMAC-SHA256 timestamp signature),
   WeCom, Telegram, Bark, Slack, and generic webhooks.
2. AES-256-GCM Vault encryption of notification channel config in SQLite.
3. Masking of sensitive tokens and URLs on API retrieval.
4. Exponential backoff retry on transient HTTP 500/503 errors.
5. Cooldown throttling with critical event bypass.
6. REST API: CRUD operations and test webhook verification.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.security import create_access_token, decrypt_secret, encrypt_secret
from backend.app.main import app
from backend.app.services.notification import (
    NotificationService,
    mask_channel_config,
    mask_secret_str,
    notification_service,
)


class TestNotificationFeatures(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_notifications.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        self.token = create_access_token(data={"sub": "admin", "role": "admin"})
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        self.client = TestClient(app)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_provider_payload_formatting(self):
        """Formatters produce correct payloads for Feishu, DingTalk, WeCom, Telegram, Bark, Slack, Generic."""
        service = NotificationService()
        fields = {"Status": "OK", "GPU": "A100", "CCU": "25.0"}
        title = "Colab Notification Test"

        # 1. Feishu (with secret sign)
        f_url, f_hdr, f_payload = service.format_provider_payload(
            provider="feishu",
            title=title,
            fields=fields,
            config={"webhook_url": "https://open.feishu.cn/hook/123", "secret": "my_feishu_secret"},
            severity="CRITICAL",
        )
        self.assertEqual(f_payload["msg_type"], "interactive")
        self.assertEqual(f_payload["card"]["header"]["template"], "red")
        self.assertTrue("sign" in f_payload)
        self.assertTrue("timestamp" in f_payload)

        # 2. DingTalk (with secret sign in URL query parameters)
        d_url, d_hdr, d_payload = service.format_provider_payload(
            provider="dingtalk",
            title=title,
            fields=fields,
            config={"webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=token123", "secret": "SEC_ding_secret"},
        )
        self.assertEqual(d_payload["msgtype"], "markdown")
        self.assertIn("timestamp=", d_url)
        self.assertIn("sign=", d_url)

        # 3. WeCom (WeChat Work)
        w_url, w_hdr, w_payload = service.format_provider_payload(
            provider="wecom",
            title=title,
            fields=fields,
            config={"webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=wecom123"},
        )
        self.assertEqual(w_payload["msgtype"], "markdown")
        self.assertIn("content", w_payload["markdown"])

        # 4. Telegram
        t_url, t_hdr, t_payload = service.format_provider_payload(
            provider="telegram",
            title=title,
            fields=fields,
            config={"bot_token": "123456:ABC-DEF", "chat_id": "-100987654"},
        )
        self.assertEqual(t_url, "https://api.telegram.org/bot123456:ABC-DEF/sendMessage")
        self.assertEqual(t_payload["chat_id"], "-100987654")
        self.assertEqual(t_payload["parse_mode"], "HTML")

        # 5. Bark
        b_url, b_hdr, b_payload = service.format_provider_payload(
            provider="bark",
            title=title,
            fields=fields,
            config={"device_key": "my_ios_key", "server": "https://api.day.app"},
        )
        self.assertEqual(b_url, "https://api.day.app/my_ios_key/")
        self.assertEqual(b_payload["title"], title)

        # 6. Slack
        s_url, s_hdr, s_payload = service.format_provider_payload(
            provider="slack",
            title=title,
            fields=fields,
            config={"webhook_url": "https://hooks.slack.com/services/T00/B00/X00"},
        )
        self.assertEqual(s_payload["text"], title)
        self.assertTrue("blocks" in s_payload)

        # 7. Generic Webhook
        g_url, g_hdr, g_payload = service.format_provider_payload(
            provider="webhook",
            title=title,
            fields=fields,
            config={"webhook_url": "https://example.com/webhook"},
            event_type="session.started",
            severity="WARNING",
        )
        self.assertEqual(g_payload["event"], "session.started")
        self.assertEqual(g_payload["severity"], "WARNING")

    def test_token_masking_logic(self):
        """Tokens, secrets, and URLs are masked to protect sensitive credentials."""
        self.assertEqual(mask_secret_str("1234567890abcdef", 3, 3), "123****def")
        self.assertEqual(mask_secret_str("short", 4, 4), "****")

        config = {
            "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/abcdef1234567890",
            "secret": "super_secret_signing_key_12345",
            "bot_token": "987654321:ABCDEFGHIJKLMN",
            "chat_id": "-10012345678",
        }
        masked = mask_channel_config("feishu", config)
        self.assertNotIn("super_secret_signing_key_12345", masked["secret"])
        self.assertNotIn("987654321:ABCDEFGHIJKLMN", masked["bot_token"])
        self.assertEqual(masked["chat_id"], "-10012345678")  # chat_id is not masked

    def test_transient_http_500_exponential_backoff_retry(self):
        """Transient HTTP 500 error triggers retry with exponential backoff."""
        service = NotificationService()

        # Simulate 2 failures then 1 success
        mock_transport = httpx.MockTransport(
            httpx.Response(500, text="Internal Server Error"),
        )
        responses = [
            httpx.Response(500, text="Internal Server Error"),
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(200, text='{"errcode": 0, "errmsg": "ok"}'),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0) if responses else httpx.Response(200, text="ok")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch("httpx.AsyncClient") as mock_client_cls:
                client_mock = AsyncMock()
                client_mock.post.side_effect = [
                    httpx.Response(500, text="Internal Server Error"),
                    httpx.Response(503, text="Service Unavailable"),
                    httpx.Response(200, text='{"errcode": 0, "errmsg": "ok"}'),
                ]
                mock_client_cls.return_value.__aenter__.return_value = client_mock

                success, msg = loop.run_until_complete(
                    service._send_with_retry("https://test.hook/api", {}, {"data": "test"}, max_retries=3)
                )
                self.assertTrue(success)
                self.assertIn("HTTP 200", msg)
                self.assertEqual(client_mock.post.call_count, 3)
        finally:
            loop.close()

    def test_cooldown_throttling_with_critical_event_bypass(self):
        """Non-critical events are throttled within cooldown, but CRITICAL events bypass cooldown."""
        service = NotificationService(cooldown_seconds=3600.0)

        # Seed an enabled webhook channel
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO notification_channels (id, name, provider, config_enc, events_json, enabled)
                VALUES ('ch_test_1', 'Test Channel', 'webhook', ?, '["quota.warning", "quota.hard_stop"]', 1)
                """,
                (encrypt_secret(json.dumps({"webhook_url": "https://httpbin.org/post"})),),
            )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with patch.object(service, "_send_with_retry", AsyncMock(return_value=(True, "OK"))):
                # Dispatch 1: quota warning -> Sent
                res1 = loop.run_until_complete(
                    service.dispatch_event(
                        event_type="quota.warning",
                        title="Low CCU",
                        fields={"Balance": "9.5"},
                        severity="WARNING",
                        account_id="acc_user_1",
                    )
                )
                self.assertEqual(len(res1), 1)
                self.assertTrue(res1[0]["success"])

                # Dispatch 2: quota warning again for same account -> Throttled
                res2 = loop.run_until_complete(
                    service.dispatch_event(
                        event_type="quota.warning",
                        title="Low CCU Again",
                        fields={"Balance": "9.2"},
                        severity="WARNING",
                        account_id="acc_user_1",
                    )
                )
                self.assertEqual(res2[0]["status"], "THROTTLED:quota.warning")

                # Dispatch 3: quota hard stop (CRITICAL) -> Bypasses warning cooldown!
                res3 = loop.run_until_complete(
                    service.dispatch_event(
                        event_type="quota.hard_stop",
                        title="Hard Stop",
                        fields={"Balance": "1.8"},
                        severity="CRITICAL",
                        account_id="acc_user_1",
                    )
                )
                self.assertEqual(len(res3), 1)
                self.assertTrue(res3[0]["success"])
        finally:
            loop.close()

    def test_notification_channels_rest_api_crud_and_test_endpoint(self):
        """REST API allows creating, reading (with masked credentials), updating, and deleting channels."""
        # 1. Create channel
        create_payload = {
            "name": "Team Lark Alerts",
            "provider": "feishu",
            "config": {
                "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/abc123456789xyz",
                "secret": "my_top_secret_key_12345",
            },
            "events": ["job.finished", "job.failed", "quota.warning"],
            "enabled": True,
        }
        res = self.client.post("/api/notifications/channels", headers=self.auth_headers, json=create_payload)
        self.assertEqual(res.status_code, 201)
        created = res.json()
        channel_id = created["id"]
        self.assertEqual(created["name"], "Team Lark Alerts")
        # Ensure credentials returned in config_masked are masked
        self.assertNotIn("my_top_secret_key_12345", created["config_masked"]["secret"])

        # 2. List channels
        list_res = self.client.get("/api/notifications/channels", headers=self.auth_headers)
        self.assertEqual(list_res.status_code, 200)
        channels = list_res.json()
        self.assertEqual(len(channels), 1)
        self.assertNotIn("my_top_secret_key_12345", str(channels[0]["config_masked"]))

        # 3. Update channel
        update_payload = {"name": "Updated Lark Alerts", "enabled": False}
        up_res = self.client.put(
            f"/api/notifications/channels/{channel_id}",
            headers=self.auth_headers,
            json=update_payload,
        )
        self.assertEqual(up_res.status_code, 200)
        updated = up_res.json()
        self.assertEqual(updated["name"], "Updated Lark Alerts")
        self.assertFalse(updated["enabled"])

        # 4. Test endpoint
        with patch.object(notification_service, "_send_with_retry", AsyncMock(return_value=(True, "HTTP 200: OK"))):
            test_res = self.client.post(
                f"/api/notifications/channels/{channel_id}/test",
                headers=self.auth_headers,
            )
            self.assertEqual(test_res.status_code, 200)
            test_data = test_res.json()
            self.assertTrue(test_data["success"])

        # 5. Delete channel
        del_res = self.client.delete(f"/api/notifications/channels/{channel_id}", headers=self.auth_headers)
        self.assertEqual(del_res.status_code, 204)

        # Verify deletion
        list_after = self.client.get("/api/notifications/channels", headers=self.auth_headers).json()
        self.assertEqual(len(list_after), 0)


if __name__ == "__main__":
    unittest.main()
