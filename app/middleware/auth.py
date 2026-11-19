"""
Authentication middleware for NexEvent.

Provides:
  * Password hashing/verification using bcrypt directly (cost factor 12).
  * JWT creation and decoding using python-jose.
  * `get_current_user`         -> FastAPI dependency returning the raw user dict.
  * `get_current_active_user`  -> adds an `isActive` check.
  * `require_roles(*roles)`    -> dependency factory that returns a validated
                                  `UserInDB` instance so route handlers get
                                  typed attribute access (`user.id`, `user.role`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import get_settings
from app.database import users_collection
from app.models.base import to_object_id
from app.models.enums import Role
from app.models.user import UserInDB

settings = get_settings()

#: Extracts `Authorization: Bearer <token>`. auto_error=False lets us return a
#: custom 401 (with the WWW-Authenticate header) instead of the default 403.
security = HTTPBearer(auto_error=False)

BCRYPT_COST_FACTOR = 12


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """Return a bcrypt hash of `password` at cost factor 12."""
    salt = bcrypt.gensalt(rounds=BCRYPT_COST_FACTOR)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time verification. Never raises — returns False on bad input."""
    if not plain_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #
def create_access_token(
    subject: str | Any,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Mint a signed JWT.

    Payload:
      * `sub` : stringified user ObjectId (subject of the token).
      * `iat` : issued-at  (UTC).
      * `exp` : expiry     (UTC).
    """
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES))

    payload = {"sub": str(subject), "iat": now, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT. Raises a 401 HTTPException on any failure."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as exc:
        raise credentials_exception from exc


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #
async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """
    Decode the bearer token and load the corresponding MongoDB document.

    Returns the RAW user document (including `hashedPassword`) — routes that
    expose it externally MUST project it through `UserResponse`.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload missing subject",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # JWT stores the id as a string; MongoDB needs a real ObjectId.
    try:
        oid = to_object_id(str(user_id))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed subject in token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await users_collection().find_one({"_id": oid})
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_active_user(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Reject tokens belonging to de-activated accounts."""
    if not current_user.get("isActive", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )
    return current_user


def require_roles(*allowed_roles: Role):
    """
    Dependency factory: gate an endpoint on one or more roles.

    Returns the authenticated user as a validated `UserInDB` instance so
    route handlers get typed attribute access (`user.id`, `user.role`)
    instead of dict-style access.

    Usage:
        @router.post("/", dependencies=[Depends(require_roles(Role.SUPER_DEAN))])
        async def create_user(...): ...

        # or as an injected value (typed as UserInDB):
        async def create_user(
            actor: Annotated[UserInDB, Depends(require_roles(Role.SUPER_DEAN))]
        ): ...
    """
    if not allowed_roles:
        raise ValueError("require_roles() needs at least one Role.")

    async def _checker(
        current_user: dict = Depends(get_current_active_user),
    ) -> UserInDB:
        raw_role = current_user.get("role")
        try:
            user_role = Role(raw_role) if raw_role else None
        except ValueError:
            user_role = None

        if user_role is None or user_role not in allowed_roles:
            allowed = ", ".join(r.value for r in allowed_roles)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Requires one of: {allowed}.",
            )

        return UserInDB.model_validate(current_user)

    return _checker


__all__ = [
    "security",
    "hash_password",
    "verify_password",
    "create_access_token",
    "decode_access_token",
    "get_current_user",
    "get_current_active_user",
    "require_roles",
]