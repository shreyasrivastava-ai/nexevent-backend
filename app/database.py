"""
Async MongoDB connectivity for NexEvent.
"""
from __future__ import annotations

import logging
from typing import Optional

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)
from pymongo import ASCENDING, DESCENDING, IndexModel, TEXT
from pymongo.collation import Collation
from pymongo.errors import CollectionInvalid, OperationFailure, PyMongoError

from app.config import get_settings

logger = logging.getLogger("nexevent.database")

USERS_COLLECTION = "users"
EVENTS_COLLECTION = "events"
VENUES_COLLECTION = "venues"
NOTIFICATIONS_COLLECTION = "notifications"
REGISTRATIONS_COLLECTION = "registrations"
AUDIT_LOGS_COLLECTION = "audit_logs"

CI_COLLATION = Collation(locale="en", strength=2)

_client: Optional[AsyncIOMotorClient] = None
_database: Optional[AsyncIOMotorDatabase] = None


async def connect_to_mongo() -> AsyncIOMotorDatabase:
    global _client, _database

    if _database is not None:
        return _database

    settings = get_settings()

    _client = AsyncIOMotorClient(
        settings.MONGO_URI,
        tz_aware=True,
        uuidRepresentation="standard",
        maxPoolSize=settings.MONGO_MAX_POOL_SIZE,
        minPoolSize=settings.MONGO_MIN_POOL_SIZE,
        serverSelectionTimeoutMS=settings.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        retryWrites=True,
        appname=f"{settings.APP_NAME}/{settings.APP_ENV}",
    )

    _database = _client[settings.MONGO_DB_NAME]

    await _client.admin.command("ping")
    logger.info("Connected to MongoDB database '%s'.", settings.MONGO_DB_NAME)

    await create_indexes()
    return _database


async def close_mongo_connection() -> None:
    global _client, _database
    if _client is not None:
        _client.close()
        logger.info("MongoDB connection closed.")
    _client = None
    _database = None


def get_database() -> AsyncIOMotorDatabase:
    if _database is None:
        raise RuntimeError("MongoDB is not initialised. Ensure `connect_to_mongo()` runs.")
    return _database


def get_client() -> AsyncIOMotorClient:
    if _client is None:
        raise RuntimeError("MongoDB client is not initialised.")
    return _client


async def get_db() -> AsyncIOMotorDatabase:
    return get_database()


def get_collection(name: str) -> AsyncIOMotorCollection:
    return get_database()[name]


def users_collection() -> AsyncIOMotorCollection:
    return get_collection(USERS_COLLECTION)


def events_collection() -> AsyncIOMotorCollection:
    return get_collection(EVENTS_COLLECTION)


def venues_collection() -> AsyncIOMotorCollection:
    return get_collection(VENUES_COLLECTION)


def notifications_collection() -> AsyncIOMotorCollection:
    return get_collection(NOTIFICATIONS_COLLECTION)


def registrations_collection() -> AsyncIOMotorCollection:
    return get_collection(REGISTRATIONS_COLLECTION)


def audit_logs_collection() -> AsyncIOMotorCollection:
    return get_collection(AUDIT_LOGS_COLLECTION)


async def _safe_create_indexes(
    collection: AsyncIOMotorCollection,
    indexes: list[IndexModel],
) -> None:
    try:
        names = await collection.create_indexes(indexes)
        logger.info("Ensured %d index(es) on '%s': %s", len(names), collection.name, names)
    except OperationFailure as exc:
        if exc.code in (85, 86):
            logger.error("IndexOptionsConflict on collection '%s'. Details: %s", collection.name, exc.details)
            return
        raise
    except CollectionInvalid:
        logger.warning("Collection '%s' could not be created; skipping indexes.", collection.name)
    except PyMongoError:
        logger.exception("Failed to create indexes on '%s'.", collection.name)
        raise


async def create_indexes() -> None:
    db = get_database()

    await _safe_create_indexes(
        db[USERS_COLLECTION],
        [
            IndexModel([("erp", ASCENDING)], name="uniq_erp", unique=True, partialFilterExpression={"erp": {"$type": "string"}}),
            IndexModel([("email", ASCENDING)], name="uniq_email", unique=True, collation=CI_COLLATION, partialFilterExpression={"email": {"$type": "string"}}),
            IndexModel([("rollNumber", ASCENDING)], name="uniq_roll_number", unique=True, partialFilterExpression={"rollNumber": {"$type": "number"}}),
            IndexModel([("role", ASCENDING), ("department", ASCENDING)], name="role_department"),
            IndexModel([("club", ASCENDING), ("role", ASCENDING)], name="club_role"),
            IndexModel([("isActive", ASCENDING)], name="is_active"),
        ],
    )

    await _safe_create_indexes(
        db[EVENTS_COLLECTION],
        [
            IndexModel([("status", ASCENDING), ("startTime", ASCENDING)], name="status_start_time"),
            IndexModel([("startTime", ASCENDING), ("endTime", ASCENDING)], name="time_window"),
            IndexModel([("venueName", ASCENDING), ("startTime", ASCENDING)], name="venue_start_time"),
            IndexModel([("createdBy", ASCENDING), ("status", ASCENDING)], name="creator_status"),
            IndexModel([("organizingClub", ASCENDING), ("startTime", DESCENDING)], name="club_start_time"),
            IndexModel([("naacCriterion", ASCENDING)], name="naac_criterion"),
            IndexModel([("targetDepartments", ASCENDING)], name="target_departments"),
            IndexModel([("targetYears", ASCENDING)], name="target_years"),
            IndexModel([("registeredStudents", ASCENDING)], name="registered_students"),
            IndexModel([("title", TEXT), ("description", TEXT)], name="event_full_text", weights={"title": 10, "description": 3}),
        ],
    )

    await _safe_create_indexes(
        db[VENUES_COLLECTION],
        [
            IndexModel([("name", ASCENDING)], name="uniq_venue_name", unique=True, collation=CI_COLLATION),
        ],
    )

    await _safe_create_indexes(
        db[NOTIFICATIONS_COLLECTION],
        [
            IndexModel([("userId", ASCENDING), ("isRead", ASCENDING), ("createdAt", DESCENDING)], name="user_inbox"),
            IndexModel([("relatedEventId", ASCENDING)], name="related_event"),
        ],
    )

    await _safe_create_indexes(
        db[REGISTRATIONS_COLLECTION],
        [
            IndexModel([("eventId", ASCENDING), ("studentId", ASCENDING)], name="uniq_event_student", unique=True),
            IndexModel([("studentId", ASCENDING), ("createdAt", DESCENDING)], name="student_history"),
            # NEW: Idempotency index for Google Webhooks
            IndexModel(
                [("idempotency_key", ASCENDING)], 
                name="uniq_idempotency_key", 
                unique=True, 
                sparse=True
            ),
        ],
    )
    
    await _safe_create_indexes(
        db[AUDIT_LOGS_COLLECTION],
        [
            IndexModel([("createdAt", DESCENDING)], name="audit_time"),
        ],
    )


async def ping() -> bool:
    try:
        await get_client().admin.command("ping")
        return True
    except PyMongoError:
        logger.exception("MongoDB ping failed.")
        return False