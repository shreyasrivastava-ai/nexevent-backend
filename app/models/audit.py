# app/models/audit.py
from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from app.models.base import utc_now

class AuditLogCreate(BaseModel):
    actorId: str
    actorName: str
    action: str
    targetType: str
    targetId: str
    details: Optional[str] = None

class AuditLogInDB(AuditLogCreate):
    id: Optional[str] = Field(default=None, alias="_id")
    createdAt: datetime = Field(default_factory=utc_now)