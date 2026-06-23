"""Health endpoint — the minimal "is it alive" surface."""

from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..config import get_settings

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    """Liveness probe. Returns ok if the process is up and config loaded."""
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "data_dir": str(settings.data_dir),
        "database_url": settings.database_url,
    }
