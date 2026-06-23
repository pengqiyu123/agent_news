"""Pytest configuration — isolate each test run under a temp data dir.

We point AGENT_NEWS_DATA_DIR at a tmp_path before importing app code, so tests
never touch the real on-disk database. This mirrors the old project's habit of
keeping test state separate from runtime state.
"""

from __future__ import annotations

import os
from pathlib import Path


def pytest_configure(config):  # noqa: D401 — pytest hook
    """Set env to a temp location before any test module imports the app."""
    tmp_data = Path(os.environ.get("AGENT_NEWS_TEST_TMP", Path(__file__).resolve().parent / "_test_data"))
    tmp_data.mkdir(parents=True, exist_ok=True)
    os.environ["AGENT_NEWS_DATA_DIR"] = str(tmp_data)

    # Reset cached settings/engine/repository so they pick up the new data dir.
    for mod_path in (
        "agent_news.config",
        "agent_news.db.engine",
        "agent_news.db.repository",
    ):
        try:
            import sys
            if mod_path in sys.modules:
                mod = sys.modules[mod_path]
                # Clear the lru_cache / module singletons.
                for attr in ("get_settings",):
                    if hasattr(mod, attr):
                        getattr(mod, attr).cache_clear()
                for attr in ("_engine", "_SessionLocal", "_repository"):
                    if hasattr(mod, attr):
                        setattr(mod, attr, None)
        except ImportError:
            pass
