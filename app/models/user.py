"""
User schemas for NexEvent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.base import (
    MongoBaseModel,
    PyObjectId,
    mongo_id_field,
    utc_now,
)
from app.models.enums import (
    STAFF_ROLES,
    STUDENT_ROLES,
    Club,
    Department,
    Role,
)


# --------------------------------------------------------------------------- #
# Shared base + the rule engine
# --------------------------------------------------------------------------- #
class UserBase(MongoBaseModel):
    """Fields common to every user representation."""

    # --- Universal identity ------------------------------------------------ #
    name: str = Field(..., min_length=2, max_length=120, examples=["Aarav Sharma"])
    erp: str = Field(
        ...,
        min_length=4,
        max_length=32,
        description="Universal institutional ERP identifier. Required for EVERY role.",
        examples=["ERP2024CSE0192"],
    )
    role: Role

    # --- Academic placement ------------------------------------------------ #
    department: Department
    year: Optional[int] = Field(
        default=None,
        ge=1,
        le=4,
        description="Required for STUDENT, CLUB_LEAD, STUDENT_COORDINATOR only.",
    )
    rollNumber: Optional[int] = Field(
        default=None,
        gt=0,
        description="Numeric roll number. Required for students; forced None for staff.",
    )

    # --- Contact ----------------------------------------------------------- #
    email: Optional[EmailStr] = Field(
        default=None,
        description="Required for SUPER_DEAN / FACULTY; forced None for students.",
    )
    phone: Optional[str] = Field(
        default=None,
        max_length=20,
        pattern=r"^[0-9+\-()\s]{7,20}$",
    )

    # --- Organisational ---------------------------------------------------- #
    club: Optional[Club] = Field(
        default=None,
        description="Affiliated club. Required when role == CLUB_LEAD.",
    )
    designation: Optional[str] = Field(default=None, max_length=120)
    isActive: bool = True
    
    # --- Deputy Dean Support ----------------------------------------------- #
    adminExpiresAt: Optional[datetime] = Field(
        default=None,
        description="If set, the user is a temporary SUPER_DEAN until this timestamp.",
    )

    # --- Identity & Security ----------------------------------------------- #
    requires_password_change: bool = Field(
        default=False,
        description="Forces the user to change their password on first login.",
    )
    reset_password_token: Optional[str] = Field(
        default=None,
        description="Secure token for password reset.",
    )
    reset_password_expires: Optional[datetime] = Field(
        default=None,
        description="Expiration timestamp for the reset token.",
    )

    # ------------------------------------------------------------------ #
    # Normalisation validators
    # ------------------------------------------------------------------ #
    @field_validator("erp", mode="before")
    @classmethod
    def _normalise_erp(cls, value: Any) -> Any:
        """ERPs are stored upper-cased so lookups are case-insensitive."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("name", mode="before")
    @classmethod
    def _normalise_name(cls, value: Any) -> Any:
        if isinstance(value, str):
            return " ".join(value.split())
        return value

    # ------------------------------------------------------------------ #
    # THE rule engine
    # ------------------------------------------------------------------ #
    @model_validator(mode="after")
    def _enforce_role_based_identity_rules(self) -> "UserBase":
        # Normalise in case the value arrived as a raw string from Mongo.
        role = Role(self.role)

        # ---------- Rule 2: ERP is UNIVERSAL ---------- #
        if not self.erp or not self.erp.strip():
            raise ValueError("`erp` is universally required for every user, regardless of role.")

        if role in STUDENT_ROLES:
            # ---------- Rule 3: year is mandatory ---------- #
            if self.year is None:
                raise ValueError(f"`year` (1-4) is required for role {role.value}.")

            # ---------- Rule 4: rollNumber required, email forbidden ---------- #
            if self.rollNumber is None:
                raise ValueError(f"`rollNumber` is required for role {role.value}.")
            self.email = None  # force — never trust the client payload

            # Extra integrity: a club lead must be tied to exactly one club.
            if role is Role.CLUB_LEAD and self.club is None:
                raise ValueError("`club` is required for role CLUB_LEAD.")

        elif role in STAFF_ROLES:
            # ---------- Rule 4: email required, rollNumber/year forbidden ---- #
            if self.email is None:
                raise ValueError(f"`email` is required for role {role.value}.")
            self.rollNumber = None
            self.year = None

        else:  # pragma: no cover - exhaustive guard for future enum additions
            raise ValueError(f"Unsupported role: {role!r}.")

        return self


# --------------------------------------------------------------------------- #
# Write models
# --------------------------------------------------------------------------- #
class UserCreate(UserBase):
    """Payload for POST /users (admin-provisioned or self-signup)."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(..., min_length=8, max_length=128, repr=False)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        if not any(char.isalpha() for char in value):
            raise ValueError("Password must contain at least one letter.")
        if not any(char.isdigit() for char in value):
            raise ValueError("Password must contain at least one digit.")
        return value


class UserUpdate(MongoBaseModel):
    """
    Partial update payload.
    """
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    department: Optional[Department] = None
    year: Optional[int] = Field(default=None, ge=1, le=4)
    club: Optional[Club] = None
    designation: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=20)
    isActive: Optional[bool] = None


# --------------------------------------------------------------------------- #
# Persistence model
# --------------------------------------------------------------------------- #
class UserInDB(UserBase):
    """Exactly what lives in the `users` collection."""

    hashedPassword: str = Field(..., repr=False)
    createdBy: Optional[PyObjectId] = Field(
        default=None,
        description="ObjectId of the SUPER_DEAN / FACULTY who provisioned this user.",
    )
    lastLoginAt: Optional[datetime] = None
    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)


# --------------------------------------------------------------------------- #
# Read model
# --------------------------------------------------------------------------- #
class UserResponse(UserBase):
    """Public representation — never leaks `hashedPassword`."""

    model_config = ConfigDict(from_attributes=True)

    id: PyObjectId = mongo_id_field()
    createdAt: datetime
    updatedAt: datetime


__all__ = ["UserBase", "UserCreate", "UserUpdate", "UserInDB", "UserResponse"]