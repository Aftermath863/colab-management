"""Multi-Channel Notification Engine for Colab Management Platform.

Supports Feishu, DingTalk, WeChat Work, Telegram, Bark, Slack, and generic webhooks.
Includes AES-256-GCM credential encryption, sensitive token masking, exponential
backoff retry on transient errors, and cooldown throttling.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.app.core.database import get_db
from backend.app.core.security import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

EVENT_ALIASES: Dict[str, List[str]] = {
    "session.started": ["session.started", "session_started"],
    "session.stopped": ["session.stopped", "session_stopped"],
    "quota.warning": ["quota.warning", "quota_warning"],
    "quota.hard_stop": ["quota.hard_stop", "quota_hard_stop"],
    "job.started": ["job.started", "job_started"],
    "job.finished": ["job.finished", "job_finished", "job_completed"],
    "job.failed": ["job.failed", "job_failed"],
    "orphan.discovered": ["orphan.discovered", "orphan_discovered"],
    "sustained_high_vram": ["sustained_high_vram", "sustained.high.vram"],
}


def normalize_event_type(event_type: str) -> str:
    """Normalize event name to canonical dot notation where applicable."""
    for canonical, aliases in EVENT_ALIASES.items():
        if event_type in aliases:
            return canonical
    return event_type.replace("_", ".")


def mask_secret_str(s: str, visible_start: int = 4, visible_end: int = 4) -> str:
    """Mask a sensitive token string, leaving only prefix and suffix visible."""
    if not s:
        return ""
    if len(s) <= (visible_start + visible_end):
        return "****"
    return f"{s[:visible_start]}****{s[-visible_end:]}"


def mask_channel_config(provider: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Return a masked representation of channel config safe for API responses."""
    masked: Dict[str, Any] = {}
    for k, v in config.items():
        if not isinstance(v, str):
            masked[k] = v
            continue
        if k in ("secret", "token", "bot_token", "device_key", "password"):
            masked[k] = mask_secret_str(v, 3, 3)
        elif "url" in k or k == "webhook_url":
            try:
                parsed = urllib.parse.urlparse(v)
                if parsed.query:
                    qs = urllib.parse.parse_qs(parsed.query)
                    masked_qs = {
                        qk: [mask_secret_str(qv[0], 2, 2)] for qk, qv in qs.items()
                    }
                    query_str = urllib.parse.urlencode(masked_qs, doseq=True)
                    masked[k] = urllib.parse.urlunparse(parsed._replace(query=query_str))
                elif len(parsed.path) > 10:
                    parts = parsed.path.split("/")
                    if len(parts) > 1:
                        parts[-1] = mask_secret_str(parts[-1], 3, 3)
                    masked[k] = urllib.parse.urlunparse(parsed._replace(path="/".join(parts)))
                else:
                    masked[k] = mask_secret_str(v, 8, 4)
            except Exception:
                masked[k] = mask_secret_str(v, 8, 4)
        else:
            masked[k] = v
    return masked


