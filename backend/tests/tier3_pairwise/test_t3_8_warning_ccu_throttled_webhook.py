"""T3.8: Warning CCU x Throttled Webhook Card Alert (Telegram/Feishu).

Validates that:
1. When an account's CCU balance falls below warning_ccu, a formatted card alert
   is dispatched to configured webhook channels (e.g. Feishu, Telegram).
2. Duplicate warning alerts for the same account are strictly throttled during the cooldown window.
3. A subsequent drop below hard_stop_ccu triggers an immediate, unthrottled critical alert.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.security import encrypt_secret
from backend.app.services import account_pool
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class MockWebhookReceiver:
    """In-memory mock endpoint that captures dispatched webhook payloads."""

    def __init__(self):
        self.dispatched_requests: List[Dict[str, Any]] = []

    def receive_webhook(self, url: str, payload: Dict[str, Any]) -> None:
        self.dispatched_requests.append({"url": url, "payload": payload, "received_at": time.time()})

    def clear(self) -> None:
        self.dispatched_requests.clear()


class NotificationDispatcher:
    """Formats and dispatches rich card alerts with event-based cooldown throttling."""

    def __init__(self, receiver: MockWebhookReceiver, cooldown_seconds: float = 3600.0):
        self.receiver = receiver
        self.cooldown_seconds = cooldown_seconds
        # Tracks {(account_id, event_type): last_dispatched_timestamp}
        self._last_dispatched: Dict[tuple[str, str], float] = {}

    def format_card_payload(self, provider: str, title: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Build provider-compliant card message."""
        if provider == "feishu":
            elements = [{"tag": "div", "text": {"content": f"**{k}**: {v}", "tag": "lark_md"}} for k, v in fields.items()]
            return {
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"content": title, "tag": "plain_text"}},
                    "elements": elements,
                },
            }
        elif provider == "telegram":
            text_lines = [f"<b>{title}</b>"] + [f"<b>{k}</b>: {v}" for k, v in fields.items()]
            return {"parse_mode": "HTML", "text": "\n".join(text_lines)}
        else:
            return {"title": title, "fields": fields}

    def check_and_dispatch_account_alert(
        self,
        account_id: str,
        current_timestamp: float,
    ) -> Optional[str]:
        with get_db() as conn:
            acc = conn.execute(
                """
                SELECT id, email, display_name, ccu_balance, burn_rate_hourly,
                       warning_ccu, hard_stop_ccu, status
                FROM accounts WHERE id = ?
                """,
                (account_id,),
            ).fetchone()
            if not acc:
                return None

            channels = conn.execute(
                "SELECT id, name, provider, config_enc, events_json FROM notification_channels WHERE enabled = 1"
            ).fetchall()

        balance = acc["ccu_balance"]
        warning_ccu = acc["warning_ccu"]
        hard_stop_ccu = acc["hard_stop_ccu"]
        email = acc["email"]

        event_type = None
        severity = "INFO"
        if balance <= hard_stop_ccu:
            event_type = "quota_hard_stop"
            severity = "CRITICAL"
        elif balance <= warning_ccu:
            event_type = "quota_warning"
            severity = "WARNING"

        if not event_type:
            return None

        # Check throttling for this specific event type
        # Critical hard_stop events do NOT share throttle with warning events
        throttle_key = (account_id, event_type)
        last_time = self._last_dispatched.get(throttle_key)
        if last_time is not None and (current_timestamp - last_time) < self.cooldown_seconds:
            # Throttled!
            return f"THROTTLED:{event_type}"

        # Dispatch to all enabled notification channels
        dispatched_count = 0
        for ch in channels:
            events = json.loads(ch["events_json"])
            if event_type in events or severity == "CRITICAL":
                from backend.app.core.security import decrypt_secret
                config = json.loads(decrypt_secret(ch["config_enc"]))
                webhook_url = config.get("webhook_url", "http://webhook.local/alert")

                title = f"[{severity}] Colab Quota Alert: {email}"
                fields = {
                    "Account": email,
                    "Current Balance": f"{balance:.2f} CCU",
                    "Burn Rate": f"{acc['burn_rate_hourly']:.2f} CCU/hr",
                    "Threshold": f"{hard_stop_ccu if severity == 'CRITICAL' else warning_ccu:.2f} CCU",
                    "Action Required": "Switch accounts or purchase compute units" if severity == "WARNING" else "Sessions paused due to quota limit",
                }
                card = self.format_card_payload(ch["provider"], title, fields)
                card["event"] = event_type
                card["account_id"] = account_id
                self.receiver.receive_webhook(webhook_url, card)
                dispatched_count += 1

        self._last_dispatched[throttle_key] = current_timestamp
        return event_type if dispatched_count > 0 else None


