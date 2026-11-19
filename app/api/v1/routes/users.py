"""User management routes."""

from typing import Annotated, List
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_active_user, require_roles
from app.core.security import hash_password
from app.database import users_collection
from app.models.enums import Role
from app.models.user import UserCreate, UserInDB, UserResponse

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_in: UserCreate,
    _: Annotated[UserInDB, Depends(require_roles([Role.SUPER_DEAN]))],
):
    """Create a new user. Only SUPER_DEAN can provision new users."""
    existing = await users_collection().find_one({"erp": user_in.erp})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User with ERP '{user_in.erp}' already exists.",
        )

    if user_in.email:
        existing_email = await users_collection().find_one({"email": user_in.email})
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"User with email '{user_in.email}' already exists.",
            )

    user_doc = user_in.model_dump(by_alias=True, exclude={"password"})
    user_doc["hashedPassword"] = hash_password(user_in.password)
    
    result = await users_collection().insert_one(user_doc)
    created_user = await users_collection().find_one({"_id": result.inserted_id})
    
    return UserResponse.model_validate(created_user)


@router.get("/", response_model=List[UserResponse])
async def list_users(
    _: Annotated[UserInDB, Depends(require_roles([Role.SUPER_DEAN, Role.FACULTY]))],
):
    """List all users. Accessible to SUPER_DEAN and FACULTY."""
    cursor = users_collection().find({})
    users = await cursor.to_list(length=100)
    return [UserResponse.model_validate(u) for u in users]


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    _: Annotated[UserInDB, Depends(get_current_active_user)],
):
    """Get a specific user by ID."""
    user = await users_collection().find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(user)