from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, status
from app.services.cloudinary_service import upload_media
from app.middleware.auth import require_roles
from app.models.enums import Role

router = APIRouter(prefix="/api/media", tags=["Media"])

# 5MB hard cap on the backend to prevent memory exhaustion
MAX_FILE_SIZE = 5 * 1024 * 1024

@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    folder: str = Form("nexevent"),
    _user=Depends(require_roles(Role.SUPER_DEAN, Role.FACULTY, Role.CLUB_LEAD)),
):
    """Upload a banner or brochure. Returns the secure Cloudinary URL."""
    
    # Check file size without loading the whole file into memory
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds the maximum allowed size of 5MB."
        )

    return await upload_media(file, folder=folder)