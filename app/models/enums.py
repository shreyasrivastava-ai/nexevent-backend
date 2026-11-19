"""
Canonical enumerations for the NexEvent domain.

Every enum subclasses `(str, Enum)` so that:
  * values serialise to plain strings in JSON and BSON,
  * FastAPI/OpenAPI emits clean string schemas,
  * membership checks work against raw strings coming from the DB.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """Base class giving a readable `__str__` and safe `_missing_` handling."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @classmethod
    def _missing_(cls, value):  # type: ignore[override]
        """Allow case-insensitive / whitespace-padded lookups from the DB."""
        if isinstance(value, str):
            normalised = value.strip()
            for member in cls:
                if member.value.lower() == normalised.lower():
                    return member
        return None


# --------------------------------------------------------------------------- #
# Role
# --------------------------------------------------------------------------- #
class Role(StrEnum):
    SUPER_DEAN = "SUPER_DEAN"
    FACULTY = "FACULTY"
    CLUB_LEAD = "CLUB_LEAD"
    STUDENT_COORDINATOR = "STUDENT_COORDINATOR"
    STUDENT = "STUDENT"


# --------------------------------------------------------------------------- #
# Department  (exact display strings — do not "tidy" the punctuation)
# --------------------------------------------------------------------------- #
class Department(StrEnum):
    BSC_HONS_AGRICULTURE = "B.Sc(Hons.) Agriculture"
    CSE_CORE = "CSE (Core)"
    CSE_AI_ML = "CSE (AI + ML)"
    CSE_DS_AI = "CSE (DS + AI)"
    CSE_CYBER_SECURITY = "CSE (Cyber Security)"
    BBA = "BBA"
    ELECTRICAL_ENGG = "ELECTRICAL ENGG."
    ME = "ME"
    CIVIL_ENGG = "CIVIL ENGG"
    CHEMICAL_ENGG = "CHEMICAL ENGG"
    IT = "IT"
    LLB = "LLB"
    BCOM_HONS = "B.COM(HONS.)"


# --------------------------------------------------------------------------- #
# Club
# --------------------------------------------------------------------------- #
class Club(StrEnum):
    TECH_FUSION = "Tech Fusion Club"
    POWER_ANGELS_1090 = "1090 Power Angels"
    APNI_PATHSALA = "Apni Pathsala Club"
    BEAT_THE_BEATS = "Beat the Beats"
    BUSINESS_CONSULTANCY = "Business Consultancy Club"
    EXPRESSION = "Expression Club"
    GOLDEN_PEN = "Golden Pen Club"
    HEALTH = "Health Club"
    MANCH = "Manch Club"
    NSS = "NSS Club"
    OZONE = "Ozone Club"
    POSITIVE_PSYCHOLOGY = "Positive Psychology Club"
    RAZZMATAZZ = "Razzmatazz Club"
    SNAPSHOT = "Snapshot Club"
    SOCIAL_MEDIA = "Social Media Club"
    SPIC_MACAY = "Spic Macay Club"
    SRIJAN = "Srijan Club"
    STOCK_EXCHANGE = "Stock Exchange Club"
    SUR_JHANKAR = "Sur Jhankar Club"


# --------------------------------------------------------------------------- #
# Event status
# --------------------------------------------------------------------------- #
class EventStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"


# --------------------------------------------------------------------------- #
# NAAC criterion
# --------------------------------------------------------------------------- #
class NAACCriterion(StrEnum):
    CRITERION_I = "Criterion I: Curricular Aspects"
    CRITERION_II = "Criterion II: Teaching-Learning & Evaluation"
    CRITERION_III = "Criterion III: Research, Innovations & Extension"
    CRITERION_V = "Criterion V: Student Support & Progression"
    GENDER_EQUITY = "Gender Equity"
    PROFESSIONAL_DEVELOPMENT = "Professional Development"
    ENVIRONMENTAL_CONSCIOUSNESS = "Environmental Consciousness"


# --------------------------------------------------------------------------- #
# Derived role groupings — imported by the user schemas & authorisation guards
# --------------------------------------------------------------------------- #
STUDENT_ROLES: frozenset[Role] = frozenset(
    {Role.STUDENT, Role.CLUB_LEAD, Role.STUDENT_COORDINATOR}
)

STAFF_ROLES: frozenset[Role] = frozenset({Role.SUPER_DEAN, Role.FACULTY})

#: Roles permitted to create / submit events.
EVENT_CREATOR_ROLES: frozenset[Role] = frozenset(
    {Role.SUPER_DEAN, Role.FACULTY, Role.CLUB_LEAD, Role.STUDENT_COORDINATOR}
)

#: Roles permitted to approve or reject events.
APPROVER_ROLES: frozenset[Role] = frozenset({Role.SUPER_DEAN, Role.FACULTY})


__all__ = [
    "StrEnum",
    "Role",
    "Department",
    "Club",
    "EventStatus",
    "NAACCriterion",
    "STUDENT_ROLES",
    "STAFF_ROLES",
    "EVENT_CREATOR_ROLES",
    "APPROVER_ROLES",
]