"""
NexEvent — Enterprise Institutional Event Management Platform.

Backend package root. Exposes the application factory, package metadata and a
lazily-resolved version string so that importing `app` never triggers the
expensive side-effects of building the FastAPI instance (config parsing, Motor
client construction, index creation, etc.).

Import the ASGI callable for deployment as:

    uvicorn app:create_app --factory --host 0.0.0.0 --port 8000

or, if you already expose a module-level `app` in `app/main.py`:

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

# --------------------------------------------------------------------------- #
# Package metadata
# --------------------------------------------------------------------------- #
__title__ = "NexEvent"
__summary__ = "Enterprise institutional event management platform API."
__version__ = "1.0.0"
__author__ = "NexEvent Platform Team"
__license__ = "Proprietary"
__all__ = [
    "__title__",
    "__summary__",
    "__version__",
    "__author__",
    "__license__",
    "create_app",
]

# --------------------------------------------------------------------------- #
# Logging bootstrap
# --------------------------------------------------------------------------- #
# Configure a package-scoped logger *without* touching the root logger. The
# final handler/formatter wiring belongs to the ASGI server (uvicorn) or the
# process supervisor, which will attach handlers to the root logger. Setting a
# NullHandler here prevents the classic "No handlers could be found" warning
# when the package is imported inside a bare interpreter or a test harness.
logging.getLogger(__name__).addHandler(logging.NullHandler())

# --------------------------------------------------------------------------- #
# Type-checker-only imports
# --------------------------------------------------------------------------- #
# `FastAPI` is imported under TYPE_CHECKING purely for the return annotation of
# `create_app`. At runtime the actual import happens lazily inside the factory,
# keeping `import app` cheap and side-effect-free.
if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI


# --------------------------------------------------------------------------- #
# Application factory
# --------------------------------------------------------------------------- #
def create_app(**overrides: Any) -> "FastAPI":
    """
    Build and return a fully-configured FastAPI application.

    This is a thin, side-effect-free delegate: all wiring (routers, middleware,
    exception handlers, lifespan hooks) lives in :mod:`app.main`. Keeping the
    factory here — rather than importing `app.main` eagerly — means that
    `import app` stays fast, and callers such as Alembic-style migration tools
    or CLI utilities can pull package metadata without instantiating the ASGI
    app.

    Parameters
    ----------
    **overrides:
        Reserved for test-time overrides (e.g. a pre-built `FastAPI` instance
        or custom settings). Currently forwarded verbatim to the underlying
        builder so that future signatures stay backwards-compatible.

    Returns
    -------
    FastAPI
        The ASGI application instance.

    Raises
    ------
    RuntimeError
        If the application module fails to expose a builder. This indicates a
        packaging error rather than a user error.
    """
    # Imported lazily so that `import app` does not pull in FastAPI, Motor,
    # Pydantic-settings, or trigger `.env` parsing.
    from app.main import create_app as _create_app  # noqa: PLC0415

    return _create_app(**overrides)


__all__ += ["__version__"]