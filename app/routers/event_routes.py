"""
Event management routes (mounted at /api/events).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Annotated, List, Optional

import pymongo
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.deps import get_current_active_user
from app.database import (
    audit_logs_collection,
    events_collection,
    notifications_collection,
    registrations_collection,
    users_collection,
    venues_collection,
)
from app.middleware.auth import require_roles
from app.models.audit import AuditLogCreate
from app.models.base import (
    MongoBaseModel,
    PyObjectId,
    ensure_utc,
    to_object_id,
    utc_now,
)
from app.models.enums import (
    EVENT_CREATOR_ROLES,
    STUDENT_ROLES,
    Department,
    EventStatus,
    Role,
)
from app.models.event import (
    EventCreate,
    EventInDB,
    EventResponse,
    EventUpdate,
    RegistrationSubSchema,
)
from app.models.notification import NotificationCreate, NotificationType
from app.models.user import UserInDB
from app.services import clash_service

router = APIRouter(prefix="/api/events", tags=["Events"])
logger = logging.getLogger("nexevent.events")

VENUE_CONFLICT_BUFFER = timedelta(minutes=30)
APPROVER_ROLE_VALUES = [Role.SUPER_DEAN.value, Role.FACULTY.value]


# --------------------------------------------------------------------------- #
# Helper Functions
# --------------------------------------------------------------------------- #
async def _log_audit(actor, action: str, target_type: str, target_id: str, details: str = None):
    try:
        log = AuditLogCreate(
            actorId=str(actor.id),
            actorName=actor.name,
            action=action,
            targetType=target_type,
            targetId=str(target_id),
            details=details
        )
        await audit_logs_collection().insert_one(log.model_dump())
    except Exception:
        logger.exception("Failed to write audit log for action %s", action)


async def check_venue_conflict(
    venue_name: str,
    start_time: datetime,
    end_time: datetime,
    exclude_event_id: Optional[str] = None,
) -> None:
    """
    Rule 1: The Global Venue Clash.
    Applies to ALL events (Open, Compulsory, or Registration).
    """
    start_time = ensure_utc(start_time)
    end_time = ensure_utc(end_time)

    buffered_start = start_time - VENUE_CONFLICT_BUFFER
    buffered_end = end_time + VENUE_CONFLICT_BUFFER

    query: dict = {
        "venueName": {"$regex": f"^{re.escape(venue_name)}$", "$options": "i"},
        "status": {
            "$in": [
                EventStatus.PENDING_APPROVAL.value,
                EventStatus.APPROVED.value,
            ]
        },
        "startTime": {"$lt": buffered_end},
        "endTime": {"$gt": buffered_start},
    }
    if exclude_event_id:
        try:
            query["_id"] = {"$ne": to_object_id(exclude_event_id)}
        except ValueError:
            pass

    conflict = await events_collection().find_one(query)
    if conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Venue '{venue_name}' is already booked for an overlapping "
                f"slot (±30 min buffer). Conflicting event: "
                f"'{conflict.get('title', 'Unknown')}'."
            ),
        )


async def _upsert_venue_name(name: str) -> None:
    if not name or not name.strip():
        return
    try:
        await venues_collection().update_one(
            {"name": name},
            {
                "$setOnInsert": {
                    "name": name,
                    "createdAt": utc_now(),
                    "updatedAt": utc_now(),
                }
            },
            upsert=True,
        )
    except Exception:
        logger.debug("Venue upsert race for %r ignored.", name)


def _student_audience_conditions(department: str, year: int) -> List[dict]:
    return [
        {
            "$or": [
                {"targetDepartments": {"$exists": False}},
                {"targetDepartments": {"$size": 0}},
                {"targetDepartments": department},
            ]
        },
        {
            "$or": [
                {"targetYears": {"$exists": False}},
                {"targetYears": {"$size": 0}},
                {"targetYears": year},
            ]
        },
    ]


def _student_is_eligible(event: dict, user: dict) -> bool:
    targets_dept = event.get("targetDepartments") or []
    targets_year = event.get("targetYears") or []
    targets_course = event.get("targetCourses") or []
    
    dept = user.get("department")
    year = user.get("year")

    if targets_dept and dept not in targets_dept:
        return False
    if targets_year and year not in targets_year:
        return False
    if targets_course:
        user_dept_str = str(dept) if dept else ""
        if user_dept_str not in targets_course and not any(c in user_dept_str for c in targets_course):
            return False
            
    return True


async def _create_notification(
    user_id: str,
    title: str,
    message: str,
    notification_type: NotificationType,
    related_event_id: Optional[str] = None,
    action_url: Optional[str] = None,
) -> None:
    try:
        notif = NotificationCreate(
            userId=user_id,
            title=title,
            message=message,
            type=notification_type,
            relatedEventId=related_event_id,
            actionUrl=action_url,
        )
        await notifications_collection().insert_one(notif.to_mongo())
    except Exception:
        logger.exception(
            "Failed to create notification (type=%s, user_id=%s)",
            getattr(notification_type, "value", notification_type),
            user_id,
        )


async def _notify_targeted_students(
    event: dict,
    event_id: str,
    title: str,
    message: str,
    notification_type: NotificationType,
) -> int:
    query: dict = {"role": Role.STUDENT.value}
    students = await users_collection().find(query, {"_id": 1, "department": 1, "year": 1}).to_list(5000)

    notified = 0
    for student in students:
        if _student_is_eligible(event, student):
            await _create_notification(
                user_id=str(student["_id"]),
                title=title,
                message=message,
                notification_type=notification_type,
                related_event_id=event_id,
                action_url="/events/discover",
            )
            notified += 1
    return notified


# --------------------------------------------------------------------------- #
# Schemas & Models
# --------------------------------------------------------------------------- #
class SetCoAuthorRequest(MongoBaseModel):
    model_config = ConfigDict(extra="forbid")
    coAuthorId: Optional[PyObjectId] = Field(default=None)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remarks: str = Field(..., min_length=3, max_length=1000)


class DeleteEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(..., min_length=3, max_length=500, description="Reason for deletion")


class GoogleFormSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rollNumber: int = Field(..., gt=0)
    erp: str = Field(..., min_length=4, max_length=32)
    response_id: str = Field(..., description="Google Form response ID for idempotency")

    @field_validator("erp", mode="before")
    @classmethod
    def _norm_erp(cls, v):
        return v.strip().upper() if isinstance(v, str) else v


# --------------------------------------------------------------------------- #
# CRUD & Lifecycle Endpoints
# --------------------------------------------------------------------------- #
@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    event_in: EventCreate,
    current_user: Annotated[
        UserInDB,
        Depends(require_roles(Role.FACULTY, Role.CLUB_LEAD, Role.SUPER_DEAN)),
    ],
):
    # Rule 1: Global Venue Clash
    await check_venue_conflict(
        venue_name=event_in.venueName,
        start_time=event_in.startTime,
        end_time=event_in.endTime,
    )

    # Rule 2: Cohort Schedule Clash
    if event_in.isCompulsory or event_in.requiresRegistration:
        conflicts = await clash_service.find_event_creation_conflicts(
            start_time=event_in.startTime,
            end_time=event_in.endTime,
            target_years=event_in.targetYears,
            target_courses=event_in.targetCourses,
        )
        if conflicts:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "detail": "Schedule clash detected for this specific Course and Year cohort.",
                    "conflicts": [c.model_dump(mode="json") for c in conflicts],
                },
            )

    is_super_dean = current_user.role == Role.SUPER_DEAN
    doc = event_in.model_dump(by_alias=True)
    doc["createdBy"] = current_user.id
    doc["registeredStudents"] = []

    if is_super_dean:
        doc["status"] = EventStatus.APPROVED.value
        doc["approvedBy"] = current_user.id
        doc["approvedAt"] = utc_now()
    else:
        doc["status"] = EventStatus.PENDING_APPROVAL.value

    result = await events_collection().insert_one(doc)
    created = await events_collection().find_one({"_id": result.inserted_id})

    await _upsert_venue_name(event_in.venueName)
    await _log_audit(current_user, "CREATE_EVENT", "EVENT", str(result.inserted_id), f"Created event: {created['title']}")

    if is_super_dean:
        await _notify_targeted_students(
            event=created,
            event_id=str(result.inserted_id),
            title=f"New Event: {created['title']}",
            message=f"A new event matching your department is open for registration.",
            notification_type=NotificationType.EVENT_APPROVED,
        )
    else:
        approvers = await users_collection().find({"role": {"$in": APPROVER_ROLE_VALUES}}, {"_id": 1}).to_list(100)
        for approver in approvers:
            await _create_notification(
                user_id=str(approver["_id"]),
                title="New Event Pending Approval",
                message=f"'{created['title']}' awaits your review.",
                notification_type=NotificationType.EVENT_SUBMITTED,
                related_event_id=str(result.inserted_id),
                action_url="/admin/dashboard",
            )

    return EventResponse.model_validate(created)


@router.get("/faculty-feed", response_model=List[EventResponse])
async def faculty_feed(
    current_user: Annotated[UserInDB, Depends(require_roles(Role.FACULTY, Role.SUPER_DEAN, Role.CLUB_LEAD))],
):
    now = utc_now()
    start_window = now - timedelta(days=5)
    end_window = now + timedelta(days=5)
    
    query = {
        "status": {"$in": [EventStatus.APPROVED.value, EventStatus.COMPLETED.value]},
        "startTime": {"$gte": start_window, "$lte": end_window}
    }
    
    cursor = events_collection().find(query).sort("startTime", 1)
    events = await cursor.to_list(length=200)
    
    valid_events = []
    for e in events:
        try:
            valid_events.append(EventResponse.model_validate(e))
        except Exception as ex:
            logger.error(f"Failed to validate event {e.get('_id')} in feed: {ex}")
            
    return valid_events


@router.get("/campus-window", response_model=List[EventResponse])
async def campus_window(
    current_user: Annotated[UserInDB, Depends(get_current_active_user)],
):
    now = utc_now()
    start_window = now - timedelta(days=5)
    end_window = now + timedelta(days=5)
    
    query = {
        "status": {"$in": [EventStatus.APPROVED.value, EventStatus.COMPLETED.value]},
        "startTime": {"$gte": start_window, "$lte": end_window}
    }
    
    cursor = events_collection().find(query).sort("startTime", 1)
    events = await cursor.to_list(length=200)
    
    valid_events = []
    for e in events:
        try:
            resp = EventResponse.model_validate(e)
            resp.isRegistered = str(current_user.id) in [str(s) for s in e.get("registeredStudents", [])]
            valid_events.append(resp)
        except Exception as ex:
            logger.error(f"Failed to validate event {e.get('_id')} in campus window: {ex}")
            
    return valid_events


@router.get("", response_model=List[EventResponse])
async def list_events(
    current_user: Annotated[UserInDB, Depends(get_current_active_user)],
    status_filter: Optional[EventStatus] = Query(default=None, alias="status"),
    club: Optional[str] = Query(default=None),
    mine: bool = Query(default=False),
    oversight: bool = Query(default=False),
    coordinating: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    role = current_user.role

    if role == Role.SUPER_DEAN:
        query: dict = {}
    elif role in (Role.STUDENT, Role.CLUB_LEAD, Role.STUDENT_COORDINATOR):
        base_or = [
            {
                "status": {"$in": [EventStatus.APPROVED.value, EventStatus.COMPLETED.value]},
                "$and": _student_audience_conditions(
                    department=current_user.department,
                    year=current_user.year,
                ),
            }
        ]
        
        if role == Role.CLUB_LEAD:
            base_or.extend([
                {"createdBy": current_user.id},
                {"coAuthorId": current_user.id},
                {"clubLeads": {"$regex": current_user.name, "$options": "i"}},
                {"clubLeads": {"$regex": current_user.erp, "$options": "i"}},
            ])
            if current_user.club:
                club_val = current_user.club.value if hasattr(current_user.club, 'value') else current_user.club
                base_or.append({"organizingClub": club_val})
                
        query = {"$or": base_or}
    else:
        query = {
            "$or": [
                {"status": EventStatus.APPROVED.value},
                {"status": EventStatus.COMPLETED.value}, 
                {"createdBy": current_user.id},
                {"coAuthorId": current_user.id},
            ]
        }

    if status_filter is not None:
        query["status"] = status_filter.value
    if club:
        query["organizingClub"] = club
    if mine:
        if role == Role.CLUB_LEAD:
            club_val = current_user.club.value if hasattr(current_user.club, 'value') else current_user.club
            mine_clause = {
                "$or": [
                    {"createdBy": current_user.id},
                    {"organizingClub": club_val} if club_val else {"createdBy": current_user.id}
                ]
            }
            if "$and" in query:
                query["$and"].append(mine_clause)
            else:
                query["$and"] = [mine_clause]
        else:
            if "$and" in query:
                query["$and"].append({"createdBy": current_user.id})
            else:
                query["$and"] = [{"createdBy": current_user.id}]
        
    if oversight:
        name_query = {"facultyIncharge": {"$regex": current_user.name, "$options": "i"}}
        erp_query = {"facultyIncharge": {"$regex": current_user.erp, "$options": "i"}}
        if "$and" in query:
            query["$and"].extend([{"$or": [name_query, erp_query]}])
        else:
            query["$and"] = [{"$or": [name_query, erp_query]}]
        if "$or" in query and not mine:
            del query["$or"]
            
    if coordinating:
        name_query = {"studentCoordinators": {"$regex": current_user.name, "$options": "i"}}
        erp_query = {"studentCoordinators": {"$regex": current_user.erp, "$options": "i"}}
        if "$and" in query:
            query["$and"].extend([{"$or": [name_query, erp_query]}])
        else:
            query["$and"] = [{"$or": [name_query, erp_query]}]
        if "$or" in query and not mine:
            del query["$or"]

    cursor = events_collection().find(query).sort("startTime", -1).skip(skip).limit(limit)
    events = await cursor.to_list(length=limit)
    
    valid_events = []
    for e in events:
        try:
            resp = EventResponse.model_validate(e)
            if current_user:
                resp.isRegistered = str(current_user.id) in [str(s) for s in e.get("registeredStudents", [])]
            valid_events.append(resp)
        except Exception as ex:
            logger.error(f"Failed to validate event {e.get('_id')} in list: {ex}")
            
    return valid_events


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: str,
    _: Annotated[dict, Depends(get_current_active_user)],
):
    try:
        oid = to_object_id(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")

    event = await events_collection().find_one({"_id": oid})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
        
    try:
        return EventResponse.model_validate(event)
    except Exception as ex:
        raise HTTPException(status_code=400, detail=f"Event data is invalid: {str(ex)}")


@router.patch("/{event_id}/update", response_model=EventResponse)
async def update_event(
    event_id: str,
    event_in: EventUpdate,
    current_user: Annotated[
        UserInDB,
        Depends(require_roles(*EVENT_CREATOR_ROLES, Role.SUPER_DEAN)),
    ],
):
    try:
        oid = to_object_id(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")

    event = await events_collection().find_one({"_id": oid})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    is_creator = str(event["createdBy"]) == current_user.id
    is_super_dean = current_user.role == Role.SUPER_DEAN
    is_co_author = (
        event.get("coAuthorId") is not None
        and str(event["coAuthorId"]) == current_user.id
        and current_user.role == Role.CLUB_LEAD
    )
    
    is_faculty_incharge = False
    faculty_list = event.get("facultyIncharge", [])
    for f in faculty_list:
        if (current_user.erp and current_user.erp in f) or (current_user.name and current_user.name in f):
            is_faculty_incharge = True
            break

    if not (is_super_dean or is_creator or is_co_author or is_faculty_incharge):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator, a designated co-author, assigned Faculty Incharge, or SUPER_DEAN may edit.",
        )

    if event["status"] not in (
        EventStatus.DRAFT.value,
        EventStatus.PENDING_APPROVAL.value,
        EventStatus.REJECTED.value,
        EventStatus.APPROVED.value,
    ):
        raise HTTPException(
            status_code=400,
            detail="This event cannot be edited in its current state.",
        )

    update_data = event_in.model_dump(exclude_unset=True, by_alias=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update.")

    merged = {**event, **update_data}

    # Rule 1: Global Venue Clash
    vce_fields = {"venueName", "startTime", "endTime"}
    if any(k in update_data for k in vce_fields):
        try:
            EventInDB.model_validate(merged)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        await check_venue_conflict(
            venue_name=merged["venueName"],
            start_time=merged["startTime"],
            end_time=merged["endTime"],
            exclude_event_id=event_id,
        )

    lifecycle_fields = {"isCompulsory", "requiresRegistration", "registrationUrl"}
    if any(k in update_data for k in lifecycle_fields):
        try:
            EventInDB.model_validate(merged)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Rule 2: Cohort Schedule Clash
    clash_fields = {"isCompulsory", "requiresRegistration", "startTime", "endTime", "targetYears", "targetCourses"}
    if any(k in update_data for k in clash_fields):
        if merged.get("isCompulsory") or merged.get("requiresRegistration"):
            conflicts = await clash_service.find_event_creation_conflicts(
                start_time=merged["startTime"],
                end_time=merged["endTime"],
                target_years=merged.get("targetYears", []),
                target_courses=merged.get("targetCourses", []),
                exclude_event_id=event_id,
            )
            if conflicts:
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={
                        "detail": "Schedule clash detected for this specific Course and Year cohort.",
                        "conflicts": [c.model_dump(mode="json") for c in conflicts],
                    },
                )

    update_data["updatedAt"] = utc_now()

    result = await events_collection().find_one_and_update(
        {"_id": oid},
        {"$set": update_data},
        return_document=True,
    )

    if "venueName" in update_data:
        await _upsert_venue_name(update_data["venueName"])

    await _log_audit(current_user, "UPDATE_EVENT", "EVENT", event_id, f"Fields updated: {', '.join(update_data.keys())}")

    return EventResponse.model_validate(result)


@router.patch("/{event_id}/approve", response_model=EventResponse)
async def approve_event(
    event_id: str,
    current_user: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN))],
):
    try:
        oid = to_object_id(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")

    event = await events_collection().find_one({"_id": oid})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if event["status"] != EventStatus.PENDING_APPROVAL.value:
        raise HTTPException(status_code=400, detail="Only PENDING_APPROVAL events can be approved.")

    result = await events_collection().find_one_and_update(
        {"_id": oid},
        {"$set": {"status": EventStatus.APPROVED.value, "approvedBy": current_user.id, "approvedAt": utc_now(), "updatedAt": utc_now()}},
        return_document=True,
    )

    await _create_notification(
        user_id=str(event["createdBy"]),
        title="Event Approved",
        message=f"Your event '{event['title']}' has been approved.",
        notification_type=NotificationType.EVENT_APPROVED,
        related_event_id=event_id,
        action_url="/events/manage",
    )

    await _notify_targeted_students(
        event=result,
        event_id=event_id,
        title=f"New Event: {result['title']}",
        message=f"A new event matching your department is now open for registration.",
        notification_type=NotificationType.EVENT_APPROVED,
    )

    await _log_audit(current_user, "APPROVE_EVENT", "EVENT", event_id, f"Approved event: {event['title']}")

    return EventResponse.model_validate(result)


@router.patch("/{event_id}/reject", response_model=EventResponse)
async def reject_event(
    event_id: str,
    payload: RejectRequest,
    current_user: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN))],
):
    try:
        oid = to_object_id(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")

    event = await events_collection().find_one({"_id": oid})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if event["status"] != EventStatus.PENDING_APPROVAL.value:
        raise HTTPException(status_code=400, detail="Only PENDING_APPROVAL events can be rejected.")

    result = await events_collection().find_one_and_update(
        {"_id": oid},
        {"$set": {"status": EventStatus.REJECTED.value, "approvedBy": current_user.id, "approvedAt": utc_now(), "rejectionReason": payload.remarks, "updatedAt": utc_now()}},
        return_document=True,
    )

    await _create_notification(
        user_id=str(event["createdBy"]),
        title="Event Rejected - Action Required",
        message=f"Your event '{event['title']}' was rejected. Feedback: {payload.remarks}",
        notification_type=NotificationType.EVENT_REJECTED,
        related_event_id=event_id,
        action_url="/events/manage",
    )

    await _log_audit(current_user, "REJECT_EVENT", "EVENT", event_id, f"Rejected event: {event['title']}. Reason: {payload.remarks}")

    return EventResponse.model_validate(result)


@router.patch("/{event_id}/complete", response_model=EventResponse)
async def complete_event(
    event_id: str,
    current_user: Annotated[UserInDB, Depends(require_roles(*EVENT_CREATOR_ROLES, Role.SUPER_DEAN))],
):
    try:
        oid = to_object_id(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")

    event = await events_collection().find_one({"_id": oid})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if event["status"] == EventStatus.COMPLETED.value:
        raise HTTPException(status_code=400, detail="This event is already completed.")
    if event["status"] == EventStatus.CANCELLED.value:
        raise HTTPException(status_code=400, detail="Cancelled events cannot be completed.")

    is_super_dean = current_user.role == Role.SUPER_DEAN
    is_faculty_incharge = False
    for f in event.get("facultyIncharge", []):
        if (current_user.erp and current_user.erp in f) or (current_user.name and current_user.name in f):
            is_faculty_incharge = True
            break

    if not (is_super_dean or is_faculty_incharge):
        raise HTTPException(status_code=403, detail="Only Super Dean or assigned Faculty Incharge can complete this event.")

    result = await events_collection().find_one_and_update(
        {"_id": oid},
        {"$set": {"status": EventStatus.COMPLETED.value, "completedAt": utc_now(), "updatedAt": utc_now()}},
        return_document=True,
    )

    coordinators = event.get("studentCoordinators", [])
    for coord in coordinators:
        erp_to_find = coord.split("(")[-1].replace(")", "").strip() if "(" in coord else coord
        name_to_find = coord.split("(")[0].strip() if "(" in coord else coord

        user_match = await users_collection().find_one({
            "$or": [{"erp": erp_to_find}, {"name": name_to_find}]
        })
        
        if user_match:
            if user_match.get("role") == Role.STUDENT_COORDINATOR.value:
                await users_collection().update_one(
                    {"_id": user_match["_id"]},
                    {"$set": {"role": Role.STUDENT.value, "club": None}}
                )
            
            await _create_notification(
                user_id=str(user_match["_id"]),
                title="Coordinator Tenure Ended",
                message=f"Your tenure as coordinator for '{event['title']}' has ended.",
                notification_type=NotificationType.ROLE_UPDATED,
                related_event_id=event_id,
                action_url="/events/discover",
            )

    await _log_audit(current_user, "COMPLETE_EVENT", "EVENT", event_id, f"Completed event: {event['title']}")

    return EventResponse.model_validate(result)


@router.patch("/{event_id}/co-author", response_model=EventResponse)
async def set_co_author(
    event_id: str,
    payload: SetCoAuthorRequest,
    current_user: Annotated[UserInDB, Depends(require_roles(*EVENT_CREATOR_ROLES))],
):
    try:
        oid = to_object_id(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")

    event = await events_collection().find_one({"_id": oid})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    is_creator = str(event["createdBy"]) == current_user.id
    is_super_dean = current_user.role == Role.SUPER_DEAN
    if not (is_creator or is_super_dean):
        raise HTTPException(status_code=403, detail="Only the creator or SUPER_DEAN may set a co-author.")

    if payload.coAuthorId is None:
        previous = event.get("coAuthorId")
        result = await events_collection().find_one_and_update({"_id": oid}, {"$set": {"coAuthorId": None, "updatedAt": utc_now()}}, return_document=True)
        if previous is not None:
            await _create_notification(
                user_id=str(previous), 
                title="Co-author access revoked", 
                message=f"You no longer have edit rights on '{event['title']}'.", 
                notification_type=NotificationType.CO_AUTHOR_REVOKED, 
                related_event_id=event_id,
                action_url="/events/manage"
            )
        return EventResponse.model_validate(result)

    if payload.coAuthorId == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot co-author your own event.")
    if str(event["createdBy"]) == payload.coAuthorId:
        raise HTTPException(status_code=400, detail="Creator is already the primary author.")

    co_author = await users_collection().find_one({"_id": to_object_id(payload.coAuthorId)})
    if not co_author or co_author.get("role") != Role.CLUB_LEAD.value:
        raise HTTPException(status_code=400, detail="Only CLUB_LEAD accounts may be co-authors.")

    previous = event.get("coAuthorId")
    result = await events_collection().find_one_and_update({"_id": oid}, {"$set": {"coAuthorId": to_object_id(payload.coAuthorId), "updatedAt": utc_now()}}, return_document=True)

    if previous is not None and str(previous) != payload.coAuthorId:
        await _create_notification(
            user_id=str(previous), 
            title="Co-author access revoked", 
            message=f"You no longer have edit rights on '{event['title']}'.", 
            notification_type=NotificationType.CO_AUTHOR_REVOKED, 
            related_event_id=event_id,
            action_url="/events/manage"
        )

    await _create_notification(
        user_id=payload.coAuthorId, 
        title="You've been added as a co-author", 
        message=f"{current_user.name} granted you edit rights on '{event['title']}'.", 
        notification_type=NotificationType.CO_AUTHOR_ASSIGNED, 
        related_event_id=event_id,
        action_url="/events/manage"
    )

    return EventResponse.model_validate(result)


@router.delete("/{event_id}", status_code=status.HTTP_200_OK)
async def delete_event(
    event_id: str,
    payload: DeleteEventRequest,
    current_user: Annotated[UserInDB, Depends(require_roles(*EVENT_CREATOR_ROLES, Role.SUPER_DEAN))],
):
    try:
        oid = to_object_id(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")

    event = await events_collection().find_one({"_id": oid})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    is_creator = str(event["createdBy"]) == current_user.id
    is_super_dean = current_user.role == Role.SUPER_DEAN
    if not (is_creator or is_super_dean):
        raise HTTPException(status_code=403, detail="Only the creator or SUPER_DEAN may delete this event.")

    if not is_super_dean and event["status"] not in (EventStatus.DRAFT.value, EventStatus.PENDING_APPROVAL.value, EventStatus.REJECTED.value):
        raise HTTPException(status_code=400, detail="Creators can only delete DRAFT, PENDING_APPROVAL, or REJECTED events.")

    await _create_notification(
        user_id=str(event["createdBy"]),
        title="Event Deleted",
        message=f"Your event '{event['title']}' was deleted. Reason: {payload.reason}",
        notification_type=NotificationType.EVENT_REJECTED,
        related_event_id=None, 
        action_url="/events/manage"
    )

    registered_students = event.get("registeredStudents", [])
    for student_id in registered_students:
        await _create_notification(
            user_id=str(student_id),
            title="Event Deleted",
            message=f"The event '{event['title']}' you registered for has been deleted. Reason: {payload.reason}",
            notification_type=NotificationType.EVENT_REJECTED,
            related_event_id=None,
            action_url="/events/discover"
        )

    await _log_audit(
        current_user, 
        "DELETE_EVENT", 
        "EVENT", 
        event_id, 
        f"Deleted event: {event['title']}. Reason: {payload.reason}"
    )

    await events_collection().delete_one({"_id": oid})
    notif_result = await notifications_collection().delete_many({"relatedEventId": event_id})

    return {
        "message": f"Event '{event['title']}' deleted.",
        "reason": payload.reason,
        "studentsNotified": len(registered_students),
        "cascadedNotificationsDeleted": notif_result.deleted_count,
    }


# --------------------------------------------------------------------------- #
# Registration Endpoints (Internal & Webhook)
# --------------------------------------------------------------------------- #
@router.post("/{event_id}/register", response_model=EventResponse)
async def register_for_event(
    event_id: str,
    current_user: Annotated[UserInDB, Depends(require_roles(*STUDENT_ROLES))],
):
    try:
        oid = to_object_id(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format")

    event = await events_collection().find_one({"_id": oid})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if event["status"] != EventStatus.APPROVED.value:
        raise HTTPException(status_code=400, detail="Registration is only open for APPROVED events.")
    
    # Enforce 3-State Lifecycle Logic
    if event.get("isCompulsory"):
        raise HTTPException(status_code=400, detail="Compulsory events do not require registration.")
    if not event.get("requiresRegistration", True):
        raise HTTPException(status_code=400, detail="This event is open and does not require registration.")
    if event.get("registrationUrl"):
         raise HTTPException(status_code=400, detail="Please use the external registration link provided.")

    if not _student_is_eligible(event, current_user.model_dump()):
        raise HTTPException(status_code=403, detail="This event is not open to your department or year.")

    # Rule 3: The Individual Student Clash
    conflicts = await clash_service.find_student_registration_conflicts(
        student_id=current_user.id,
        cohort_year=current_user.year,
        cohort_course=str(current_user.department.value) if current_user.department else None,
        start_time=event["startTime"],
        end_time=event["endTime"],
        exclude_event_id=event_id,
    )
    if conflicts:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": "You have a scheduling clash with another commitment.",
                "conflicts": [c.model_dump(mode="json") for c in conflicts],
            },
        )

    if current_user.id in [str(s) for s in event.get("registeredStudents", [])]:
        raise HTTPException(status_code=400, detail="You are already registered.")

    if event.get("capacity") and len(event.get("registeredStudents", [])) >= event["capacity"]:
        raise HTTPException(status_code=400, detail="Event is at full capacity.")

    result = await events_collection().find_one_and_update(
        {"_id": oid},
        {"$push": {"registeredStudents": to_object_id(current_user.id)}, "$set": {"updatedAt": utc_now()}},
        return_document=True,
    )

    await _create_notification(
        user_id=current_user.id, 
        title="Registration Confirmed", 
        message=f"You are registered for '{event['title']}'.", 
        notification_type=NotificationType.REGISTRATION_CONFIRMED, 
        related_event_id=event_id,
        action_url="/events/discover"
    )

    return EventResponse.model_validate(result)


async def _process_google_form_sync(event_id: str, payload: GoogleFormSyncRequest):
    """
    Background worker for Google Form webhooks.
    Runs asynchronously so Apps Script never times out.
    """
    try:
        oid = to_object_id(event_id)
        event = await events_collection().find_one({"_id": oid})
        if not event:
            logger.warning(f"Webhook failed: Event not found {event_id}")
            return
            
        if event["status"] != EventStatus.APPROVED.value:
            logger.warning(f"Webhook failed: Event {event_id} is not APPROVED (status: {event['status']})")
            return

        student = await users_collection().find_one({"rollNumber": payload.rollNumber, "erp": payload.erp})
        if not student:
            logger.warning(f"Webhook failed: No student found with ERP {payload.erp} and Roll {payload.rollNumber}")
            return
            
        if student.get("role") not in {r.value for r in STUDENT_ROLES}:
            logger.warning(f"Webhook failed: User {payload.erp} has role {student.get('role')}, not a student role.")
            return

        if not _student_is_eligible(event, student):
            logger.warning(f"Webhook failed: User {payload.erp} (Dept: {student.get('department')}, Year: {student.get('year')}) is not eligible for event targets.")
            return

        student_id = student["_id"]
        
        if event.get("capacity") and len(event.get("registeredStudents", [])) >= event["capacity"]:
            logger.warning(f"Webhook failed: Event {event_id} is at full capacity.")
            return

        # 1. Insert into dedicated registrations collection (Idempotency Guard)
        try:
            reg_doc = RegistrationSubSchema(
                eventId=oid,
                studentId=student_id,
                idempotency_key=payload.response_id
            )
            await registrations_collection().insert_one(reg_doc.model_dump(by_alias=True))
        except pymongo.errors.DuplicateKeyError:
            logger.info(f"Webhook ignored: Duplicate response_id {payload.response_id} or student already registered.")
            return

        # 2. Push to Event's registeredStudents array
        await events_collection().find_one_and_update(
            {"_id": oid},
            {"$push": {"registeredStudents": student_id}, "$set": {"updatedAt": utc_now()}},
        )
        logger.info(f"Webhook success: Registered student {payload.erp} for event {event_id}")

        # 3. Notify
        await _create_notification(
            user_id=str(student_id), 
            title="Registration Confirmed", 
            message=f"You are registered for '{event['title']}'.", 
            notification_type=NotificationType.REGISTRATION_CONFIRMED, 
            related_event_id=event_id,
            action_url="/events/discover"
        )
    except Exception as e:
        logger.error(f"Async Webhook Error processing {payload.response_id}: {str(e)}")


@router.post("/{event_id}/google-form-sync", status_code=status.HTTP_202_ACCEPTED)
async def google_form_sync(
    event_id: str,
    payload: GoogleFormSyncRequest,
    background_tasks: BackgroundTasks,
):
    """
    Receives webhooks from Google Apps Script.
    Instantly returns 202 Accepted and processes ERP validation in the background.
    """
    background_tasks.add_task(_process_google_form_sync, event_id, payload)
    
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "accepted", "message": "Webhook queued for processing."}
    )