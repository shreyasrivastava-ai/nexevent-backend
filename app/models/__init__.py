"""Public schema surface for NexEvent."""

from app.models.base import (
    MongoBaseModel,
    PyObjectId,
    TimestampedMongoModel,
    ensure_utc,
    mongo_id_field,
    to_object_id,
    utc_now,
)
from app.models.enums import (
    APPROVER_ROLES,
    EVENT_CREATOR_ROLES,
    STAFF_ROLES,
    STUDENT_ROLES,
    Club,
    Department,
    EventStatus,
    NAACCriterion,
    Role,
)
from app.models.event import (
    EventApproval,
    EventCore,
    EventCreate,
    EventInDB,
    EventResponse,
    EventUpdate,
)
from app.models.notification import (
    NotificationBase,
    NotificationBulkMarkRead,
    NotificationCreate,
    NotificationInDB,
    NotificationMarkRead,
    NotificationResponse,
    NotificationType,
)
from app.models.user import UserBase, UserCreate, UserInDB, UserResponse, UserUpdate
from app.models.venue import VenueBase, VenueCreate, VenueInDB, VenueResponse, VenueUpdate

__all__ = [
    # base
    "MongoBaseModel",
    "TimestampedMongoModel",
    "PyObjectId",
    "mongo_id_field",
    "to_object_id",
    "utc_now",
    "ensure_utc",
    # enums
    "Role",
    "Department",
    "Club",
    "EventStatus",
    "NAACCriterion",
    "STUDENT_ROLES",
    "STAFF_ROLES",
    "EVENT_CREATOR_ROLES",
    "APPROVER_ROLES",
    # user
    "UserBase",
    "UserCreate",
    "UserUpdate",
    "UserInDB",
    "UserResponse",
    # event
    "EventCore",
    "EventCreate",
    "EventUpdate",
    "EventApproval",
    "EventInDB",
    "EventResponse",
    # venue
    "VenueBase",
    "VenueCreate",
    "VenueUpdate",
    "VenueInDB",
    "VenueResponse",
    # notification
    "NotificationType",
    "NotificationBase",
    "NotificationCreate",
    "NotificationInDB",
    "NotificationResponse",
    "NotificationMarkRead",
    "NotificationBulkMarkRead",
]