"""
Institutional delegation hierarchy routes.
"""

from __future__ import annotations

from enum import Enum
from datetime import timedelta
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.api.deps import get_current_active_user
from app.database import users_collection, notifications_collection
from app.middleware.auth import hash_password, verify_password, require_roles
from app.models.base import utc_now, to_object_id  # <-- ADDED to_object_id
from app.models.enums import Club, Department, Role
from app.models.user import UserCreate, UserInDB, UserResponse
from app.models.notification import NotificationCreate, NotificationType

router = APIRouter(prefix="/api/users", tags=["User Management"])


# --------------------------------------------------------------------------- #
# Request schemas
# --------------------------------------------------------------------------- #
class StaffCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    erp: str = Field(..., min_length=4, max_length=32)
    department: Department
    role: Role
    password: str = Field(..., min_length=8, max_length=128)
    designation: Optional[str] = Field(default=None, max_length=120)

    @field_validator("role")
    @classmethod
    def _must_be_staff_role(cls, value: Role) -> Role:
        if value not in (Role.SUPER_DEAN, Role.FACULTY):
            raise ValueError("role must be SUPER_DEAN or FACULTY.")
        return value

    @field_validator("erp", mode="before")
    @classmethod
    def _normalise_erp(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


class AssignClubLeadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    club: Club


class AssignCoordinatorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    club: Optional[Club] = None


class MakeDeputyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expiryDays: int = Field(default=7, ge=1, le=30)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=8)
    new_password: str = Field(..., min_length=8)


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
async def _load_user_by_erp(erp: str) -> dict:
    user = await users_collection().find_one({"erp": erp.upper()})
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No user found with ERP '{erp}'.",
        )
    return user

async def _create_notification(user_id: str, title: str, message: str, n_type: NotificationType, action_url: str):
    try:
        notif = NotificationCreate(
            userId=user_id,
            title=title,
            message=message,
            type=n_type,
            actionUrl=action_url,
        )
        await notifications_collection().insert_one(notif.to_mongo())
    except Exception:
        pass


async def _apply_mutation(user_doc: dict, changes: dict) -> dict:
    merged = {**user_doc, **changes}

    try:
        validated = UserInDB.model_validate(merged)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid user state after mutation: {exc}",
        ) from exc

    update_data: dict = {}
    for key in changes.keys():
        if key == "_id":
            continue
        value = getattr(validated, key)
        if isinstance(value, Enum):
            value = value.value
        update_data[key] = value
    update_data["updatedAt"] = utc_now()

    result = await users_collection().find_one_and_update(
        {"_id": user_doc["_id"]},
        {"$set": update_data},
        return_document=True,
    )
    return result


# --------------------------------------------------------------------------- #
# 1. POST /api/users/staff   (SUPER_DEAN only)
# --------------------------------------------------------------------------- #
@router.post(
    "/staff",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a FACULTY or SUPER_DEAN account (SUPER_DEAN only)",
)
async def create_staff(
    payload: StaffCreateRequest,
    actor: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN))],
) -> UserResponse:
    if await users_collection().find_one({"erp": payload.erp}):
        raise HTTPException(status_code=400, detail=f"An account with ERP '{payload.erp}' already exists.")
    if await users_collection().find_one({"email": str(payload.email).lower()}):
        raise HTTPException(status_code=400, detail=f"An account with email '{payload.email}' already exists.")

    try:
        user_in = UserCreate(
            name=payload.name,
            erp=payload.erp,
            role=payload.role,
            department=payload.department,
            email=payload.email,
            password=payload.password,
            designation=payload.designation,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    doc = user_in.model_dump(by_alias=True, exclude={"password"})
    doc["hashedPassword"] = hash_password(user_in.password)
    doc["isActive"] = True
    doc["requires_password_change"] = True # Force newly created staff to change password
    doc["createdBy"] = actor.id
    now = utc_now()
    doc["createdAt"] = now
    doc["updatedAt"] = now

    result = await users_collection().insert_one(doc)
    created = await users_collection().find_one({"_id": result.inserted_id})
    if created is None:
        raise HTTPException(status_code=500, detail="Staff account was created but could not be reloaded.")
    return UserResponse.model_validate(created)


# --------------------------------------------------------------------------- #
# 2. PATCH /api/users/{erp}/assign-club-lead   (SUPER_DEAN, FACULTY)
# --------------------------------------------------------------------------- #
@router.patch(
    "/{erp}/assign-club-lead",
    response_model=UserResponse,
    summary="Promote a STUDENT to CLUB_LEAD and assign a club",
)
async def assign_club_lead(
    erp: str,
    payload: AssignClubLeadRequest,
    actor: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN, Role.FACULTY))],
) -> UserResponse:
    target = await _load_user_by_erp(erp)

    if target.get("role") != Role.STUDENT.value:
        raise HTTPException(
            status_code=400,
            detail=f"Only STUDENT accounts can be promoted to CLUB_LEAD. '{erp}' is currently {target.get('role')}.",
        )

    updated = await _apply_mutation(target, {"role": Role.CLUB_LEAD, "club": payload.club})
    
    await _create_notification(
        user_id=str(target["_id"]),
        title="Role Updated: Club Lead",
        message=f"You have been assigned as the Club Lead for {payload.club.value}.",
        n_type=NotificationType.ROLE_UPDATED,
        action_url="/faculty/dashboard"
    )
    
    return UserResponse.model_validate(updated)


