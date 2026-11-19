"""
Venue schemas — simplified to free-text recommendation tags.

The `venues` collection now acts as a dynamic dropdown source for the frontend.
Creators can type any venue name, and it will be auto-saved for future use.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from app.models.base import (
    MongoBaseModel,
    PyObjectId,
    mongo_id_field,
    utc_now,
)


class VenueBase(MongoBaseModel):
    """A simple venue recommendation tag."""

    name: str = Field(
        ...,
        min_length=2,
        max_length=120,
        examples=["Main Auditorium", "Seminar Hall A", "Open Air Theatre"],
    )


class VenueCreate(VenueBase):
    """Payload for POST /venues (admin-only)."""

    model_config = ConfigDict(extra="forbid")


class VenueUpdate(MongoBaseModel):
    """Partial update payload."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=2, max_length=120)


class VenueInDB(VenueBase):
    """Exactly what lives in the `venues` collection."""

    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)


class VenueResponse(VenueInDB):
    """Public representation returned to the React client."""

    model_config = ConfigDict(from_attributes=True)
    id: PyObjectId = mongo_id_field()


__all__ = ["VenueBase", "VenueCreate", "VenueUpdate", "VenueInDB", "VenueResponse"]