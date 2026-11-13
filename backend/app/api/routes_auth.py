"""Authentication and admin session verification routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from backend.app.config import settings
from backend.app.core.models import LoginRequest, LoginResponse, UserStatusResponse
from backend.app.core.security import (
    create_access_token,
    get_current_admin,
    verify_admin_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """Authenticate administrator and issue signed JWT access token."""
    if not verify_admin_password(req.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect admin password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token({"sub": "admin", "role": "admin"})
    return LoginResponse(token=token, token_type="bearer")


@router.get("/me", response_model=UserStatusResponse)
async def get_current_user_status(admin=Depends(get_current_admin)):
    """Verify active JWT token and admin permissions."""
    return UserStatusResponse(
        authenticated=True,
        role="admin",
        is_default_password=(settings.ADMIN_PASSWORD == "changeme"),
    )
