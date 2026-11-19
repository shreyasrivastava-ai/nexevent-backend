"""
Institutional dashboard routes (mounted at /api/dashboard).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_active_user
from app.database import events_collection, users_collection, venues_collection, audit_logs_collection
from app.middleware.auth import require_roles
from app.models.base import to_object_id
from app.models.enums import EventStatus, Role, Club, Department, NAACCriterion
from app.models.user import UserInDB

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


def _can_access_event_roster(event: dict, user: UserInDB) -> bool:
    """Security check to ensure users only see rosters for events they manage."""
    role = user.role
    
    if role == Role.SUPER_DEAN:
        return True
        
    if role == Role.FACULTY:
        for f in event.get("facultyIncharge", []):
            if (user.erp and user.erp in f) or (user.name and user.name in f):
                return True
        return False
        
    if role == Role.CLUB_LEAD:
        if str(event.get("createdBy")) == user.id or str(event.get("coAuthorId")) == user.id:
            return True
        if user.club and event.get("organizingClub") == user.club.value:
            return True
        for c in event.get("clubLeads", []):
            if (user.erp and user.erp in c) or (user.name and user.name in c):
                return True
        return False

    if role == Role.STUDENT_COORDINATOR:
        for c in event.get("studentCoordinators", []):
            if (user.erp and user.erp in c) or (user.name and user.name in c):
                return True
        return False

    return False


async def _get_roster_data(event: dict) -> list[dict]:
    """Helper to fetch and format student details for an event."""
    student_ids_raw = event.get("registeredStudents") or []
    student_oids = []
    for sid in student_ids_raw:
        try:
            student_oids.append(to_object_id(str(sid)))
        except ValueError:
            continue

    students: list[dict] = []
    if student_oids:
        cursor = users_collection().find(
            {"_id": {"$in": student_oids}},
            {"name": 1, "rollNumber": 1, "erp": 1, "department": 1, "year": 1, "email": 1, "phone": 1},
        )
        docs = await cursor.to_list(length=len(student_oids))
        by_id = {str(d["_id"]): d for d in docs}
        for sid in student_ids_raw:
            doc = by_id.get(str(sid))
            if doc is not None:
                students.append({
                    "id": str(doc["_id"]),
                    "name": doc.get("name"),
                    "rollNumber": doc.get("rollNumber"),
                    "erp": doc.get("erp"),
                    "department": doc.get("department"),
                    "year": doc.get("year"),
                    "email": doc.get("email"),
                    "phone": doc.get("phone"),
                })
    return students


# --------------------------------------------------------------------------- #
# 1. GET /api/dashboard/events/{event_id}/roster
# --------------------------------------------------------------------------- #
@router.get("/events/{event_id}/roster")
async def event_roster(
    event_id: str,
    current_user: Annotated[
        UserInDB,
        Depends(require_roles(
            Role.SUPER_DEAN,
            Role.FACULTY,
            Role.CLUB_LEAD,
            Role.STUDENT_COORDINATOR,
        )),
    ],
) -> dict:
    try:
        event_oid = to_object_id(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format.")

    event = await events_collection().find_one({"_id": event_oid})
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found.")

    if not _can_access_event_roster(event, current_user):
        raise HTTPException(status_code=403, detail="You do not have permission to view this event's roster.")

    students = await _get_roster_data(event)

    return {
        "eventId": event_id,
        "eventTitle": event.get("title"),
        "eventStatus": event.get("status"),
        "venueName": event.get("venueName"),
        "startTime": event.get("startTime"),
        "endTime": event.get("endTime"),
        "capacity": event.get("capacity"),
        "registeredCount": len(students),
        "seatsRemaining": max(event.get("capacity", 0) - len(students), 0) if event.get("capacity") else None,
        "roster": students,
    }


# --------------------------------------------------------------------------- #
# 1B. GET /api/dashboard/events/{event_id}/roster/export (CSV)
# --------------------------------------------------------------------------- #
@router.get("/events/{event_id}/roster/export")
async def export_event_roster(
    event_id: str,
    current_user: Annotated[
        UserInDB,
        Depends(require_roles(
            Role.SUPER_DEAN,
            Role.FACULTY,
            Role.CLUB_LEAD,
            Role.STUDENT_COORDINATOR,
        )),
    ],
) -> StreamingResponse:
    try:
        event_oid = to_object_id(event_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid event ID format.")

    event = await events_collection().find_one({"_id": event_oid})
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found.")

    if not _can_access_event_roster(event, current_user):
        raise HTTPException(status_code=403, detail="You do not have permission to export this event's roster.")

    students = await _get_roster_data(event)

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(["Name", "Roll Number", "ERP", "Department", "Year", "Email", "Phone"])

    for s in students:
        writer.writerow([
            s.get("name") or "—",
            s.get("rollNumber") or "—",
            s.get("erp") or "—",
            s.get("department") or "—",
            s.get("year") or "—",
            s.get("email") or "—",
            s.get("phone") or "—",
        ])

    content = buf.getvalue().encode("utf-8-sig")
    safe_title = "".join(c for c in event.get("title", "Event") if c.isalnum() or c in " -_").strip().replace(" ", "_")

    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="roster_{safe_title}.csv"', "Cache-Control": "no-store"},
    )


# --------------------------------------------------------------------------- #
# 2. GET /api/dashboard/stats
# --------------------------------------------------------------------------- #
@router.get("/stats")
async def dashboard_stats(
    current_user: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN, Role.FACULTY))],
) -> dict:
    total_events = await events_collection().count_documents({})
    status_results = await events_collection().aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}]).to_list(None)
    by_status = {r["_id"]: r["count"] for r in status_results if r["_id"]}
    for s in EventStatus:
        by_status.setdefault(s.value, 0)

    naac_results = await events_collection().aggregate([{"$group": {"_id": "$naacCriterion", "count": {"$sum": 1}}}]).to_list(None)
    by_naac = {r["_id"]: r["count"] for r in naac_results if r["_id"]}

    club_results = await events_collection().aggregate([
        {"$match": {"organizingClub": {"$ne": None}}},
        {"$group": {"_id": "$organizingClub", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]).to_list(None)
    by_club = {r["_id"]: r["count"] for r in club_results if r["_id"]}

    reg_result = await events_collection().aggregate([
        {"$project": {"count": {"$size": {"$ifNull": ["$registeredStudents", []]}}}},
        {"$group": {"_id": None, "total": {"$sum": "$count"}}},
    ]).to_list(1)
    total_registrations = reg_result[0]["total"] if reg_result else 0

    return {
        "totalEvents": total_events,
        "totalRegistrations": total_registrations,
        "byStatus": by_status,
        "byNaacCriterion": by_naac,
        "byClub": by_club,
    }


# --------------------------------------------------------------------------- #
# 3. GET /api/dashboard/vetting (External Guest Queue)
# --------------------------------------------------------------------------- #
@router.get("/vetting")
async def external_guest_vetting(
    current_user: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN))],
) -> List[dict]:
    pipeline = [
        {
            "$match": {
                "status": EventStatus.PENDING_APPROVAL.value,
                "guestList": {"$exists": True, "$not": {"$size": 0}}
            }
        },
        {"$sort": {"startTime": 1}}
    ]
    cursor = events_collection().aggregate(pipeline)
    events = await cursor.to_list(length=100)
    return [{**e, "id": str(e["_id"]), "_id": str(e["_id"])} for e in events]


# --------------------------------------------------------------------------- #
# 4. GET /api/dashboard/analytics (FOR CHARTS)
# --------------------------------------------------------------------------- #
@router.get("/analytics")
async def get_analytics(
    current_user: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN, Role.FACULTY))],
) -> dict:
    venue_pipeline = [
        {"$match": {"status": {"$in": [EventStatus.APPROVED.value, EventStatus.COMPLETED.value]}}},
        {"$group": {"_id": "$venueName", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    venues = await events_collection().aggregate(venue_pipeline).to_list(None)
    venue_data = [{"name": v["_id"] or "Unknown", "events": v["count"]} for v in venues]

    dept_pipeline = [
        {"$match": {"status": {"$in": [EventStatus.APPROVED.value, EventStatus.COMPLETED.value]}}},
        {"$unwind": "$registeredStudents"},
        {
            "$lookup": {
                "from": "users",
                "localField": "registeredStudents",
                "foreignField": "_id",
                "as": "student"
            }
        },
        {"$unwind": "$student"},
        {"$group": {"_id": "$student.department", "value": {"$sum": 1}}},
        {"$sort": {"value": -1}}
    ]
    depts = await events_collection().aggregate(dept_pipeline).to_list(None)
    dept_data = [{"name": d["_id"] or "Unknown", "value": d["value"]} for d in depts]

    return {
        "venueUsage": venue_data,
        "departmentEngagement": dept_data
    }


# --------------------------------------------------------------------------- #
# 5. GET /api/dashboard/audit-logs
# --------------------------------------------------------------------------- #
@router.get("/audit-logs")
async def get_audit_logs(
    current_user: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN))],
    limit: int = Query(default=50, le=100)
) -> List[dict]:
    cursor = audit_logs_collection().find({}).sort("createdAt", -1).limit(limit)
    logs = await cursor.to_list(length=limit)
    return [{**log, "id": str(log["_id"]), "_id": str(log["_id"])} for log in logs]


# --------------------------------------------------------------------------- #
# 6. GET /api/dashboard/naac-export
# --------------------------------------------------------------------------- #
@router.get("/naac-export")
async def naac_export(
    current_user: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN, Role.FACULTY))],
) -> StreamingResponse:
    cursor = events_collection().find(
        {"status": {"$in": [EventStatus.APPROVED.value, EventStatus.COMPLETED.value]}}
    ).sort("startTime", -1)
    events = await cursor.to_list(length=10_000)

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")

    writer.writerow([
        "Event Title", "NAAC Criterion", "Organizing Club", "Faculty Incharge",
        "Venue", "Start Date", "End Date", "Status", "Total Registered Students",
    ])

    def _fmt(value) -> str:
        if value is None: return ""
        if isinstance(value, datetime): return value.isoformat()
        return str(value)

    for ev in events:
        writer.writerow([
            _fmt(ev.get("title")),
            _fmt(ev.get("naacCriterion")),
            _fmt(ev.get("organizingClub") or "—"),
            _fmt(ev.get("facultyIncharge") or ev.get("faculty_incharge") or "—"),
            _fmt(ev.get("venueName")),
            _fmt(ev.get("startTime")),
            _fmt(ev.get("endTime")),
            _fmt(ev.get("status")),
            len(ev.get("registeredStudents") or []),
        ])

    content = buf.getvalue().encode("utf-8-sig")

    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="naac_compliance_report.csv"', "Cache-Control": "no-store"},
    )


# --------------------------------------------------------------------------- #
# 7. GET /api/dashboard/venues
# --------------------------------------------------------------------------- #
@router.get("/venues", response_model=List[str])
async def recommended_venues(current_user: Annotated[UserInDB, Depends(get_current_active_user)]) -> List[str]:
    cursor = venues_collection().find({}, {"name": 1, "_id": 0}).sort("name", 1)
    docs = await cursor.to_list(length=1000)
    return [d["name"] for d in docs if d.get("name")]


# --------------------------------------------------------------------------- #
# 8. GET /api/dashboard/clubs
# --------------------------------------------------------------------------- #
@router.get("/clubs", response_model=List[str])
async def available_clubs(current_user: Annotated[UserInDB, Depends(get_current_active_user)]) -> List[str]:
    user_clubs = await users_collection().distinct("club")
    event_clubs = await events_collection().distinct("organizingClub")
    
    all_clubs = set()
    if user_clubs: all_clubs.update([c for c in user_clubs if c])
    if event_clubs: all_clubs.update([c for c in event_clubs if c])
    
    return sorted(list(all_clubs))


# --------------------------------------------------------------------------- #
# 9. GET /api/dashboard/metadata (NEW)
# --------------------------------------------------------------------------- #
@router.get("/metadata")
async def get_metadata(
    current_user: Annotated[UserInDB, Depends(get_current_active_user)],
) -> dict:
    """Provides the exact Enums from the backend for frontend dropdowns."""
    return {
        "clubs": [c.value for c in Club],
        "departments": [d.value for d in Department],
        "naacCriteria": [n.value for n in NAACCriterion]
    }


__all__ = ["router"]