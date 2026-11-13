"""File Explorer, Jupyter Contents REST proxy, and Workspace Incremental Sync routes."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status

from backend.app.core.models import (
    SyncPullRequest,
    SyncPushRequest,
    SyncResultResponse,
)
from backend.app.core.security import get_current_admin
from backend.app.services import session_manager, workspace_sync
from backend.app.services.colab_client import ColabClient, ColabClientError

router = APIRouter(prefix="/api/sessions", tags=["files"])


def _get_proxy_client(session_id: str) -> tuple[ColabClient, str, str]:
    sess = session_manager.get_session_internal(session_id)
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )
    proxy_url = sess.get("proxy_url")
    proxy_token = sess.get("proxy_token")
    if not proxy_url or not proxy_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session runtime proxy information unavailable",
        )
    return ColabClient(), proxy_url, proxy_token


@router.get("/{session_id}/contents")
@router.get("/{session_id}/contents/{path:path}")
async def get_file_contents(
    session_id: str,
    path: str = "",
    content: Optional[str] = Query(None),
    admin=Depends(get_current_admin),
):
    """Retrieve directory listing or file content from Colab runtime."""
    client, proxy_url, proxy_token = _get_proxy_client(session_id)
    try:
        if content == "1":
            # File download
            result = await asyncio.to_thread(
                client.download_file, proxy_url, proxy_token, path
            )
        else:
            # Directory listing
            result = await asyncio.to_thread(
                client.list_contents, proxy_url, proxy_token, path
            )
        return result
    except ColabClientError as e:
        status_code = e.status_code or status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=str(e))


@router.put("/{session_id}/contents/{path:path}")
async def upload_file_contents(
    session_id: str,
    path: str,
    payload: Dict[str, Any] = Body(...),
    admin=Depends(get_current_admin),
):
    """Upload or update file content in Colab runtime (expects base64 content)."""
    client, proxy_url, proxy_token = _get_proxy_client(session_id)
    content_b64 = payload.get("content", "")
    file_name = payload.get("name")
    chunk = payload.get("chunk", 1)

    try:
        result = await asyncio.to_thread(
            client.upload_file,
            proxy_url,
            proxy_token,
            path,
            content_b64,
            file_name=file_name,
            chunk=chunk,
        )
        return result
    except ColabClientError as e:
        status_code = e.status_code or status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=str(e))


@router.delete("/{session_id}/contents/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file_path(
    session_id: str,
    path: str,
    admin=Depends(get_current_admin),
):
    """Delete a file or directory from Colab runtime."""
    client, proxy_url, proxy_token = _get_proxy_client(session_id)
    try:
        await asyncio.to_thread(client.delete_file, proxy_url, proxy_token, path)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ColabClientError as e:
        status_code = e.status_code or status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=status_code, detail=str(e))


# ---------------------------------------------------------------------------
# Workspace Incremental Sync Endpoints
# ---------------------------------------------------------------------------

@router.post("/{session_id}/sync/push", response_model=SyncResultResponse)
async def push_workspace_sync(
    session_id: str,
    req: Optional[SyncPushRequest] = None,
    admin=Depends(get_current_admin),
):
    """Push modified files from local ./workspace to remote Colab runtime."""
    push_req = req or SyncPushRequest()
    try:
        res = await workspace_sync.sync_push_session(session_id, push_req)
        return SyncResultResponse(**res)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Workspace push sync failed: {e}",
        )


@router.post("/{session_id}/sync/pull", response_model=SyncResultResponse)
async def pull_workspace_sync(
    session_id: str,
    req: Optional[SyncPullRequest] = None,
    admin=Depends(get_current_admin),
):
    """Pull modified files from remote Colab runtime to local ./workspace."""
    pull_req = req or SyncPullRequest()
    try:
        res = await workspace_sync.sync_pull_session(session_id, pull_req)
        return SyncResultResponse(**res)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Workspace pull sync failed: {e}",
        )


@router.get("/{session_id}/sync/status")
async def get_sync_status(
    session_id: str,
    admin=Depends(get_current_admin),
):
    """Get current sync path mapping and lock status for session."""
    try:
        local_dir, remote_prefix, _, _ = workspace_sync.resolve_session_sync_paths(session_id)
        lock = workspace_sync._get_sync_lock(session_id)
        return {
            "session_id": session_id,
            "local_dir": local_dir,
            "remote_prefix": remote_prefix,
            "is_syncing": lock.locked(),
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
