"""REST API routes for Notification Channel management and webhook testing."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.core.database import get_db
from backend.app.core.models import (
    NotificationChannelCreate,
    NotificationChannelResponse,
    NotificationProvider,
)
from backend.app.core.security import decrypt_secret, encrypt_secret, get_current_admin
from backend.app.services.notification import mask_channel_config, notification_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class ChannelCreateRequest(BaseModel):
    name: str
    provider: NotificationProvider
    config: Dict[str, Any]
    events: List[str] = Field(
        default_factory=lambda: [
            "session.started",
            "session.stopped",
            "quota.warning",
            "quota.hard_stop",
            "job.started",
            "job.finished",
            "job.failed",
            "orphan.discovered",
        ]
    )
    enabled: bool = True


class ChannelUpdateRequest(BaseModel):
    name: Optional[str] = None
    provider: Optional[NotificationProvider] = None
    config: Optional[Dict[str, Any]] = None
    events: Optional[List[str]] = None
    enabled: Optional[bool] = None


@router.get("/channels", response_model=List[NotificationChannelResponse])
async def list_channels(admin=Depends(get_current_admin)):
    """List all configured notification channels with sensitive tokens masked."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, provider, config_enc, events_json, enabled FROM notification_channels ORDER BY name ASC"
        ).fetchall()

    result = []
    for r in rows:
        try:
            cfg = json.loads(decrypt_secret(r["config_enc"]))
        except Exception:
            cfg = {}
        result.append(
            NotificationChannelResponse(
                id=r["id"],
                name=r["name"],
                provider=r["provider"],
                events=json.loads(r["events_json"]),
                enabled=bool(r["enabled"]),
                config_masked=mask_channel_config(r["provider"], cfg),
            )
        )
    return result


@router.post("/channels", response_model=NotificationChannelResponse, status_code=status.HTTP_201_CREATED)
async def create_channel(body: ChannelCreateRequest, admin=Depends(get_current_admin)):
    """Create a new notification channel, encrypting credentials via AES-256-GCM Vault."""
    channel_id = f"notif_{uuid.uuid4().hex[:12]}"
    config_enc = encrypt_secret(json.dumps(body.config))
    events_json = json.dumps(body.events)

    with get_db() as conn:
        existing = conn.execute("SELECT id FROM notification_channels WHERE name = ?", (body.name,)).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail=f"Channel with name '{body.name}' already exists")
        conn.execute(
            """
            INSERT INTO notification_channels (id, name, provider, config_enc, events_json, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (channel_id, body.name, body.provider, config_enc, events_json, int(body.enabled)),
        )

    return NotificationChannelResponse(
        id=channel_id,
        name=body.name,
        provider=body.provider,
        events=body.events,
        enabled=body.enabled,
        config_masked=mask_channel_config(body.provider, body.config),
    )


@router.put("/channels/{channel_id}", response_model=NotificationChannelResponse)
async def update_channel(
    channel_id: str,
    body: ChannelUpdateRequest,
    admin=Depends(get_current_admin),
):
    """Update existing notification channel configuration."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name, provider, config_enc, events_json, enabled FROM notification_channels WHERE id = ?",
            (channel_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Notification channel not found")

        curr_cfg = json.loads(decrypt_secret(row["config_enc"]))
        new_name = body.name if body.name is not None else row["name"]
        new_provider = body.provider if body.provider is not None else row["provider"]
        new_enabled = int(body.enabled) if body.enabled is not None else row["enabled"]
        new_events = json.dumps(body.events) if body.events is not None else row["events_json"]

        if body.config is not None:
            curr_cfg.update(body.config)
        new_config_enc = encrypt_secret(json.dumps(curr_cfg))

        conn.execute(
            """
            UPDATE notification_channels
            SET name = ?, provider = ?, config_enc = ?, events_json = ?, enabled = ?
            WHERE id = ?
            """,
            (new_name, new_provider, new_config_enc, new_events, new_enabled, channel_id),
        )

    return NotificationChannelResponse(
        id=channel_id,
        name=new_name,
        provider=new_provider,
        events=json.loads(new_events),
        enabled=bool(new_enabled),
        config_masked=mask_channel_config(new_provider, curr_cfg),
    )


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: str, admin=Depends(get_current_admin)):
    """Delete a notification channel."""
    with get_db() as conn:
        res = conn.execute("DELETE FROM notification_channels WHERE id = ?", (channel_id,))
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail="Notification channel not found")
    return None


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: str, admin=Depends(get_current_admin)):
    """Trigger a live test alert to verify webhook configuration and connectivity."""
    try:
        result = await notification_service.test_channel(channel_id)
        return result
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        return {"success": False, "response": f"Error: {str(e)}", "channel_id": channel_id}
