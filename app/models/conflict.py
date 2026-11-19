from datetime import datetime
from pydantic import BaseModel

class ConflictInfo(BaseModel):
    id: str
    title: str
    startTime: datetime
    endTime: datetime
    reason: str

class ConflictResponse(BaseModel):
    detail: str
    conflicts: list[ConflictInfo]