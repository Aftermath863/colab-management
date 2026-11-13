"""Cloud Storage Wizard API routes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel

from backend.app.core.models import (
    StorageProfileCreate,
    StorageProfileResponse,
    StorageProfileUpdate,
)
from backend.app.core.security import get_current_admin
from backend.app.services import storage_wizard

router = APIRouter(prefix="/api/storage", tags=["storage"])


class GDrivePropagateRequest(BaseModel):
    dryrun: bool = False


class MountStorageRequest(BaseModel):
    profile_id: str


@router.get("/profiles", response_model=List[StorageProfileResponse])
async def list_profiles(admin=Depends(get_current_admin)):
    """List all cloud storage profiles (sanitized)."""
    return storage_wizard.list_storage_profiles()


@router.post(
    "/profiles",
    response_model=StorageProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    req: StorageProfileCreate, admin=Depends(get_current_admin)
):
    """Create a new encrypted cloud storage profile."""
    try:
        return storage_wizard.create_storage_profile(req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create storage profile: {e}",
        )


@router.get("/profiles/{profile_id}", response_model=StorageProfileResponse)
async def get_profile(profile_id: str, admin=Depends(get_current_admin)):
    """Retrieve metadata for a specific storage profile."""
    prof = storage_wizard.get_storage_profile(profile_id)
    if not prof:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage profile {profile_id} not found",
        )
    return prof


@router.put("/profiles/{profile_id}", response_model=StorageProfileResponse)
async def update_profile(
    profile_id: str, req: StorageProfileUpdate, admin=Depends(get_current_admin)
):
    """Update an existing cloud storage profile."""
    try:
        return storage_wizard.update_storage_profile(profile_id, req)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update storage profile: {e}",
        )


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: str, admin=Depends(get_current_admin)):
    """Delete a cloud storage profile."""
    deleted = storage_wizard.delete_storage_profile(profile_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage profile {profile_id} not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/gdrive/propagate/{session_id}")
async def propagate_gdrive(
    session_id: str,
    req: Optional[GDrivePropagateRequest] = None,
    admin=Depends(get_current_admin),
):
    """Execute official Google Drive credentials propagation on session runtime."""
    dryrun = req.dryrun if req else False
    try:
        return storage_wizard.propagate_gdrive_credentials(session_id, dryrun=dryrun)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google Drive credential propagation failed: {e}",
        )


@router.post("/mount/{session_id}")
async def mount_storage(
    session_id: str,
    req: MountStorageRequest,
    admin=Depends(get_current_admin),
):
    """Mount cloud storage profile (GDrive, S3/R2 rclone, HF token) onto session."""
    try:
        res = storage_wizard.mount_profile_on_session(session_id, req.profile_id)
        if not res.get("success"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=res.get("error", "Storage mount failed"),
            )
        return res
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to mount storage: {e}",
        )


@router.get("/verify/{session_id}/{profile_id}")
async def verify_mount(
    session_id: str,
    profile_id: str,
    admin=Depends(get_current_admin),
):
    """Preflight check to verify if cloud storage mount path is accessible."""
    prof = storage_wizard.get_storage_profile(profile_id)
    if not prof:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Storage profile {profile_id} not found",
        )
    is_accessible = storage_wizard.verify_mount_accessibility(
        session_id, prof["mount_path"]
    )
    return {
        "session_id": session_id,
        "profile_id": profile_id,
        "mount_path": prof["mount_path"],
        "accessible": is_accessible,
    }