# --------------------------------------------------------------------------- #
# 3. PATCH /api/users/{erp}/remove-club-lead   (SUPER_DEAN, FACULTY)
# --------------------------------------------------------------------------- #
@router.patch(
    "/{erp}/remove-club-lead",
    response_model=UserResponse,
    summary="Demote a CLUB_LEAD back to STUDENT",
)
async def remove_club_lead(
    erp: str,
    actor: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN, Role.FACULTY))],
) -> UserResponse:
    target = await _load_user_by_erp(erp)

    if target.get("role") != Role.CLUB_LEAD.value:
        raise HTTPException(
            status_code=400,
            detail=f"User '{erp}' is not a CLUB_LEAD (current role: {target.get('role')}).",
        )

    updated = await _apply_mutation(target, {"role": Role.STUDENT, "club": None})

    await _create_notification(
        user_id=str(target["_id"]),
        title="Role Updated: Student",
        message="Your Club Lead role has been removed. You are now a standard Student.",
        n_type=NotificationType.ROLE_UPDATED,
        action_url="/events/discover"
    )
    
    return UserResponse.model_validate(updated)


# --------------------------------------------------------------------------- #
# 4. PATCH /api/users/{erp}/assign-coordinator
# --------------------------------------------------------------------------- #
@router.patch(
    "/{erp}/assign-coordinator",
    response_model=UserResponse,
    summary="Promote a STUDENT to STUDENT_COORDINATOR",
)
async def assign_coordinator(
    erp: str,
    payload: AssignCoordinatorRequest,
    actor: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN, Role.FACULTY, Role.CLUB_LEAD))],
) -> UserResponse:
    target = await _load_user_by_erp(erp)

    if target.get("role") != Role.STUDENT.value:
        raise HTTPException(
            status_code=400,
            detail=f"Only STUDENT accounts can be promoted to STUDENT_COORDINATOR. '{erp}' is currently {target.get('role')}.",
        )

    caller_role = actor.role

    if caller_role == Role.CLUB_LEAD:
        caller_club = actor.club
        if not caller_club:
            raise HTTPException(status_code=500, detail="Caller's CLUB_LEAD account has no club assigned.")
        club_to_assign: Optional[Club] = caller_club if isinstance(caller_club, Club) else Club(caller_club)
    else:
        club_to_assign = payload.club

    changes: dict = {"role": Role.STUDENT_COORDINATOR}
    if club_to_assign is not None:
        changes["club"] = club_to_assign

    updated = await _apply_mutation(target, changes)

    await _create_notification(
        user_id=str(target["_id"]),
        title="Role Updated: Student Coordinator",
        message=f"You have been promoted to Student Coordinator for {club_to_assign.value if club_to_assign else 'an event'}.",
        n_type=NotificationType.ROLE_UPDATED,
        action_url="/events/manage"
    )
    
    return UserResponse.model_validate(updated)


# --------------------------------------------------------------------------- #
# 5. PATCH /api/users/{erp}/remove-coordinator
# --------------------------------------------------------------------------- #
@router.patch(
    "/{erp}/remove-coordinator",
    response_model=UserResponse,
    summary="Demote a STUDENT_COORDINATOR back to STUDENT",
)
async def remove_coordinator(
    erp: str,
    actor: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN, Role.FACULTY, Role.CLUB_LEAD))],
) -> UserResponse:
    target = await _load_user_by_erp(erp)

    if target.get("role") != Role.STUDENT_COORDINATOR.value:
        raise HTTPException(
            status_code=400,
            detail=f"User '{erp}' is not a STUDENT_COORDINATOR (current role: {target.get('role')}).",
        )

    if actor.role == Role.CLUB_LEAD:
        if target.get("club") != actor.club:
            raise HTTPException(
                status_code=403,
                detail=f"You can only remove coordinators that belong to your own club. Target club: {target.get('club')}, your club: {actor.club}.",
            )

    updated = await _apply_mutation(target, {"role": Role.STUDENT, "club": None})

    await _create_notification(
        user_id=str(target["_id"]),
        title="Role Updated: Student",
        message="Your Student Coordinator role has been removed. You are now a standard Student.",
        n_type=NotificationType.ROLE_UPDATED,
        action_url="/events/discover"
    )
    
    return UserResponse.model_validate(updated)


