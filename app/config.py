"""
Centralised, type-safe application configuration for NexEvent.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Immutable runtime settings object."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Application
    # ------------------------------------------------------------------ #
    APP_NAME: str = "NexEvent API"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    PORT: int = Field(default=8000, ge=1, le=65535)
    FRONTEND_BASE_URL: str = "http://localhost:5173"

    # ------------------------------------------------------------------ #
    # MongoDB
    # ------------------------------------------------------------------ #
    MONGO_URI: str = Field(
        ...,
        description="mongodb+srv://<user>:<pass>@<cluster>/<db>?retryWrites=true&w=majority",
    )
    MONGO_DB_NAME: str = "nexevent"
    MONGO_MAX_POOL_SIZE: int = Field(default=100, ge=1)
    MONGO_MIN_POOL_SIZE: int = Field(default=0, ge=0)
    MONGO_SERVER_SELECTION_TIMEOUT_MS: int = Field(default=5000, ge=500)

    # ------------------------------------------------------------------ #
    # JWT
    # ------------------------------------------------------------------ #
    JWT_SECRET: str = Field(..., min_length=32)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = Field(default=1440, ge=1)

    # ------------------------------------------------------------------ #
    # CORS
    # ------------------------------------------------------------------ #
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # ------------------------------------------------------------------ #
    # Cloudinary
    # ------------------------------------------------------------------ #
    CLOUDINARY_CLOUD_NAME: str = Field(default="")
    CLOUDINARY_API_KEY: str = Field(default="")
    CLOUDINARY_API_SECRET: str = Field(default="")

    # ------------------------------------------------------------------ #
    # SMTP (Email)
    # ------------------------------------------------------------------ #
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = Field(default="")
    SMTP_PASSWORD: str = Field(default="")
    SMTP_FROM: str = Field(default="noreply@nexevent.com")
    SMTP_FROM_NAME: str = "NexEvent"

    # ------------------------------------------------------------------ #
    # Validators & Helpers
    # ------------------------------------------------------------------ #
    @field_validator("JWT_SECRET")
    @classmethod
    def _validate_jwt_secret(cls, value: str) -> str:
        if value.strip().lower() in {"secret", "changeme", "jwt_secret", "password"}:
            raise ValueError("JWT_SECRET is trivially guessable.")
        return value

    @field_validator("MONGO_URI")
    @classmethod
    def _validate_mongo_uri(cls, value: str) -> str:
        if not value.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("MONGO_URI must start with 'mongodb://' or 'mongodb+srv://'.")
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def jwt_expire_seconds(self) -> int:
        return self.JWT_EXPIRE_MINUTES * 60

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

settings: Settings = get_settings()