"""Application configuration.

All paths and tunables live here. Loaded from environment variables with
sensible defaults for local single-user deployment.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


# ── Project root ────────────────────────────────────────────────────────────
# pyproject.toml lives in <root>; this file is at <root>/agent_news/config.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Runtime directories ─────────────────────────────────────────────────────
DATA_DIR = Path(os.getenv("AGENT_NEWS_DATA_DIR", PROJECT_ROOT / "data"))
LOGS_DIR = Path(os.getenv("AGENT_NEWS_LOGS_DIR", PROJECT_ROOT / "logs"))
RUNTIME_DIR = Path(os.getenv("AGENT_NEWS_RUNTIME_DIR", PROJECT_ROOT / "runtime"))

DATABASE_PATH = DATA_DIR / "agent_news.db"
DATABASE_URL = os.getenv(
    "AGENT_NEWS_DATABASE_URL",
    f"sqlite:///{DATABASE_PATH}",
)

# Browser profile holds the WeChat login state; one profile per user.
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"

# ── Server ──────────────────────────────────────────────────────────────────
HOST = os.getenv("AGENT_NEWS_HOST", "127.0.0.1")
PORT = int(os.getenv("AGENT_NEWS_PORT", "8000"))

# ── Browser automation ──────────────────────────────────────────────────────
# Browser channel: "msedge" (default, matches the old project) or "chrome".
# Empty = Playwright's bundled Chromium.
BROWSER_CHANNEL = os.getenv("AGENT_NEWS_BROWSER_CHANNEL", "msedge")
# Browser executable path override. Empty = use channel default.
BROWSER_EXECUTABLE_PATH = os.getenv("AGENT_NEWS_BROWSER_PATH", "")
# Default WeChat MP selectors profile version.
DEFAULT_SELECTOR_VERSION = "wechat-mp-v1"

# ── Optional autoglm fallback ───────────────────────────────────────────────
AUTOG_FALLBACK_ENABLED = os.getenv("AGENT_NEWS_AUTOG_FALLBACK", "").lower() in ("1", "true", "yes")

# ── LLM (for agent loop, optional in phase 1) ──────────────────────────────
LLM_API_BASE = os.getenv("AGENT_NEWS_LLM_API_BASE", "")
LLM_API_KEY = os.getenv("AGENT_NEWS_LLM_API_KEY", "")
LLM_MODEL = os.getenv("AGENT_NEWS_LLM_MODEL", "")


class Settings:
    """Lazy settings bundle, exposed as a singleton via get_settings()."""

    def __init__(self) -> None:
        self.project_root = PROJECT_ROOT
        self.data_dir = DATA_DIR
        self.logs_dir = LOGS_DIR
        self.runtime_dir = RUNTIME_DIR
        self.database_path = DATABASE_PATH
        self.database_url = DATABASE_URL
        self.browser_profile_dir = BROWSER_PROFILE_DIR
        self.host = HOST
        self.port = PORT
        self.browser_executable_path = BROWSER_EXECUTABLE_PATH
        self.browser_channel = BROWSER_CHANNEL
        self.default_selector_version = DEFAULT_SELECTOR_VERSION
        self.autog_fallback_enabled = AUTOG_FALLBACK_ENABLED
        self.llm_api_base = LLM_API_BASE
        self.llm_api_key = LLM_API_KEY
        self.llm_model = LLM_MODEL

    def ensure_runtime_dirs(self) -> None:
        """Create runtime directories if missing. Safe to call repeatedly."""
        for d in (self.data_dir, self.logs_dir, self.runtime_dir, self.browser_profile_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