class TestT38WarningCCUThrottledWebhookAlert(unittest.TestCase):
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

        # Register account
        self.account_id = "acc_webhook_ccu_test"
        self.email = "quota_user@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Quota User",
            sub="sub_quota_123",
            code="code_quota",
            access_token="ya29.mock_token_quota",
            refresh_token="1//mock_refresh_quota",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_token_quota",
            "refresh_token": "1//mock_refresh_quota",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Quota User', ?, 12.0, 1.5, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

            # Insert Feishu webhook channel
            ch_config = {"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/mock_token_123"}
            conn.execute(
                """
                INSERT INTO notification_channels (id, name, provider, config_enc, events_json, enabled)
                VALUES ('ch_feishu_1', 'Team Alerts', 'feishu', ?, ?, 1)
                """,
                (
                    encrypt_secret(json.dumps(ch_config)),
                    json.dumps(["quota_warning", "quota_hard_stop"]),
                ),
            )

        self.mock_receiver = MockWebhookReceiver()
        self.dispatcher = NotificationDispatcher(self.mock_receiver, cooldown_seconds=3600.0)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t3_8_ccu_warning_dispatches_formatted_card_and_throttles_duplicates(self):
        t0 = 10000.0

        # 1. Base check: balance is 12.0 (> 10.0 warning_ccu) -> No alerts sent
        res = self.dispatcher.check_and_dispatch_account_alert(self.account_id, current_timestamp=t0)
        self.assertIsNone(res)
        self.assertEqual(len(self.mock_receiver.dispatched_requests), 0)

        # 2. Sync 1: Colab reports balance dropped to 9.5 (< 10.0 warning threshold)
        self.hub.set_account_ccu(self.email, balance=9.5, burn_rate=1.8)
        self.hub.control_plane.state.ccu_balance = 9.5
        self.hub.control_plane.state.consumption_rate_hourly = 1.8
        account_pool.sync_account(self.account_id)

        # Dispatch alert at t0 + 60s
        res = self.dispatcher.check_and_dispatch_account_alert(self.account_id, current_timestamp=t0 + 60.0)
        self.assertEqual(res, "quota_warning")

        # Invariant: exactly 1 formatted card request received
        self.assertEqual(len(self.mock_receiver.dispatched_requests), 1)
        req = self.mock_receiver.dispatched_requests[0]
        self.assertEqual(req["url"], "https://open.feishu.cn/open-apis/bot/v2/hook/mock_token_123")
        self.assertEqual(req["payload"]["msg_type"], "interactive")
        self.assertEqual(req["payload"]["event"], "quota_warning")
        self.assertIn("9.50 CCU", json.dumps(req["payload"]))

        # 3. Sync 2: 5 minutes later (t0 + 360s), balance is 9.2 (< 10.0)
        # 300s elapsed since last alert < 3600s cooldown window
        self.hub.set_account_ccu(self.email, balance=9.2, burn_rate=1.8)
        self.hub.control_plane.state.ccu_balance = 9.2
        account_pool.sync_account(self.account_id)

        res = self.dispatcher.check_and_dispatch_account_alert(self.account_id, current_timestamp=t0 + 360.0)
        self.assertEqual(res, "THROTTLED:quota_warning")

        # Invariant: 0 additional webhooks sent (duplicate warning suppressed)
        self.assertEqual(len(self.mock_receiver.dispatched_requests), 1)

        # 4. Sync 3: Balance drops to 1.8 (< hard_stop_ccu 2.0)
        # Invariant: Critical hard stop event bypasses warning throttling and delivers immediately
        self.hub.set_account_ccu(self.email, balance=1.8, burn_rate=2.0)
        self.hub.control_plane.state.ccu_balance = 1.8
        account_pool.sync_account(self.account_id)

        res = self.dispatcher.check_and_dispatch_account_alert(self.account_id, current_timestamp=t0 + 400.0)
        self.assertEqual(res, "quota_hard_stop")

        # Invariant: 2nd alert received for quota_hard_stop
        self.assertEqual(len(self.mock_receiver.dispatched_requests), 2)
        req2 = self.mock_receiver.dispatched_requests[1]
        self.assertEqual(req2["payload"]["event"], "quota_hard_stop")
        self.assertIn("CRITICAL", json.dumps(req2["payload"]))
        self.assertIn("1.80 CCU", json.dumps(req2["payload"]))


if __name__ == "__main__":
    unittest.main()
