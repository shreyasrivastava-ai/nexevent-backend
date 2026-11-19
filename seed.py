"""
Database Seeder for NexEvent — Updated for dynamic venue architecture.

Usage:
    python seed.py
"""

import asyncio
import logging
from datetime import datetime, timezone

import bcrypt

from app.database import (
    EVENTS_COLLECTION,
    NOTIFICATIONS_COLLECTION,
    REGISTRATIONS_COLLECTION,
    USERS_COLLECTION,
    VENUES_COLLECTION,
    close_mongo_connection,
    connect_to_mongo,
    create_indexes,
    get_database,
)
from app.models.enums import Club, Department, Role
from app.models.user import UserInDB
from app.models.venue import VenueInDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("nexevent.seed")

DEFAULT_PASSWORD = "NexEvent@2026"
BCRYPT_COST_FACTOR = 12


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_COST_FACTOR)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def get_seed_users() -> list[dict]:
    """Returns the exact list of users to seed, validated via Pydantic."""
    hashed_pw = hash_password(DEFAULT_PASSWORD)
    now = datetime.now(timezone.utc)

    raw_users = [
        {"name": "Shreya Srivastava", "email": "shreyacoding0607@gmail.com", "erp": "53290", "department": Department.CSE_CORE, "role": Role.SUPER_DEAN},
        {"name": "Gauri Mishra", "email": "archeyparth@gmail.com", "erp": "53242", "department": Department.CSE_CORE, "role": Role.FACULTY},
        {"name": "Akshara Singh", "email": "aksharasingh6294@gmail.com", "erp": "51714", "department": Department.CSE_DS_AI, "role": Role.FACULTY},
        {"name": "Prachi singh", "rollNumber": 202511201120043, "erp": "53122", "department": Department.BSC_HONS_AGRICULTURE, "club": Club.OZONE, "year": 2, "role": Role.CLUB_LEAD},
        {"name": "Ansh Pratap Singh", "rollNumber": 202510101150206, "erp": "47372", "department": Department.CSE_DS_AI, "club": Club.TECH_FUSION, "year": 2, "role": Role.CLUB_LEAD},
        {"name": "Rijul rai", "rollNumber": 202511201120018, "erp": "52188", "department": Department.BSC_HONS_AGRICULTURE, "year": 2, "role": Role.STUDENT},
        {"name": "Arpita Singh", "rollNumber": 202510101150105, "erp": "51715", "department": Department.CSE_DS_AI, "year": 3, "role": Role.STUDENT},
        {"name": "Tanu Priya", "rollNumber": 202510101240036, "erp": "48133", "department": Department.CSE_AI_ML, "year": 3, "role": Role.STUDENT},
        {"name": "Hanshika Lakhan", "rollNumber": 202510101240033, "erp": "48732", "department": Department.CSE_AI_ML, "year": 1, "role": Role.STUDENT},
        {"name": "Shiv kumar bhardwaj", "rollNumber": 202510101150134, "erp": "47660", "department": Department.CSE_DS_AI, "year": 3, "role": Role.STUDENT},
        {"name": "Arnav Mishra", "rollNumber": 202510101150205, "erp": "51507", "department": Department.IT, "year": 2, "role": Role.STUDENT},
        {"name": "Anubhav singh", "rollNumber": 202510101150157, "erp": "48764", "department": Department.CSE_DS_AI, "year": 4, "role": Role.STUDENT},
        {"name": "Kriti Sachan", "rollNumber": 202510101110008, "erp": "47557", "department": Department.CSE_CORE, "year": 1, "role": Role.STUDENT},
        {"name": "Harshita Ojha", "rollNumber": 202510101240085, "erp": "52252", "department": Department.BBA, "year": 1, "role": Role.STUDENT},
        {"name": "Kanak Pandey", "rollNumber": 202510101150210, "erp": "48524", "department": Department.ME, "year": 4, "role": Role.STUDENT},
        {"name": "Esha Singh Rathaur", "rollNumber": 202510101110060, "erp": "47887", "department": Department.CSE_CORE, "year": 2, "role": Role.STUDENT},
        {"name": "Atharva Pandey", "rollNumber": 202510101150012, "erp": "48925", "department": Department.LLB, "year": 2, "role": Role.STUDENT},
        {"name": "Achint", "rollNumber": 202510101150183, "erp": "52771", "department": Department.BCOM_HONS, "year": 3, "role": Role.STUDENT},
    ]

    validated_users = []
    for user_data in raw_users:
        user_data["hashedPassword"] = hashed_pw
        user_data["isActive"] = True
        user_data["createdAt"] = now
        user_data["updatedAt"] = now
        user = UserInDB.model_validate(user_data)
        doc = user.model_dump(by_alias=True, exclude_none=True)
        doc["hashedPassword"] = user.hashedPassword
        validated_users.append(doc)
    return validated_users


def get_seed_venues() -> list[dict]:
    """Returns the 3 default venue recommendation tags."""
    now = datetime.now(timezone.utc)
    raw_venues = [
        {"name": "Main Auditorium"},
        {"name": "Seminar Hall A"},
        {"name": "Open Air Theatre"},
    ]
    validated_venues = []
    for venue_data in raw_venues:
        venue_data["createdAt"] = now
        venue_data["updatedAt"] = now
        venue = VenueInDB.model_validate(venue_data)
        doc = venue.model_dump(by_alias=True, exclude_none=True)
        validated_venues.append(doc)
    return validated_venues


async def seed_database() -> None:
    logger.info("Connecting to MongoDB...")
    await connect_to_mongo()
    db = get_database()

    logger.info("Dropping existing collections...")
    for collection_name in [
        USERS_COLLECTION,
        EVENTS_COLLECTION,
        VENUES_COLLECTION,
        NOTIFICATIONS_COLLECTION,
        REGISTRATIONS_COLLECTION,
    ]:
        await db.drop_collection(collection_name)
        logger.info(f"  Dropped: {collection_name}")

    logger.info("Rebuilding indexes...")
    await create_indexes()

    logger.info("Seeding users...")
    users = get_seed_users()
    if users:
        result = await db[USERS_COLLECTION].insert_many(users)
        logger.info(f"  Inserted {len(result.inserted_ids)} users.")

    logger.info("Seeding venues...")
    venues = get_seed_venues()
    if venues:
        result = await db[VENUES_COLLECTION].insert_many(venues)
        logger.info(f"  Inserted {len(result.inserted_ids)} venues.")

    await close_mongo_connection()
    logger.info("✅ Database seeding completed successfully!")


if __name__ == "__main__":
    asyncio.run(seed_database())