"""FastAPI dependencies for authentication and authorization."""

from app.models.base import to_object_id
from typing import Annotated, List
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import ValidationError

from app.config import get_settings
from app.database import users_collection
from app.models.enums import Role
from app.models.user import UserInDB

settings = get_settings()

# This tells FastAPI where the login endpoint is
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> UserInDB:
    """Decode the JWT and fetch the user from MongoDB."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except (JWTError, ValidationError):
        raise credentials_exception

    # --- THE FIX: Convert the string ID to a MongoDB ObjectId ---
    try:
        oid = to_object_id(user_id)
    except ValueError:
        raise credentials_exception

    user = await users_collection().find_one({"_id": oid})
    if user is None:
        raise credentials_exception
    
    return UserInDB.model_validate(user)


async def get_current_active_user(
    current_user: Annotated[UserInDB, Depends(get_current_user)],
) -> UserInDB:
    """Ensure the user account is active."""
    if not current_user.isActive:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def require_roles(allowed_roles: List[Role]):
    """Dependency factory to restrict endpoints to specific roles."""
    async def role_checker(
        current_user: Annotated[UserInDB, Depends(get_current_active_user)],
    ) -> UserInDB:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation not permitted for role {current_user.role.value}",
            )
        return current_user
    return role_checker