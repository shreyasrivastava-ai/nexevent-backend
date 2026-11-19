"""
Shared Pydantic v2 primitives for every MongoDB-backed NexEvent model.

Key design decisions
--------------------
* **camelCase everywhere.** Field names are literally camelCase (`rollNumber`,
  `startTime`, `isRead`, ...) so the JSON payload returned by FastAPI drops
  straight into the React frontend without a transformation layer.
* **`_id` handling.** Mongo documents are validated with `_id` but always
  serialised as `id`, so the frontend never sees the underscore.
* **ObjectIds are represented as `str` in the Python layer.** This keeps the
  API surface JSON-native. The `to_object_id()` helper is provided for
  repositories that prefer to persist native BSON ObjectIds.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
)


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def utc_now() -> datetime:
    """Timezone-aware UTC timestamp — the ONLY clock used across the codebase."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Coerce a naive datetime to UTC and normalise aware datetimes to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# ObjectId plumbing
# --------------------------------------------------------------------------- #
def _coerce_object_id(value: Any) -> Any:
    """Accept a BSON ObjectId or a 24-char hex string; normalise to `str`."""
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, str):
        candidate = value.strip()
        if ObjectId.is_valid(candidate):
            return candidate
        raise ValueError(f"{value!r} is not a valid 24-character ObjectId.")
    raise ValueError(f"Cannot interpret {type(value).__name__} as an ObjectId.")


#: A validated, JSON-friendly ObjectId string.
PyObjectId = Annotated[
    str,
    BeforeValidator(_coerce_object_id),
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]


def to_object_id(value: str | ObjectId) -> ObjectId:
    """
    Convert a validated id string to a native BSON ObjectId.

    Use this in repository methods when you want references stored as real
    ObjectIds rather than strings.
    """
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"{value!r} is not a valid ObjectId.") from exc


def mongo_id_field(default: Any = ...) -> Any:
    """
    Build the canonical `id` FieldInfo.

    * validates from either `_id` (raw Mongo document) or `id` (API payload),
    * serialises as `id` for the frontend,
    * required when `default` is omitted.
    """
    return Field(
        default=default,
        validation_alias=AliasChoices("_id", "id"),
        serialization_alias="id",
    )


# --------------------------------------------------------------------------- #
# Base models
# --------------------------------------------------------------------------- #
class MongoBaseModel(BaseModel):
    """Root base class for every document-backed schema."""

    model_config = ConfigDict(
        # Allow constructing with the python field name as well as the alias.
        populate_by_name=True,
        arbitrary_types_allowed=True,
        # Trim stray whitespace from every incoming string.
        str_strip_whitespace=True,
        # Silently drop unknown keys when hydrating from legacy Mongo documents.
        extra="ignore",
        # Never re-run validators on attribute assignment (mutating validators
        # below rely on this to avoid recursion).
        validate_assignment=False,
        # Emit ISO-8601 UTC strings with a trailing `Z`-compatible offset.
        ser_json_timedelta="float",
        json_encoders={datetime: lambda v: ensure_utc(v).isoformat()},
    )

    id: Optional[PyObjectId] = mongo_id_field(default=None)


class TimestampedMongoModel(MongoBaseModel):
    """Adds server-managed audit timestamps."""

    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)