class NotificationService:
    """Engine responsible for channel management, payload formatting, and webhook dispatch."""

    def __init__(self, cooldown_seconds: float = 3600.0):
        self.cooldown_seconds = cooldown_seconds
        self._last_dispatched: Dict[Tuple[str, str], float] = {}

    def format_provider_payload(
        self,
        provider: str,
        title: str,
        fields: Dict[str, Any],
        config: Dict[str, Any],
        event_type: str = "generic",
        severity: str = "INFO",
    ) -> Tuple[str, Dict[str, str], Any]:
        """Build provider-compliant request tuple: (target_url, headers, json_payload)."""
        headers = {"Content-Type": "application/json"}
        target_url = config.get("webhook_url", "")

        if provider == "feishu":
            elements = [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**{k}**: {v}"},
                }
                for k, v in fields.items()
            ]
            header_color = "red" if severity == "CRITICAL" else ("orange" if severity == "WARNING" else "blue")
            payload: Dict[str, Any] = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": title},
                        "template": header_color,
                    },
                    "elements": elements,
                },
            }
            secret = config.get("secret", "").strip()
            if secret:
                timestamp = str(int(time.time()))
                string_to_sign = f"{timestamp}\n{secret}"
                sign = base64.b64encode(
                    hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
                ).decode("utf-8")
                payload["timestamp"] = timestamp
                payload["sign"] = sign
            return target_url, headers, payload

        elif provider == "dingtalk":
            secret = config.get("secret", "").strip()
            url_to_use = target_url
            if secret:
                timestamp = str(round(time.time() * 1000))
                string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
                hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign, digestmod=hashlib.sha256).digest()
                sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
                delimiter = "&" if "?" in url_to_use else "?"
                url_to_use = f"{url_to_use}{delimiter}timestamp={timestamp}&sign={sign}"

            markdown_text = f"### {title}\n\n" + "\n".join([f"- **{k}**: {v}" for k, v in fields.items()])
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": markdown_text,
                },
            }
            return url_to_use, headers, payload

        elif provider == "wecom":
            markdown_content = f"### {title}\n" + "\n".join(
                [f"> **{k}**: <font color=\"comment\">{v}</font>" for k, v in fields.items()]
            )
            payload = {
                "msgtype": "markdown",
                "markdown": {"content": markdown_content},
            }
            return target_url, headers, payload

        elif provider == "telegram":
            bot_token = config.get("bot_token", "")
            chat_id = config.get("chat_id", "")
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            text_lines = [f"<b>{title}</b>"] + [f"<b>{k}</b>: {v}" for k, v in fields.items()]
            payload = {
                "chat_id": chat_id,
                "parse_mode": "HTML",
                "text": "\n".join(text_lines),
            }
            return url, headers, payload

        elif provider == "bark":
            device_key = config.get("device_key", "").strip()
            server = config.get("server", "https://api.day.app").rstrip("/")
            url = f"{server}/{device_key}/" if device_key else target_url
            body_text = "\n".join([f"{k}: {v}" for k, v in fields.items()])
            payload = {
                "title": title,
                "body": body_text,
                "group": "ColabManager",
                "icon": "https://colab.research.google.com/img/favicon.ico",
            }
            return url, headers, payload

        elif provider == "slack":
            blocks = [
                {"type": "header", "text": {"type": "plain_text", "text": title}},
                {
                    "type": "section",
                    "fields": [{"type": "mrkdwn", "text": f"*{k}*:\n{v}"} for k, v in fields.items()],
                },
            ]
            payload = {
                "text": title,
                "blocks": blocks,
                "title": title,
                "fields": fields,
            }
            return target_url, headers, payload

        else:  # generic webhook
            payload = {
                "event": event_type,
                "severity": severity,
                "title": title,
                "fields": fields,
                "timestamp": time.time(),
            }
            return target_url, headers, payload

    async def _send_with_retry(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Any,
        max_retries: int = 3,
        base_delay: float = 0.05,
    ) -> Tuple[bool, str]:
        """Dispatch HTTP POST with exponential backoff on transient errors (500, 502, 503, 504)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            last_resp_text = ""
            for attempt in range(max_retries):
                try:
                    resp = await client.post(url, headers=headers, json=payload)
                    last_resp_text = resp.text[:300]
                    if resp.status_code in (500, 502, 503, 504) and attempt < max_retries - 1:
                        await asyncio.sleep(base_delay * (2 ** attempt))
                        continue
                    if resp.is_success:
                        return True, f"HTTP {resp.status_code}: {last_resp_text}"
                    return False, f"HTTP {resp.status_code}: {last_resp_text}"
                except (httpx.RequestError, httpx.TimeoutException) as exc:
                    last_resp_text = str(exc)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(base_delay * (2 ** attempt))
                        continue
                    return False, f"Network Error: {last_resp_text}"
            return False, f"Delivery failed after {max_retries} attempts: {last_resp_text}"

    async def dispatch_event(
        self,
        event_type: str,
        title: str,
        fields: Dict[str, Any],
        severity: str = "INFO",
        account_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Dispatch event card to all enabled channels subscribed to this event type."""
        norm_event = normalize_event_type(event_type)
        aliases = EVENT_ALIASES.get(norm_event, [norm_event, event_type])

        entity_key = account_id or session_id or "global"
        throttle_key = (entity_key, norm_event)
        now = time.time()
        last_time = self._last_dispatched.get(throttle_key)
        if severity != "CRITICAL" and last_time is not None and (now - last_time) < self.cooldown_seconds:
            logger.info("Throttled notification event %s for %s", norm_event, entity_key)
            return [{"status": f"THROTTLED:{norm_event}", "dispatched": 0}]

        with get_db() as conn:
            channels = conn.execute(
                "SELECT id, name, provider, config_enc, events_json FROM notification_channels WHERE enabled = 1"
            ).fetchall()

        results: List[Dict[str, Any]] = []
        for ch in channels:
            ch_events = json.loads(ch["events_json"])
            is_subscribed = any(alias in ch_events for alias in aliases) or severity == "CRITICAL"
            if not is_subscribed:
                continue

            try:
                config = json.loads(decrypt_secret(ch["config_enc"]))
                url, headers, payload = self.format_provider_payload(
                    provider=ch["provider"],
                    title=title,
                    fields=fields,
                    config=config,
                    event_type=norm_event,
                    severity=severity,
                )
                success, response_msg = await self._send_with_retry(url, headers, payload)
                results.append({
                    "channel_id": ch["id"],
                    "channel_name": ch["name"],
                    "provider": ch["provider"],
                    "success": success,
                    "response": response_msg,
                })
            except Exception as e:
                logger.error("Failed to dispatch to channel %s: %s", ch["id"], e)
                results.append({
                    "channel_id": ch["id"],
                    "channel_name": ch["name"],
                    "provider": ch["provider"],
                    "success": False,
                    "response": str(e),
                })

        if results:
            self._last_dispatched[throttle_key] = now
        return results

    def dispatch_event_sync(
        self,
        event_type: str,
        payload: Dict[str, Any],
        title: Optional[str] = None,
        severity: str = "INFO",
        account_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Helper to dispatch notification from synchronous code."""
        t = title or f"Colab Notification: {event_type}"
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(
                    self.dispatch_event(
                        event_type=event_type,
                        title=t,
                        fields=payload,
                        severity=severity,
                        account_id=account_id,
                        session_id=session_id,
                    )
                )
        except RuntimeError:
            asyncio.run(
                self.dispatch_event(
                    event_type=event_type,
                    title=t,
                    fields=payload,
                    severity=severity,
                    account_id=account_id,
                    session_id=session_id,
                )
            )

    async def test_channel(self, channel_id: str) -> Dict[str, Any]:
        """Dispatch a single test verification alert to a specific channel."""
        with get_db() as conn:
            ch = conn.execute(
                "SELECT id, name, provider, config_enc FROM notification_channels WHERE id = ?",
                (channel_id,),
            ).fetchone()
            if not ch:
                raise ValueError(f"Notification channel {channel_id} not found")

        config = json.loads(decrypt_secret(ch["config_enc"]))
        title = f"[TEST] Colab Management Notification: {ch['name']}"
        fields = {
            "Channel Name": ch["name"],
            "Provider": ch["provider"],
            "Timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "Status": "Verification Probe OK",
        }
        url, headers, payload = self.format_provider_payload(
            provider=ch["provider"],
            title=title,
            fields=fields,
            config=config,
            event_type="channel.test",
            severity="INFO",
        )
        success, response_msg = await self._send_with_retry(url, headers, payload, max_retries=1)
        return {
            "success": success,
            "response": response_msg,
            "channel_id": channel_id,
            "channel_name": ch["name"],
        }


notification_service = NotificationService()
