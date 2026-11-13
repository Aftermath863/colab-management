"""Telemetry REST API endpoints."""

from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.app.core.database import get_db
from backend.app.core.models import TelemetryResponse, TelemetrySample
from backend.app.core.security import get_current_admin
from backend.app.services import session_manager

router = APIRouter(prefix="/api/sessions", tags=["telemetry"])


@router.get("/{session_id}/telemetry", response_model=TelemetryResponse)
async def get_session_telemetry(
    session_id: str,
    limit: int = Query(100, ge=1, le=500),
    admin=Depends(get_current_admin),
):
    """Retrieve real-time and historical telemetry samples for a session."""
    sess = session_manager.get_session(session_id)
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT session_id, ts, kernel_state, cpu_pct, ram_used_mb, ram_total_mb,
                   disk_used_gb, disk_total_gb, gpu_name, gpu_util_pct,
                   vram_used_mb, vram_total_mb
            FROM telemetry_samples
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()

    samples = [TelemetrySample(**dict(row)) for row in reversed(rows)]
    latest = samples[-1] if samples else None

    return TelemetryResponse(
        session_id=session_id,
        latest=latest,
        history=samples,
    )


@router.post("/{session_id}/telemetry", response_model=TelemetrySample, status_code=status.HTTP_201_CREATED)
async def record_session_telemetry(
    session_id: str,
    sample: TelemetrySample,
    admin=Depends(get_current_admin),
):
    """Directly insert a telemetry sample (useful for test fixtures and external sidecars)."""
    sess = session_manager.get_session(session_id)
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO telemetry_samples (
                session_id, kernel_state, cpu_pct, ram_used_mb, ram_total_mb,
                disk_used_gb, disk_total_gb, gpu_name, gpu_util_pct,
                vram_used_mb, vram_total_mb
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                sample.kernel_state,
                sample.cpu_pct,
                sample.ram_used_mb,
                sample.ram_total_mb,
                sample.disk_used_gb,
                sample.disk_total_gb,
                sample.gpu_name,
                sample.gpu_util_pct,
                sample.vram_used_mb,
                sample.vram_total_mb,
            ),
        )

    return sample
