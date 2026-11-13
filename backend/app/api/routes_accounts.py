"""Google Account management and headless OAuth2 routes."""

from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, HTTPException, Response, status

from backend.app.core.models import (
    AccountSummary,
    OAuthCompleteRequest,
    OAuthStartResponse,
)
from backend.app.core.security import get_current_admin
from backend.app.services import account_pool

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=List[AccountSummary])
async def get_accounts(admin=Depends(get_current_admin)):
    """List all registered Google accounts with CCU metrics (credentials redacted)."""
    return account_pool.list_accounts()


@router.post("/oauth/start", response_model=OAuthStartResponse)
async def start_oauth(admin=Depends(get_current_admin)):
    """Generate remote Google OAuth authorization URL."""
    auth_url, state = account_pool.start_oauth_flow()
    return OAuthStartResponse(auth_url=auth_url, state=state)


@router.post("/oauth/complete", response_model=AccountSummary)
async def complete_oauth(req: OAuthCompleteRequest, admin=Depends(get_current_admin)):
    """Exchange authorization code and register Google account."""
    try:
        account = account_pool.exchange_code(req.code, state=req.state)
        return account
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/{account_id}/sync", response_model=AccountSummary)
async def sync_account_metrics(account_id: str, admin=Depends(get_current_admin)):
    """Force manual synchronization of CCU quota and orphan assignments."""
    try:
        updated = account_pool.sync_account(account_id)
        return updated
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to sync with Google Colab: {e}",
        )


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: str, admin=Depends(get_current_admin)):
    """Delete account and cascade delete all associated active sessions."""
    success = account_pool.delete_account(account_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {account_id} not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