# --------------------------------------------------------------------------- #
# 6. PATCH /api/users/{erp}/make-deputy   (SUPER_DEAN only)
# --------------------------------------------------------------------------- #
@router.patch(
    "/{erp}/make-deputy",
    response_model=UserResponse,
    summary="Temporarily elevate a user to SUPER_DEAN status",
)
async def make_deputy(
    erp: str,
    payload: MakeDeputyRequest,
    actor: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN))],
) -> UserResponse:
    target = await _load_user_by_erp(erp)

    expiry_date = utc_now() + timedelta(days=payload.expiryDays)
    
    updated = await _apply_mutation(
        target, 
        {
            "role": Role.SUPER_DEAN,
            "adminExpiresAt": expiry_date
        }
    )
    
    await _create_notification(
        user_id=str(target["_id"]),
        title="Role Updated: Deputy Dean",
        message=f"You have been elevated to Deputy Super Dean until {expiry_date.strftime('%Y-%m-%d %H:%M')}.",
        n_type=NotificationType.ROLE_UPDATED,
        action_url="/admin/dashboard"
    )
    
    return UserResponse.model_validate(updated)


# --------------------------------------------------------------------------- #
# 7. GET /api/users   (SUPER_DEAN, FACULTY, CLUB_LEAD)
# --------------------------------------------------------------------------- #
@router.get(
    "",
    response_model=List[UserResponse],
    summary="Search/filter users by role, department, year, club, or ERP",
)
async def list_users(
    actor: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN, Role.FACULTY, Role.CLUB_LEAD))],
    role: Optional[Role] = Query(default=None),
    department: Optional[Department] = Query(default=None),
    year: Optional[int] = Query(default=None, ge=1, le=4),
    club: Optional[Club] = Query(default=None),
    erp: Optional[str] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> List[UserResponse]:
    query: dict = {}
    if role is not None: query["role"] = role.value
    if department is not None: query["department"] = department.value
    if year is not None: query["year"] = year
    if club is not None: query["club"] = club.value
    if erp is not None: query["erp"] = erp.strip().upper()

    cursor = users_collection().find(query, {"hashedPassword": 0}).sort("name", 1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [UserResponse.model_validate(doc) for doc in docs]


# --------------------------------------------------------------------------- #
# 8. DELETE /api/users/{erp}   (SUPER_DEAN only)
# --------------------------------------------------------------------------- #
@router.delete(
    "/{erp}",
    status_code=status.HTTP_200_OK,
    summary="Delete a user by ERP (SUPER_DEAN only)",
)
async def delete_user_by_erp(
    erp: str,
    actor: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN))],
) -> dict:
    from app.database import events_collection, notifications_collection

    target_erp = erp.strip().upper()

    if target_erp == actor.erp:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")

    target = await users_collection().find_one({"erp": target_erp})
    if target is None:
        raise HTTPException(status_code=404, detail=f"No user found with ERP '{target_erp}'.")

    owned_event = await events_collection().find_one({"createdBy": target["_id"]})
    if owned_event is not None:
        raise HTTPException(
            status_code=409,
            detail=f"User '{target_erp}' is the creator of event '{owned_event.get('title')}'. Delete or reassign that event first.",
        )

    co_authored = await events_collection().find_one({"coAuthorId": target["_id"]})
    if co_authored is not None:
        raise HTTPException(
            status_code=409,
            detail=f"User '{target_erp}' is a co-author on event '{co_authored.get('title')}'. Revoke that co-authorship first.",
        )

    await users_collection().delete_one({"_id": target["_id"]})
    notif_result = await notifications_collection().delete_many({"userId": str(target["_id"])})

    return {
        "message": f"User '{target_erp}' ({target.get('name')}) deleted successfully.",
        "cascadedNotificationsDeleted": notif_result.deleted_count,
    }


# --------------------------------------------------------------------------- #
# 9. GET /api/users/me   (Any authenticated user)
# --------------------------------------------------------------------------- #
@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the authenticated user's profile",
)
async def get_my_profile(
    actor: Annotated[UserInDB, Depends(get_current_active_user)],
) -> UserResponse:
    return UserResponse.model_validate(actor)


# --------------------------------------------------------------------------- #
# 10. PATCH /api/users/me/password   (Any authenticated user)
# --------------------------------------------------------------------------- #
@router.patch(
    "/me/password",
    status_code=status.HTTP_200_OK,
    summary="Change the authenticated user's password",
)
async def change_my_password(
    payload: ChangePasswordRequest,
    actor: Annotated[UserInDB, Depends(get_current_active_user)],
):
    if not verify_password(payload.old_password, actor.hashedPassword):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect old password.",
        )

    new_hash = hash_password(payload.new_password)
    
    await users_collection().update_one(
        {"_id": to_object_id(actor.id)},  # <-- SURGICAL FIX: Converts string ID to ObjectId for MongoDB
        {"$set": {"hashedPassword": new_hash, "requires_password_change": False, "updatedAt": utc_now()}}
    )

    return {"message": "Password updated successfully."}


__all__ = ["router"]