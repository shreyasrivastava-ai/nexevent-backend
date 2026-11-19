from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from app.config import get_settings
from app.database import close_mongo_connection, connect_to_mongo, ping
from app.core.rate_limit import limiter

# Legacy /api/v1 routers
from app.api.v1.routes import auth, users

# Domain routers
from app.routers import (
    auth_routes,
    dashboard_routes,
    event_routes,
    notification_routes,
    user_routes,
    media_routes,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_to_mongo()
    yield
    await close_mongo_connection()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)

# --- SlowAPI Rate Limiting Setup ---
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Health Check Endpoint ---
@app.get("/health", tags=["Health"])
async def health_check():
    """Zero-downtime health check for Render."""
    is_healthy = await ping()
    if is_healthy:
        return {"status": "healthy", "database": "connected"}
    return JSONResponse(
        status_code=503, 
        content={"status": "unhealthy", "database": "disconnected"}
    )

# --------------------------------------------------------------------------- #
# Legacy /api/v1 routes
# --------------------------------------------------------------------------- #
app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
app.include_router(users.router, prefix=settings.API_V1_PREFIX)

# --------------------------------------------------------------------------- #
# New routers — absolute prefixes, no API_V1_PREFIX
# --------------------------------------------------------------------------- #
app.include_router(auth_routes.router)          # /api/auth/*
app.include_router(user_routes.router)          # /api/users/*
app.include_router(notification_routes.router)  # /api/notifications/*
app.include_router(event_routes.router)         # /api/events/*
app.include_router(media_routes.router)         # /api/media/*

# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
app.include_router(dashboard_routes.router)  # /api/dashboard/* — absolute prefix