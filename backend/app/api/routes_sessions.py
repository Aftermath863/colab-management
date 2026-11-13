"""Session allocation, teardown, and monitoring routes."""

from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from backend.app.core.models import (
    ColabUrlResponse,
    SSHConfigResponse,
    SessionCreateRequest,
    SessionSummary,
)
from backend.app.core.security import get_current_admin
from backend.app.services import session_manager
from backend.app.services.colab_client import QuotaExceededError, TooManyAssignmentsError

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=List[SessionSummary])
async def get_sessions(
    status_filter: Optional[str] = Query(None, alias="status"),
    admin=Depends(get_current_admin),
):
    """List all Colab compute sessions with current status and deep links."""
    return session_manager.list_sessions(status_filter=status_filter)


@router.post("", response_model=SessionSummary, status_code=status.HTTP_201_CREATED)
async def create_session(
    req: SessionCreateRequest, admin=Depends(get_current_admin)
):
    """Provision a new Colab compute session."""
    try:
        session = session_manager.create_session(req)
        return session
    except QuotaExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Colab quota exceeded or unsupported accelerator: {e}",
        )
    except TooManyAssignmentsError as e:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=f"Too many active assignments on Google account: {e}",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session provisioning failed: {e}",
        )


@router.get("/{session_id}", response_model=SessionSummary)
async def get_session(session_id: str, admin=Depends(get_current_admin)):
    """Retrieve details for a specific session."""
    sess = session_manager.get_session(session_id)
    if not sess:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )
    return sess


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def stop_session(session_id: str, admin=Depends(get_current_admin)):
    """Terminate and unassign a running Colab compute session."""
    try:
        session_manager.stop_session(session_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop session: {e}",
        )


@router.get("/{session_id}/colab-url", response_model=ColabUrlResponse)
async def get_colab_url(session_id: str, admin=Depends(get_current_admin)):
    """Get the direct Colab web notebook deep link URL."""
    try:
        url = session_manager.get_session_colab_url(session_id)
        return ColabUrlResponse(url=url)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get("/{session_id}/ssh-config", response_model=SSHConfigResponse)
async def get_session_ssh_config(session_id: str, admin=Depends(get_current_admin)):
    """Get SSH connection parameters and VS Code Remote-SSH configuration snippet."""
    try:
        cfg = session_manager.get_session_ssh_config(session_id)
        return SSHConfigResponse(**cfg)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
