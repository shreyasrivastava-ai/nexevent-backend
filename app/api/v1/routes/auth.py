"""Authentication routes: login and token generation."""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import get_current_active_user
from app.core.security import create_access_token, verify_password
from app.database import users_collection
from app.models.user import UserInDB, UserResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login")
async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    """
    OAuth2 compatible token login, get an access token for future requests.
    Use the `erp` field as the username.
    """
    user_dict = await users_collection().find_one({"erp": form_data.username.upper()})
    if not user_dict:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect ERP or password",
        )

    user = UserInDB.model_validate(user_dict)
    if not verify_password(form_data.password, user.hashedPassword):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect ERP or password",
        )

    if not user.isActive:
        raise HTTPException(status_code=400, detail="Inactive user")

    return {
        "access_token": create_access_token(user.id),
        "token_type": "bearer",
    }


@router.get("/me", response_model=UserResponse)
async def read_current_user(
    current_user: Annotated[UserInDB, Depends(get_current_active_user)],
):
    """Get the current logged-in user's profile."""
    return current_user