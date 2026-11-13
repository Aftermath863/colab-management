"""Environment Templates and Secrets Vault API routes."""

from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Response, status

from backend.app.core.models import (
    SecretCreate,
    SecretResponse,
    SecretUpdate,
    TemplateCreate,
    TemplateResponse,
    TemplateUpdate,
)
from backend.app.core.security import get_current_admin
from backend.app.services import template_service

router = APIRouter(tags=["templates", "secrets"])


# ---------------------------------------------------------------------------
# Templates API
# ---------------------------------------------------------------------------

@router.get("/api/templates", response_model=List[TemplateResponse])
async def list_templates(admin=Depends(get_current_admin)):
    """Enumerate all environment templates."""
    return template_service.list_templates()


@router.post(
    "/api/templates",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_template(req: TemplateCreate, admin=Depends(get_current_admin)):
    """Create a new environment template."""
    try:
        return template_service.create_template(req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create template: {e}",
        )


@router.get("/api/templates/{template_id}", response_model=TemplateResponse)
async def get_template(template_id: str, admin=Depends(get_current_admin)):
    """Retrieve details for a specific environment template."""
    tpl = template_service.get_template(template_id)
    if not tpl:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template {template_id} not found",
        )
    return tpl


@router.put("/api/templates/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: str, req: TemplateUpdate, admin=Depends(get_current_admin)
):
    """Update an existing environment template."""
    try:
        return template_service.update_template(template_id, req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update template: {e}",
        )


@router.delete(
    "/api/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_template(template_id: str, admin=Depends(get_current_admin)):
    """Delete an environment template."""
    deleted = template_service.delete_template(template_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template {template_id} not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Secrets Vault API
# ---------------------------------------------------------------------------

@router.get("/api/secrets", response_model=List[SecretResponse])
async def list_secrets(admin=Depends(get_current_admin)):
    """Enumerate stored secrets (values omitted)."""
    return template_service.list_secrets()


@router.post(
    "/api/secrets",
    response_model=SecretResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_secret(req: SecretCreate, admin=Depends(get_current_admin)):
    """Encrypt and store a secret in the vault."""
    try:
        return template_service.create_secret(req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store secret: {e}",
        )


@router.get("/api/secrets/{secret_id}", response_model=SecretResponse)
async def get_secret(secret_id: str, admin=Depends(get_current_admin)):
    """Retrieve secret metadata (value omitted)."""
    sec = template_service.get_secret(secret_id)
    if not sec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Secret {secret_id} not found",
        )
    return sec


@router.put("/api/secrets/{secret_id}", response_model=SecretResponse)
async def update_secret(
    secret_id: str, req: SecretUpdate, admin=Depends(get_current_admin)
):
    """Update a secret's value or description."""
    try:
        return template_service.update_secret(secret_id, req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update secret: {e}",
        )


@router.delete("/api/secrets/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(secret_id: str, admin=Depends(get_current_admin)):
    """Delete a secret from the vault."""
    deleted = template_service.delete_secret(secret_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Secret {secret_id} not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
