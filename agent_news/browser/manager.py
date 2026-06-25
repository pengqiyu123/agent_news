"""Persistent browser manager for the WeChat browser session.

The browser PERSISTS because this manager (and its worker thread + Playwright
context) lives in a long-running server process. This is the ONLY mechanism —
no CDP, no daemon tricks.

Key invariants:
- Worker thread owns all Playwright objects (thread affinity).
- Operations run via _run_in_worker(fn) → queue → worker thread.
- with_session(channel, action_fn) is the single entry point: lock → ensure_page → action_fn(context, page).
- ensure_context reuses if signature matches, else relaunches via launch_persistent_context.
- Single-tab contract: extra tabs closed, editor page preferred.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from queue import Queue
from threading import Event, Lock, Thread

from ..config import get_settings

logger = logging.getLogger(__name__)

WECHAT_HOME_URL = "https://mp.weixin.qq.com/"
DEFAULT_LOCK_TIMEOUT_SECONDS = 60


# ── Page liveness helpers ───────────────────────────────────────────────────
def _is_page_closed(page) -> bool:
    if page is None:
        return True
    is_closed = getattr(page, "is_closed", None)
    if callable(is_closed):
        try:
            return bool(is_closed())
        except Exception:
            return True
    closed = getattr(page, "closed", None)
    return bool(closed)


def _can_interact_with_page(page) -> bool:
    """Real liveness check — runs a tiny evaluate to confirm the page responds."""
    if page is None or _is_page_closed(page):
        return False
    try:
        evaluator = getattr(page, "evaluate", None)
        if callable(evaluator):
            evaluator("() => document.readyState")
        else:
            _ = getattr(page, "url", "")
        return True
    except Exception:
        return False


def _list_live_context_pages(context) -> list:
    try:
        pages = list(getattr(context, "pages", []) or [])
    except Exception:
        return []
    return [page for page in pages if not _is_page_closed(page)]


def _is_editor_like_page(page) -> bool:
    try:
        url = str(getattr(page, "url", "") or "")
    except Exception:
        return False
    if "action=list_card" in url or "action=preview" in url or "/s/" in url:
        return False
    return "action=edit" in url or "media/appmsg_edit" in url


def _page_url_or_empty(page) -> str:
    try:
        return str(getattr(page, "url", "") or "").strip()
    except Exception:
        return ""


def _is_blank_page(page) -> bool:
    return _page_url_or_empty(page).lower() in {"", "about:blank"}


def _is_initial_blank_page(page) -> bool:
    return _is_blank_page(page)


# ── Channel helpers ─────────────────────────────────────────────────────────
def normalize_browser_name(value: object | None) -> str:
    compact = str(value or "").strip().lower()
    if compact in {"edge", "chrome"}:
        return compact
    return "edge"


def browser_channel_name(browser_name: str) -> str:
    return "msedge" if normalize_browser_name(browser_name) == "edge" else "chrome"


def default_browser_profile_path(browser_name: str = "edge") -> Path:
    settings = get_settings()
    return settings.runtime_dir / "browser" / f"wechat-{normalize_browser_name(browser_name)}-profile"


def resolve_profile_path(value: object | None, browser_name: object | None = None) -> Path:
    compact = str(value or "").strip()
    if compact:
        return Path(compact).expanduser()
    return default_browser_profile_path(normalize_browser_name(browser_name))


def ensure_channel_defaults(channel: dict | None = None) -> dict:
    """Normalize a channel dict for the resident WeChat browser."""
    next_channel = dict(channel or {})
    browser_name = normalize_browser_name(next_channel.get("browser_name"))
    next_channel["browser_name"] = browser_name
    next_channel["browser_profile_path"] = str(
        resolve_profile_path(next_channel.get("browser_profile_path"), browser_name)
    )
    next_channel["publish_entry_url"] = str(
        next_channel.get("publish_entry_url") or WECHAT_HOME_URL
    )
    next_channel["selectors_version"] = str(next_channel.get("selectors_version") or "wechat-mp-v1")
    return next_channel


def default_wechat_channel() -> dict:
    """The default WeChat channel config (Edge, default profile, home URL)."""
    return ensure_channel_defaults({})


# ── BrowserManager ──────────────────────────────────────────────────────────
class BrowserManager:
    """Thread-safe singleton owning a persistent Playwright browser context.

    The browser stays alive as long as this object's worker thread runs —
    which is the entire lifetime of the server process (via FastAPI lifespan).
    """

    def __init__(self) -> None:
        self._playwright = None
        self._context = None
        self._page = None
        self._lock = Lock()
        self._worker: Thread | None = None
        self._queue: Queue | None = None
        self._worker_thread_id: int | None = None
        self._manager_alive = False
        self._channel_signature: tuple | None = None
        self._resident_page: str | None = None
        self._last_reset_reason: str | None = None
        self._last_error: str | None = None

    # ── Worker thread ───────────────────────────────────────────────────────
    def startup(self) -> None:
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive() and self._queue is not None:
            return
        self._queue = Queue()
        self._worker = Thread(target=self._worker_loop, name="browser-worker", daemon=True)
        self._worker.start()

    def _worker_loop(self) -> None:
        self._worker_thread_id = threading.get_ident()
        self._manager_alive = True
        assert self._queue is not None
        while True:
            item = self._queue.get()
            if item is None:
                break
            fn, result_box = item
            try:
                result_box["result"] = fn()
            except Exception as exc:
                result_box["error"] = exc
            finally:
                result_box["event"].set()
        self._close_runtime_internal()
        self._manager_alive = False
        self._worker_thread_id = None

    def _run_in_worker(self, fn):
        self._ensure_worker()
        result_box = {"result": None, "error": None, "event": Event()}
        assert self._queue is not None
        self._queue.put((fn, result_box))
        result_box["event"].wait()
        if result_box["error"] is not None:
            raise result_box["error"]
        return result_box["result"]

    def shutdown(self) -> None:
        if self._worker_thread_id == threading.get_ident():
            self._close_runtime_internal()
            return
        if self._queue is not None:
            self._queue.put(None)
        if self._worker is not None:
            self._worker.join(timeout=10)
        self._worker = None
        self._queue = None
        self._worker_thread_id = None

    # ── Status ──────────────────────────────────────────────────────────────
    def is_alive(self) -> bool:
        return bool(
            self._manager_alive
            and self._worker is not None
            and self._worker.is_alive()
        )

    def is_busy(self) -> bool:
        return self._lock.locked()

    def manager_state(self) -> dict:
        return {
            "manager_alive": self.is_alive(),
            "busy": self.is_busy(),
            "resident_page": self._resident_page,
            "last_reset_reason": self._last_reset_reason,
            "last_error": self._last_error,
        }

    def observe_page(self) -> dict:
        """Read current page state ON THE WORKER THREAD (Playwright is thread-affine).

        Returns {current_url, is_editor_page}. Safe to call from any thread —
        it marshals the read through _run_in_worker. If no page/context exists,
        returns empty values. Does NOT take _lock, so it can interleave with an
        in-flight with_session (they serialize via the worker queue).
        """
        def _do():
            if self._page is None:
                return {"current_url": "", "is_editor_page": False, "page_count": 0, "page_urls": []}
            try:
                url = str(getattr(self._page, "url", "") or "")
            except Exception:
                url = ""
            page_urls = []
            if self._context is not None:
                for candidate in _list_live_context_pages(self._context):
                    page_urls.append(_page_url_or_empty(candidate))
            return {
                "current_url": url,
                "is_editor_page": _is_editor_like_page(self._page),
                "page_count": len(page_urls),
                "page_urls": page_urls,
            }
        try:
            return self._run_in_worker(_do)
        except Exception:
            return {"current_url": "", "is_editor_page": False, "page_count": 0, "page_urls": []}

    def observe_tabs(self) -> dict:
        """Return live tab metadata on the worker thread."""
        def _do():
            pages = _list_live_context_pages(self._context) if self._context is not None else []
            tabs = []
            focused_index = None
            for index, page in enumerate(pages):
                url = _page_url_or_empty(page)
                try:
                    title = str(page.title() or "")
                except Exception:
                    title = ""
                if page is self._page:
                    focused_index = index
                tabs.append(
                    {
                        "index": index,
                        "url": url,
                        "title": title,
                        "is_blank": _is_blank_page(page),
                        "is_editor": _is_editor_like_page(page),
                    }
                )
            return {"page_count": len(tabs), "tabs": tabs, "focused_index": focused_index}

        try:
            return self._run_in_worker(_do)
        except Exception as exc:
            return {"page_count": 0, "tabs": [], "focused_index": None, "error": f"{type(exc).__name__}: {exc}"}

    def focus_editor_tab(self) -> dict:
        """Focus an existing editor tab without creating a new page."""
        def _do():
            pages = _list_live_context_pages(self._context) if self._context is not None else []
            for index, page in enumerate(pages):
                if not _is_editor_like_page(page):
                    continue
                self._page = page
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                self._resident_page = "editor|focused"
                return {"focused": True, "focused_index": index, "url": _page_url_or_empty(page)}
            return {"focused": False, "focused_index": None, "url": "", "error": "editor tab not found"}

        return self._run_in_worker(_do)

    def close_blank_tabs(self) -> dict:
        """Close about:blank tabs while preserving editor/effective tabs."""
        def _do():
            pages = _list_live_context_pages(self._context) if self._context is not None else []
            closed = []
            kept = []
            if len(pages) <= 1:
                return {"closed_count": 0, "closed": [], "kept": [_page_url_or_empty(p) for p in pages]}
            for index, page in enumerate(pages):
                url = _page_url_or_empty(page)
                if _is_blank_page(page) and page is not self._page:
                    try:
                        page.close()
                        closed.append({"index": index, "url": url})
                    except Exception:
                        kept.append(url)
                else:
                    kept.append(url)
            if not _can_interact_with_page(self._page):
                live_pages = _list_live_context_pages(self._context) if self._context is not None else []
                self._page = self._pick_reusable_page(live_pages)
            return {"closed_count": len(closed), "closed": closed, "kept": kept}

        return self._run_in_worker(_do)

    def signature_for(self, channel: dict) -> tuple:
        normalized = ensure_channel_defaults(channel)
        return (
            str(normalized.get("browser_name") or "edge"),
            str(normalized.get("browser_profile_path") or ""),
            str(normalized.get("publish_entry_url") or WECHAT_HOME_URL),
            str(normalized.get("selectors_version") or "wechat-mp-v1"),
        )

    # ── Context / page management ───────────────────────────────────────────
    def reset(self, reason: str = "") -> None:
        self._resident_page = f"reset:{reason or 'unknown'}"
        self._last_reset_reason = reason or "unknown"
        if self._worker_thread_id == threading.get_ident():
            self._close_runtime_internal()
            return
        try:
            self._run_in_worker(self._close_runtime_internal)
        except Exception:
            self._close_runtime_internal()

    def _ensure_playwright(self):
        if self._playwright is not None:
            return self._playwright
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        return self._playwright

    def _close_extra_pages(self, context, keep_page) -> None:
        for candidate in _list_live_context_pages(context):
            if candidate is keep_page:
                continue
            try:
                candidate.close()
            except Exception:
                pass

    def _close_blank_pages(self, context, *, keep_page=None) -> None:
        """Close startup about:blank tabs without touching the working page."""
        for candidate in _list_live_context_pages(context):
            if candidate is keep_page:
                continue
            if not _is_initial_blank_page(candidate):
                continue
            try:
                candidate.close()
            except Exception:
                pass

    def _pick_reusable_page(self, pages: list):
        live_pages = [page for page in pages if _can_interact_with_page(page)]
        if not live_pages:
            return None
        for page in live_pages:
            if _is_editor_like_page(page):
                return page
        non_blank_pages = [page for page in live_pages if not _is_blank_page(page)]
        if _can_interact_with_page(self._page) and not _is_blank_page(self._page):
            for page in live_pages:
                if page is self._page:
                    return page
        if non_blank_pages:
            return non_blank_pages[0]
        if _can_interact_with_page(self._page):
            for page in live_pages:
                if page is self._page:
                    return page
        return live_pages[0]

    def _prepare_working_page(self, context, entry_url: str):
        live_pages = [item for item in _list_live_context_pages(context) if _can_interact_with_page(item)]
        page = self._pick_reusable_page(live_pages)
        created_page = False
        create_error = None
        if page is None:
            try:
                page = context.new_page()
                created_page = True
            except Exception as exc:
                create_error = exc
                page = None
        if page is None:
            page_urls = [_page_url_or_empty(candidate) for candidate in _list_live_context_pages(context)]
            detail = f"page_count={len(page_urls)}, urls={page_urls}"
            if create_error is not None:
                detail += f", new_page_error={type(create_error).__name__}: {create_error}"
            raise RuntimeError(f"违反单标签页约束：当前浏览器上下文中没有可复用标签页（{detail}）。")
        try:
            page.evaluate("() => { document.title = 'agent-news-微信专用'; }")
        except Exception:
            pass
        try:
            current_url = str(getattr(page, "url", "") or "").strip().lower()
        except Exception:
            current_url = ""
        should_navigate = created_page or current_url in {"", "about:blank"}
        if should_navigate:
            try:
                page.goto(entry_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
        self._close_blank_pages(context, keep_page=page)
        self._close_extra_pages(context, page)
        self._page = page
        self._resident_page = "home" if should_navigate else (self._resident_page or "recovered")
        return page

    def ensure_context(self, channel: dict):
        """Launch or reuse the persistent context. Runs on worker thread."""
        signature = self.signature_for(channel)
        if self._context is not None and self.is_alive() and signature == self._channel_signature:
            live_pages = _list_live_context_pages(self._context)
            if live_pages:
                preferred = self._pick_reusable_page(live_pages)
                if preferred is not None and (
                    not _can_interact_with_page(self._page)
                    or (_is_editor_like_page(preferred) and not _is_editor_like_page(self._page))
                    or (_is_blank_page(self._page) and not _is_blank_page(preferred))
                ):
                    self._page = preferred
                if _can_interact_with_page(self._page):
                    self._close_blank_pages(self._context, keep_page=self._page)
                    self._close_extra_pages(self._context, self._page)
                self._resident_page = f"{self._resident_page or 'home'}|context_reused"
                return self._context
        # Relaunch.
        self._close_runtime_internal()
        playwright = self._ensure_playwright()
        normalized = ensure_channel_defaults(channel)
        self._clean_profile_locks(Path(normalized["browser_profile_path"]))

        context = playwright.chromium.launch_persistent_context(
            str(normalized["browser_profile_path"]),
            headless=False,
            channel=browser_channel_name(str(normalized.get("browser_name"))),
            args=[
                "--remote-debugging-port=9223",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=msEdgeStartupBoost,msEdgeFirstRunExperience",
            ],
        )
        self._context = context
        self._page = None
        self._channel_signature = signature
        self._resident_page = "boot"
        return context

    def ensure_page(self, channel: dict, entry_url: str):
        context = self.ensure_context(channel)
        if _can_interact_with_page(self._page):
            return self._page
        self._page = None
        return self._prepare_working_page(context, entry_url)

    @staticmethod
    def _clean_profile_locks(profile_path: Path) -> None:
        profile_path.mkdir(parents=True, exist_ok=True)
        for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock = profile_path / lock_name
            if lock.exists():
                try:
                    lock.unlink()
                except Exception:
                    pass

    def _close_runtime_internal(self) -> None:
        page = self._page
        context = self._context
        playwright = self._playwright
        self._page = None
        self._context = None
        self._playwright = None
        self._channel_signature = None
        self._resident_page = None
        for closable in (page, context):
            if closable is None:
                continue
            try:
                closable.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    # ── The single entry point ──────────────────────────────────────────────
    def with_session(
        self,
        channel: dict | None = None,
        *,
        action_fn,
        timeout_seconds: int = DEFAULT_LOCK_TIMEOUT_SECONDS,
        reset_on_failure: bool = True,
    ):
        """Run action_fn(context, page) on the worker thread, serialized by a lock.

        channel defaults to the WeChat channel. action_fn receives (context, page).
        """
        channel = ensure_channel_defaults(channel) if channel else default_wechat_channel()
        acquired = self._lock.acquire(timeout=timeout_seconds)
        if not acquired:
            raise RuntimeError("浏览器忙，稍后重试")
        try:

            def _execute():
                page = self.ensure_page(channel, str(channel.get("publish_entry_url") or WECHAT_HOME_URL))
                return action_fn(self._context, page)

            return self._run_in_worker(_execute)
        except Exception as e:
            # Record the error so wechat.session / manager_state can surface it.
            self._last_error = f"{type(e).__name__}: {e}"
            if reset_on_failure:
                try:
                    self.reset("with_session_failed")
                except Exception:
                    self._last_reset_reason = "with_session_failed"
                    self._close_runtime_internal()
            raise
        finally:
            try:
                self._lock.release()
            except Exception:
                pass

    # ── Screenshot (runs on worker thread, for diagnostics) ────────────────
    def screenshot(self, label: str = "step") -> str | None:
        from datetime import datetime, timezone

        def _do():
            if self._page is None:
                return None
            settings = get_settings()
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            path = settings.runtime_dir / f"screenshot_{label}_{ts}.png"
            try:
                self._page.screenshot(path=str(path), full_page=True)
                return str(path)
            except Exception as e:
                logger.warning("screenshot failed: %s", e)
                return None

        try:
            return self._run_in_worker(_do)
        except Exception:
            return None


# ── Singleton (module-level, lives in the server process) ───────────────────
BROWSER_MANAGER = BrowserManager()


def get_browser_manager() -> BrowserManager:
    return BROWSER_MANAGER


def reset_browser_manager() -> None:
    BROWSER_MANAGER.shutdown()
