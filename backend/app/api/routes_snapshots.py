"""Environment Snapshot & Restore API routes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from backend.app.core.models import (
    SnapshotActionResponse,
    SnapshotResponse,
    SnapshotRestoreRequest,
    SnapshotSaveRequest,
    SnapshotSyncRequest,
)
from backend.app.core.security import get_current_admin
from backend.app.services import snapshot_service

router = APIRouter(tags=["snapshots"])


@router.get("/api/snapshots", response_model=List[SnapshotResponse])
async def list_snapshots_endpoint(
    account_id: Optional[str] = Query(None),
    admin=Depends(get_current_admin),
):
    """List environment snapshots stored in Google Drive."""
    return snapshot_service.list_snapshots(account_id=account_id)


@router.get("/api/snapshots/{snapshot_id}", response_model=SnapshotResponse)
async def get_snapshot_endpoint(
    snapshot_id: str,
    admin=Depends(get_current_admin),
):
    """Retrieve metadata for a single environment snapshot."""
    snap = snapshot_service.get_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Snapshot not found",
        )
    return snap


@router.delete("/api/snapshots/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_snapshot_endpoint(
    snapshot_id: str,
    admin=Depends(get_current_admin),
):
    """Delete a snapshot metadata record."""
    deleted = snapshot_service.delete_snapshot(snapshot_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Snapshot not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/snapshots/sync", response_model=SnapshotActionResponse)
async def sync_snapshots_endpoint(
    req: SnapshotSyncRequest,
    admin=Depends(get_current_admin),
):
    """Synchronize ColabSnapshots/*/manifest.json from mounted Google Drive into DB."""
    try:
        return await snapshot_service.sync_snapshots_from_drive(req.session_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.post(
    "/api/sessions/{session_id}/snapshots/save",
    response_model=SnapshotActionResponse,
)
async def save_session_snapshot_endpoint(
    session_id: str,
    req: SnapshotSaveRequest,
    admin=Depends(get_current_admin),
):
    """Save or overwrite a runtime environment snapshot in Google Drive."""
    try:
        return await snapshot_service.start_save_snapshot(session_id, req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.post(
    "/api/sessions/{session_id}/snapshots/restore",
    response_model=SnapshotActionResponse,
)
async def restore_session_snapshot_endpoint(
    session_id: str,
    req: SnapshotRestoreRequest,
    admin=Depends(get_current_admin),
):
    """Restore an existing Google Drive snapshot onto a running session."""
    try:
        return await snapshot_service.start_restore_snapshot(session_id, req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.get("/api/sessions/{session_id}/snapshots/task")
async def get_session_snapshot_task_endpoint(
    session_id: str,
    admin=Depends(get_current_admin),
) -> Dict[str, Any]:
    """Poll latest snapshot save/restore background task progress for a session."""
    task = snapshot_service.get_session_snapshot_task(session_id)
    if not task:
        return {"session_id": session_id, "status": "IDLE"}
    return task
