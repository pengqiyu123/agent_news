"""FastAPI application entry point.

Run locally:
    python -m uvicorn agent_news.main:app --host 127.0.0.1 --port 8000

The FastAPI server is the PRIMARY runtime: it owns the persistent BrowserManager
singleton + worker thread, so the WeChat browser stays open across API calls.
CLI commands are thin HTTP clients to this server (see agent_news/cli.py).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import __version__
from .browser import BROWSER_MANAGER
from .config import get_settings
from .routes import articles, audit, health, intel, operations, workflows


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-warm the BrowserManager worker thread on startup, shut down on exit.

    The browser itself launches lazily on the first with_session call, but the
    worker thread (required for Playwright thread affinity) starts now so the
    first operation is fast. Modeled on old project's FastAPI lifespan pattern.
    """
    BROWSER_MANAGER.startup()
    try:
        yield
    finally:
        BROWSER_MANAGER.shutdown()


def create_app() -> FastAPI:
    """Build the FastAPI app. Kept as a factory so tests can spin up isolated apps."""
    settings = get_settings()
    settings.ensure_runtime_dirs()

    app = FastAPI(
        title="agent-news",
        description=(
            "Fully agent-controlled news publishing architecture. "
            "Information radar (collect→cluster→score→deep-dive) + WeChat publish, "
            "every step an AI-selectable atomic operation."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(articles.router)
    app.include_router(workflows.router)
    app.include_router(intel.router)
    app.include_router(operations.router)
    app.include_router(audit.router)

    return app


app = create_app()
