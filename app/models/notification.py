"""
In-app notification schemas.

Notifications are the platform's single inbox channel — approval decisions,
registration confirmations, reminders, co-author grants, and broadcast
announcements all funnel through this collection.

BSON SAFETY
-----------
MongoDB's driver cannot serialize Python Enum instances directly. The
`use_enum_values=True` flag on every model in this module forces Pydantic to
store the *underlying value* (a plain `str` for our `str`-based enums) rather
than the enum member. `NotificationCreate.to_mongo()` goes one step further
and guarantees every field is a BSON-safe primitive.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.base import (
    MongoBaseModel,
    PyObjectId,
    ensure_utc,
    mongo_id_field,
    utc_now,
)


# --------------------------------------------------------------------------- #
# Notification type
# --------------------------------------------------------------------------- #
class NotificationType(str, Enum):
    """Drives iconography and deep-linking in the frontend."""

    GENERAL = "GENERAL"
    EVENT_SUBMITTED = "EVENT_SUBMITTED"
    EVENT_APPROVED = "EVENT_APPROVED"
    EVENT_REJECTED = "EVENT_REJECTED"
    EVENT_REMINDER = "EVENT_REMINDER"
    REGISTRATION_CONFIRMED = "REGISTRATION_CONFIRMED"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    CO_AUTHOR_ASSIGNED = "CO_AUTHOR_ASSIGNED"
    CO_AUTHOR_REVOKED = "CO_AUTHOR_REVOKED"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# --------------------------------------------------------------------------- #
# Base
# --------------------------------------------------------------------------- #
class NotificationBase(MongoBaseModel):
    """
    Fields shared by every notification representation.

    `use_enum_values=True` is CRITICAL — without it, Pydantic keeps the
    `NotificationType` enum instance in the model, and `model_dump()` would
    hand PyMongo an un-serializable object. With it, `type` is stored and
    dumped as a plain `str`.
    """

    model_config = ConfigDict(
        use_enum_values=True,
        populate_by_name=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
        extra="ignore",
        validate_assignment=False,
    )

    userId: PyObjectId = Field(..., description="Recipient user ObjectId.")

    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=2000)
    type: NotificationType = NotificationType.GENERAL

    isRead: bool = False
    readAt: Optional[datetime] = None

    relatedEventId: Optional[PyObjectId] = Field(
        default=None,
        description="Event this notification refers to, when applicable.",
    )
    actionUrl: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Relative frontend route, e.g. `/events/665f.../details`.",
    )

    createdAt: datetime = Field(default_factory=utc_now)

    # ------------------------------------------------------------------ #
    # Validators
    # ------------------------------------------------------------------ #
    @field_validator("readAt", "createdAt", mode="after")
    @classmethod
    def _ensure_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return ensure_utc(value) if value is not None else None

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, value: Any) -> Any:
        """Accept a raw string from Mongo and coerce it to the enum."""
        if isinstance(value, NotificationType):
            return value
        if isinstance(value, str):
            try:
                return NotificationType(value)
            except ValueError:
                return NotificationType.GENERAL
        return value

    @model_validator(mode="after")
    def _keep_read_state_consistent(self) -> "NotificationBase":
        """
        `isRead` is the single source of truth; `readAt` mirrors it.

        Uses `object.__setattr__` so the mutation is guaranteed to land in
        the instance dict regardless of Pydantic's internal bookkeeping.
        """
        if self.isRead:
            if self.readAt is None:
                object.__setattr__(self, "readAt", utc_now())
        else:
            object.__setattr__(self, "readAt", None)
        return self

    # ------------------------------------------------------------------ #
    # BSON safety net
    # ------------------------------------------------------------------ #
    def to_mongo(self) -> dict:
        """
        Return a guaranteed BSON-safe dict for MongoDB insertion.

        Rules:
          * Drop `id` — MongoDB generates `_id` automatically.
          * Convert enum instances to their `.value`.
          * Convert datetime to tz-aware UTC datetime (BSON native).
          * Drop `None` values so sparse indexes and defaults work correctly.
        """
        raw = self.model_dump(by_alias=True, exclude_none=True)
        raw.pop("id", None)
        raw.pop("_id", None)

        cleaned: dict = {}
        for key, value in raw.items():
            if isinstance(value, Enum):
                cleaned[key] = value.value
            elif isinstance(value, datetime):
                cleaned[key] = ensure_utc(value)
            else:
                cleaned[key] = value
        return cleaned


# --------------------------------------------------------------------------- #
# Write model
# --------------------------------------------------------------------------- #
class NotificationCreate(NotificationBase):
    """Payload used internally by services to enqueue a notification."""

    model_config = ConfigDict(
        use_enum_values=True,
        extra="forbid",
        populate_by_name=True,
    )


# --------------------------------------------------------------------------- #
# Persistence model
# --------------------------------------------------------------------------- #
class NotificationInDB(NotificationBase):
    """Exactly what lives in the `notifications` collection."""

    pass


# --------------------------------------------------------------------------- #
# Read model
# --------------------------------------------------------------------------- #
class NotificationResponse(NotificationInDB):
    """Public representation returned to the React client."""

    model_config = ConfigDict(
        use_enum_values=True,
        from_attributes=True,
        populate_by_name=True,
    )

    id: PyObjectId = mongo_id_field()


# --------------------------------------------------------------------------- #
# Update models
# --------------------------------------------------------------------------- #
class NotificationMarkRead(MongoBaseModel):
    """Payload for PATCH /api/notifications/{id}/read."""

    model_config = ConfigDict(extra="forbid")

    isRead: bool = True


class NotificationBulkMarkRead(MongoBaseModel):
    """Payload for POST /api/notifications/mark-read (bulk inbox action)."""

    model_config = ConfigDict(extra="forbid")

    notificationIds: List[PyObjectId] = Field(default_factory=list)
    markAll: bool = False

    @model_validator(mode="after")
    def _require_a_target(self) -> "NotificationBulkMarkRead":
        if not self.markAll and not self.notificationIds:
            raise ValueError("Provide `notificationIds` or set `markAll = true`.")
        return self


__all__ = [
    "NotificationType",
    "NotificationBase",
    "NotificationCreate",
    "NotificationInDB",
    "NotificationResponse",
    "NotificationMarkRead",
    "NotificationBulkMarkRead",
]