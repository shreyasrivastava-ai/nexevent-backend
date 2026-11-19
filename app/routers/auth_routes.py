"""
Secure Access Gateway routes.
"""


import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request, Response, Body
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from fastapi_mail import MessageSchema, MessageType

from app.database import users_collection
from app.middleware.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models.base import utc_now
from app.models.enums import STAFF_ROLES, STUDENT_ROLES, Department, Role
from app.models.user import UserCreate, UserResponse
from app.core.rate_limit import limiter
from app.services.mailer import fastmail
from app.config import get_settings

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
settings = get_settings()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: Optional[EmailStr] = None
    rollNumber: Optional[int] = Field(default=None, gt=0)
    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    token: str
    user: UserResponse
    requires_password_change: bool = False


class StudentRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=120)
    rollNumber: int = Field(..., gt=0)
    erp: str = Field(..., min_length=4, max_length=32)
    department: Department
    year: int = Field(..., ge=1, le=4)
    password: str = Field(..., min_length=8, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=20)

    @field_validator("erp", mode="before")
    @classmethod
    def _normalise_erp(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        if not any(c.isalpha() for c in value):
            raise ValueError("Password must contain at least one letter.")
        if not any(c.isdigit() for c in value):
            raise ValueError("Password must contain at least one digit.")
        return value


class ForgotPasswordRequest(BaseModel):
    identifier: str = Field(..., description="Email or Roll Number")


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Dual-portal login (Employee or Student)",
)
@limiter.limit("5/minute")
async def login(
    request: Request, 
    response: Response, 
    payload: LoginRequest = Body(...)  # <-- SURGICAL FIX: Explicitly declare Body
) -> LoginResponse:
    user: Optional[dict] = None

    if payload.email is not None:
        if payload.rollNumber is not None:
            raise HTTPException(status_code=400, detail="Provide either `email` or `rollNumber`, not both.")
        query = {"email": str(payload.email).lower().strip()}
        user = await users_collection().find_one(query)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        if user.get("role") not in {r.value for r in STAFF_ROLES}:
            raise HTTPException(status_code=403, detail="Email login is restricted to staff accounts.")

    elif payload.rollNumber is not None:
        user = await users_collection().find_one({"rollNumber": payload.rollNumber})
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid roll number or password.")
        if user.get("role") not in {r.value for r in STUDENT_ROLES}:
            raise HTTPException(status_code=403, detail="This portal is for students only.")

    else:
        raise HTTPException(status_code=400, detail="Provide `email` (staff) or `rollNumber` (student).")

    if not verify_password(payload.password, user.get("hashedPassword", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    if not user.get("isActive", True):
        raise HTTPException(status_code=403, detail="Account is inactive.")

    token = create_access_token(str(user["_id"]))
    user_response = UserResponse.model_validate(user)

    await users_collection().update_one(
        {"_id": user["_id"]},
        {"$set": {"lastLoginAt": utc_now(), "updatedAt": utc_now()}},
    )

    return LoginResponse(
        token=token, 
        user=user_response,
        requires_password_change=user.get("requires_password_change", False)
    )


@router.post(
    "/register/student",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Public student self-registration",
)
async def register_student(payload: StudentRegisterRequest) -> UserResponse:
    if await users_collection().find_one({"erp": payload.erp}):
        raise HTTPException(status_code=400, detail=f"An account with ERP '{payload.erp}' already exists.")
    if await users_collection().find_one({"rollNumber": payload.rollNumber}):
        raise HTTPException(status_code=400, detail=f"An account with roll number {payload.rollNumber} already exists.")

    try:
        user_in = UserCreate(
            name=payload.name,
            erp=payload.erp,
            role=Role.STUDENT,
            department=payload.department,
            year=payload.year,
            rollNumber=payload.rollNumber,
            email=None,
            phone=payload.phone,
            password=payload.password,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user_doc = user_in.model_dump(by_alias=True, exclude={"password"})
    user_doc["hashedPassword"] = hash_password(user_in.password)
    user_doc["isActive"] = True
    user_doc["requires_password_change"] = False
    now = utc_now()
    user_doc["createdAt"] = now
    user_doc["updatedAt"] = now

    result = await users_collection().insert_one(user_doc)
    created = await users_collection().find_one({"_id": result.inserted_id})
    if created is None:
        raise HTTPException(status_code=500, detail="User was created but could not be reloaded.")

    return UserResponse.model_validate(created)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Current authenticated user's profile",
)
async def get_me(current_user: dict = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def forgot_password(
    request: Request, 
    response: Response, 
    payload: ForgotPasswordRequest = Body(...)  # <-- SURGICAL FIX: Explicitly declare Body
):
    query = {
        "$or": [
            {"email": payload.identifier.lower()},
            {"rollNumber": int(payload.identifier) if payload.identifier.isdigit() else None}
        ]
    }
    if query["$or"][1]["rollNumber"] is None:
        del query["$or"][1]

    user = await users_collection().find_one(query)
    
    if not user:
        return {"message": "If an account exists, a reset link has been generated."}

    token = secrets.token_urlsafe(32)
    expires = utc_now() + timedelta(minutes=15)

    await users_collection().update_one(
        {"_id": user["_id"]},
        {"$set": {"reset_password_token": token, "reset_password_expires": expires}}
    )

    # LIVE SMTP EMAIL SENDING
    reset_link = f"{settings.FRONTEND_BASE_URL}/reset-password?token={token}"
    
    html_body = f"""
    <div style="font-family:sans-serif;max-width:520px;margin:auto">
      <h2 style="color:#1e293b">NexEvent Password Reset</h2>
      <p>Hello {user.get('name', 'User')},</p>
      <p>You requested a password reset. This link expires in <strong>15 minutes</strong>.</p>
      <p><a href="{reset_link}"
            style="display:inline-block;padding:12px 24px;background:#4f46e5;
                   color:#fff;border-radius:8px;text-decoration:none">
         Reset Password</a></p>
      <p style="color:#64748b;font-size:13px">
         If you did not request this, you can safely ignore this email.</p>
    </div>
    """

    try:
        message = MessageSchema(
            subject="NexEvent — Reset Your Password",
            recipients=[user["email"]] if user.get("email") else [],
            body=html_body,
            subtype=MessageType.html,
        )
        if user.get("email"):
            await fastmail.send_message(message)
    except Exception as e:
        print(f"SMTP Error: {e}")
        print(f"\n--- FALLBACK RESET LINK ---\n{reset_link}\n---------------------------\n")

    return {"message": "If an account exists, a reset link has been generated."}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(payload: ResetPasswordRequest):
    user = await users_collection().find_one({"reset_password_token": payload.token})
    
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token.")
        
    expires = user.get("reset_password_expires")
    if not expires or expires < utc_now():
        raise HTTPException(status_code=400, detail="Reset token has expired.")

    new_hash = hash_password(payload.new_password)

    await users_collection().update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "hashedPassword": new_hash,
                "requires_password_change": False,
                "reset_password_token": None,
                "reset_password_expires": None,
                "updatedAt": utc_now()
            }
        }
    )

    return {"message": "Password reset successfully. You can now log in."}


__all__ = ["router"]