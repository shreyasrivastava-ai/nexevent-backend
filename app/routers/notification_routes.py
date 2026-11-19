"""
In-app notification read API (mounted at /api/notifications).

Endpoints:
  GET    /api/notifications                   list inbox (paginated)
  GET    /api/notifications/unread-count      badge count
  PATCH  /api/notifications/{id}/read         mark one as read/unread
  POST   /api/notifications/mark-all-read     bulk mark-as-read
  DELETE /api/notifications/{id}              dismiss one
"""

from __future__ import annotations

from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_active_user
from app.database import notifications_collection
from app.models.base import to_object_id, utc_now
from app.models.notification import (
    NotificationBulkMarkRead,
    NotificationMarkRead,
    NotificationResponse,
)
from app.models.user import UserInDB

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


# --------------------------------------------------------------------------- #
# GET /api/notifications
# --------------------------------------------------------------------------- #
@router.get("", response_model=List[NotificationResponse])
async def list_notifications(
    current_user: Annotated[UserInDB, Depends(get_current_active_user)],
    unread_only: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Return the caller's notification inbox, newest first."""
    query: dict = {"userId": current_user.id}
    if unread_only:
        query["isRead"] = False

    cursor = (
        notifications_collection()
        .find(query)
        .sort("createdAt", -1)
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return [NotificationResponse.model_validate(d) for d in docs]


# --------------------------------------------------------------------------- #
# GET /api/notifications/unread-count
# --------------------------------------------------------------------------- #
@router.get("/unread-count")
async def unread_count(
    current_user: Annotated[UserInDB, Depends(get_current_active_user)],
) -> dict:
    """Return `{ "count": N }` for the frontend bell badge."""
    count = await notifications_collection().count_documents(
        {"userId": current_user.id, "isRead": False}
    )
    return {"count": count}


# --------------------------------------------------------------------------- #
# PATCH /api/notifications/{id}/read
# --------------------------------------------------------------------------- #
@router.patch("/{notification_id}/read", response_model=NotificationResponse)
async def mark_read(
    notification_id: str,
    payload: NotificationMarkRead,
    current_user: Annotated[UserInDB, Depends(get_current_active_user)],
):
    """Toggle a notification's read state. Only the owner can do this."""
    try:
        oid = to_object_id(notification_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid notification ID.")

    new_state = bool(payload.isRead)
    update_fields: dict = {"isRead": new_state}
    update_fields["readAt"] = utc_now() if new_state else None

    result = await notifications_collection().find_one_and_update(
        {"_id": oid, "userId": current_user.id},
        {"$set": update_fields},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return NotificationResponse.model_validate(result)


# --------------------------------------------------------------------------- #
# POST /api/notifications/mark-all-read
# --------------------------------------------------------------------------- #
@router.post("/mark-all-read")
async def mark_all_read(
    payload: NotificationBulkMarkRead,
    current_user: Annotated[UserInDB, Depends(get_current_active_user)],
) -> dict:
    """
    Bulk mark-as-read.

    * `markAll: true`             → marks every unread notification as read.
    * `notificationIds: [id, ...]`→ marks only the listed ones.
    """
    query: dict = {
        "userId": current_user.id,
        "isRead": False,
    }
    if not payload.markAll:
        try:
            oids = [to_object_id(i) for i in payload.notificationIds]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        query["_id"] = {"$in": oids}

    result = await notifications_collection().update_many(
        query,
        {"$set": {"isRead": True, "readAt": utc_now()}},
    )
    return {"modified": result.modified_count}


# --------------------------------------------------------------------------- #
# DELETE /api/notifications/{id}
# --------------------------------------------------------------------------- #
@router.delete("/{notification_id}", status_code=status.HTTP_200_OK)
async def delete_notification(
    notification_id: str,
    current_user: Annotated[UserInDB, Depends(get_current_active_user)],
) -> dict:
    """Dismiss one of the caller's notifications."""
    try:
        oid = to_object_id(notification_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid notification ID.")

    result = await notifications_collection().delete_one(
        {"_id": oid, "userId": current_user.id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"message": "Notification deleted."}


__all__ = ["router"]