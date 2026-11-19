from datetime import datetime
from typing import Iterable, Optional, List
from app.database import events_collection
from app.models.base import to_object_id
from app.models.conflict import ConflictInfo

def _intersects(a: Iterable, b: Iterable) -> bool:
    """
    Checks for intersection between two lists.
    Empty list = wildcard (matches anything).
    """
    a_set, b_set = set(a or []), set(b or [])
    if not a_set or not b_set:
        return True
    return bool(a_set & b_set)

def _student_id_candidates(student_id: str) -> list:
    ids: list = [student_id]
    try:
        ids.append(to_object_id(student_id))
    except ValueError:
        pass
    return ids

async def find_event_creation_conflicts(
    *,
    start_time: datetime,
    end_time: datetime,
    target_years: List[int],
    target_courses: List[str],
    exclude_event_id: Optional[str] = None,
) -> List[ConflictInfo]:
    """
    Rule 2: The Cohort Schedule Clash.
    Finds conflicts for creating/updating an event.
    Only checks against existing events that are EITHER Compulsory OR require Registration.
    """
    query: dict = {
        "$or": [
            {"isCompulsory": True},
            {"requiresRegistration": True}
        ],
        "startTime": {"$lt": end_time},
        "endTime": {"$gt": start_time},
    }
    if exclude_event_id:
        query["_id"] = {"$ne": to_object_id(exclude_event_id)}

    conflicts: list[ConflictInfo] = []
    async for doc in events_collection().find(query):
        # Check for intersection in BOTH years and courses
        years_intersect = _intersects(doc.get("targetYears", []), target_years)
        courses_intersect = _intersects(doc.get("targetCourses", []), target_courses)
        
        # If either years or courses do not intersect, it is a VALID CONCURRENCY (no clash).
        # Example: Yr 1&4 (CSE) vs Yr 2&3 (CSE) -> years_intersect is False -> Allowed.
        if not years_intersect or not courses_intersect:
            continue
            
        conflicts.append(
            ConflictInfo(
                id=str(doc["_id"]),
                title=doc.get("title", ""),
                startTime=doc["startTime"],
                endTime=doc["endTime"],
                reason="cohort_schedule_overlap",
            )
        )
    return conflicts

async def find_student_registration_conflicts(
    *,
    student_id: str,
    cohort_year: Optional[int],
    cohort_course: Optional[str],
    start_time: datetime,
    end_time: datetime,
    exclude_event_id: str,
) -> List[ConflictInfo]:
    """
    Rule 3: The Individual Student Clash.
    """
    time_clause = {
        "startTime": {"$lt": end_time},
        "endTime": {"$gt": start_time},
    }
    exclude_clause = {"_id": {"$ne": to_object_id(exclude_event_id)}}

    # 1) Already-registered overlap
    registered_query = {
        **time_clause,
        **exclude_clause,
        "registeredStudents": {"$in": _student_id_candidates(student_id)},
    }

    # 2) Compulsory-for-cohort overlap
    year_or = [{"targetYears": {"$size": 0}}]
    if cohort_year:
        year_or.append({"targetYears": cohort_year})
    
    course_or = [{"targetCourses": {"$size": 0}}]
    if cohort_course:
        course_or.append({"targetCourses": cohort_course})

    compulsory_query = {
        **time_clause,
        **exclude_clause,
        "isCompulsory": True,
        "$and": [{"$or": year_or}, {"$or": course_or}],
    }

    seen = set()
    conflicts: list[ConflictInfo] = []

    for q in (registered_query, compulsory_query):
        async for doc in events_collection().find(q):
            _id = str(doc["_id"])
            if _id in seen:
                continue
            seen.add(_id)
            conflicts.append(
                ConflictInfo(
                    id=_id,
                    title=doc.get("title", ""),
                    startTime=doc["startTime"],
                    endTime=doc["endTime"],
                    reason="student_overlap",
                )
            )
    return conflicts