import logging
from fastapi import UploadFile, HTTPException
import cloudinary
import cloudinary.uploader

from app.config import get_settings

logger = logging.getLogger("nexevent.cloudinary")
settings = get_settings()

if settings.CLOUDINARY_CLOUD_NAME:
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True
    )

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_BYTES = 10 * 1024 * 1024  # 10 MB

async def upload_media(file: UploadFile, folder: str = "nexevent") -> dict:
    if not settings.CLOUDINARY_CLOUD_NAME:
        raise HTTPException(500, "Cloudinary is not configured.")
        
    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(400, f"Unsupported file type: {file.content_type}")

    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "File exceeds 10 MB limit")

    resource_type = "raw" if file.content_type == "application/pdf" else "image"

    try:
        result = cloudinary.uploader.upload(
            content,
            folder=folder,
            resource_type=resource_type,
        )
        return {
            "secure_url": result["secure_url"],
            "public_id": result["public_id"],
        }
    except Exception as exc:
        logger.error("Cloudinary upload failed: %s", exc)
        raise HTTPException(502, "Media upload service unavailable") from exc