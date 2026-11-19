"""
Event schemas for NexEvent.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import (
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.models.base import (
    MongoBaseModel,
    PyObjectId,
    ensure_utc,
    mongo_id_field,
    utc_now,
)
from app.models.enums import Club, Department, EventStatus, NAACCriterion


class EventCore(MongoBaseModel):
    """Fields an event author is allowed to control."""

    title: str = Field(..., min_length=3, max_length=200, examples=["HackNex 2026"])
    description: str = Field(..., min_length=10, max_length=5000)
    naacCriterion: NAACCriterion
    organizingClub: Optional[Club] = Field(default=None)
    targetDepartments: List[Department] = Field(default_factory=list)
    targetYears: List[int] = Field(default_factory=list)
    targetCourses: List[str] = Field(default_factory=list)

    venueName: str = Field(..., min_length=2, max_length=120)
    startTime: datetime
    endTime: datetime
    capacity: Optional[int] = Field(default=None, ge=1, le=100_000)
    isRegistrationOpen: bool = True
    
    isCompulsory: bool = Field(default=False)
    requiresRegistration: bool = Field(default=False)
    registrationUrl: Optional[str] = Field(default=None, max_length=500)

    bannerUrl: Optional[str] = Field(default=None, max_length=500)
    brochureUrl: Optional[str] = Field(default=None, max_length=500)
    contactPerson: Optional[str] = Field(default=None, max_length=200)
    guestList: List[str] = Field(default_factory=list)
    rules: Optional[str] = Field(default=None, max_length=5000)

    facultyIncharge: List[str] = Field(default_factory=list)
    clubLeads: List[str] = Field(default_factory=list)
    studentCoordinators: List[str] = Field(default_factory=list)

    @field_validator("startTime", "endTime", mode="after")
    @classmethod
    def _normalise_to_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("targetYears")
    @classmethod
    def _validate_target_years(cls, value: List[int]) -> List[int]:
        invalid = [year for year in value if year not in (1, 2, 3, 4)]
        if invalid:
            raise ValueError(f"`targetYears` may only contain 1-4. Received: {invalid}.")
        return sorted(set(value))

    @field_validator("targetDepartments")
    @classmethod
    def _dedupe_departments(cls, value: List[Department]) -> List[Department]:
        seen: set[Department] = set()
        ordered: List[Department] = []
        for item in value:
            department = Department(item)
            if department not in seen:
                seen.add(department)
                ordered.append(department)
        return ordered

    @model_validator(mode="after")
    def _validate_time_window(self) -> "EventCore":
        if self.endTime <= self.startTime:
            raise ValueError("`endTime` must be strictly after `startTime`.")
        return self


class EventCreate(EventCore):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_registration_logic(self) -> "EventCreate":
        if self.isCompulsory:
            self.requiresRegistration = False
            self.registrationUrl = None
        elif self.requiresRegistration and not self.registrationUrl:
            raise ValueError("A registrationUrl is required for non-compulsory events that require RSVP.")
        elif not self.requiresRegistration:
            self.registrationUrl = None
        return self


class EventUpdate(MongoBaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=3, max_length=200)
    description: Optional[str] = Field(default=None, min_length=10, max_length=5000)
    naacCriterion: Optional[NAACCriterion] = None
    organizingClub: Optional[Club] = None
    targetDepartments: Optional[List[Department]] = None
    targetYears: Optional[List[int]] = None
    targetCourses: Optional[List[str]] = None
    venueName: Optional[str] = Field(default=None, min_length=2, max_length=120)
    startTime: Optional[datetime] = None
    endTime: Optional[datetime] = None
    capacity: Optional[int] = Field(default=None, ge=1, le=100_000)
    isRegistrationOpen: Optional[bool] = None
    isCompulsory: Optional[bool] = None
    requiresRegistration: Optional[bool] = None
    registrationUrl: Optional[str] = Field(default=None, max_length=500)

    bannerUrl: Optional[str] = Field(default=None, max_length=500)
    brochureUrl: Optional[str] = Field(default=None, max_length=500)
    contactPerson: Optional[str] = Field(default=None, max_length=200)
    guestList: Optional[List[str]] = None
    rules: Optional[str] = Field(default=None, max_length=5000)
    
    facultyIncharge: Optional[List[str]] = None
    clubLeads: Optional[List[str]] = None
    studentCoordinators: Optional[List[str]] = None


class EventApproval(MongoBaseModel):
    model_config = ConfigDict(extra="forbid")

    status: EventStatus
    remarks: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _validate_decision(self) -> "EventApproval":
        if self.status not in (EventStatus.APPROVED, EventStatus.REJECTED):
            raise ValueError("Approval decisions must be either APPROVED or REJECTED.")
        if self.status is EventStatus.REJECTED and not (self.remarks or "").strip():
            raise ValueError("`remarks` is required when rejecting.")
        return self


# --- NEW: Track Idempotency for Google Webhooks ---
class RegistrationSubSchema(MongoBaseModel):
    """Schema for tracking individual registrations with idempotency."""
    eventId: PyObjectId
    studentId: PyObjectId
    idempotency_key: Optional[str] = Field(
        default=None, 
        description="Google Form response_id to prevent duplicate RSVPs."
    )
    createdAt: datetime = Field(default_factory=utc_now)


class EventInDB(EventCore):
    status: EventStatus = EventStatus.DRAFT
    createdBy: PyObjectId
    approvedBy: Optional[PyObjectId] = None
    approvedAt: Optional[datetime] = None
    rejectionReason: Optional[str] = Field(default=None, max_length=1000)
    deletionReason: Optional[str] = Field(default=None, max_length=1000)
    completedAt: Optional[datetime] = None
    registeredStudents: List[PyObjectId] = Field(default_factory=list)
    coAuthorId: Optional[PyObjectId] = Field(default=None)
    
    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)

    @field_validator("approvedAt", "completedAt", mode="after")
    @classmethod
    def _normalise_optional_datetimes(cls, value: Optional[datetime]) -> Optional[datetime]:
        return ensure_utc(value) if value is not None else None

    @model_validator(mode="after")
    def _validate_status_consistency(self) -> "EventInDB":
        if self.status in (EventStatus.APPROVED, EventStatus.COMPLETED):
            if self.approvedBy is None:
                raise ValueError(f"`approvedBy` is required once an event reaches {self.status.value}.")
        if self.status is EventStatus.REJECTED and not self.rejectionReason:
            raise ValueError("`rejectionReason` is required for REJECTED events.")
        if self.status is EventStatus.COMPLETED and self.completedAt is None:
            self.completedAt = utc_now()
        return self


class EventResponse(EventInDB):
    model_config = ConfigDict(from_attributes=True)
    id: PyObjectId = mongo_id_field()
    isRegistered: bool = Field(default=False)

    @computed_field
    @property
    def registrationCount(self) -> int:
        return len(self.registeredStudents)

    @computed_field
    @property
    def seatsRemaining(self) -> Optional[int]:
        if self.capacity is None:
            return None
        return max(self.capacity - len(self.registeredStudents), 0)


__all__ = [
    "EventCore",
    "EventCreate",
    "EventUpdate",
    "EventApproval",
    "EventInDB",
    "EventResponse",
    "RegistrationSubSchema",
]