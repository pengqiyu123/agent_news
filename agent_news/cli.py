"""CLI entry — thin client that talks to the FastAPI tool server.

Architecture (per PLAN.md):
- The FastAPI server (auto-started by CLI, or via start.bat) is the primary
  runtime. It owns the persistent BrowserManager singleton + worker thread.
- CLI prefers HTTP to 127.0.0.1:8000. If the server is down, the CLI
  auto-starts it (with a shared start-lock). Only radar/data ops fall back
  to local in-process execution; wechat.* ops REQUIRE the server (they fail
  explicitly if the server can't start, to avoid competing browser profiles).

Commands:
    python -m agent_news list                    list all registered operations
    python -m agent_news status                  is the FastAPI server up?
    python -m agent_news dashboard               open WeChat MP + check login
    python -m agent_news run <op> [key=val ...]  run any operation
"""

from __future__ import annotations

import json
import sys
from typing import Any

SERVER_URL = "http://127.0.0.1:8000"
HEALTH_TIMEOUT = 2.0


def _emit(result: Any) -> None:
    """Print a result as JSON to stdout."""
    if hasattr(result, "model_dump"):
        print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    elif isinstance(result, (dict, list)):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(str(result))


def _server_is_up() -> bool:
    """Probe the FastAPI server's /api/health. True if reachable."""
    try:
        import httpx
        r = httpx.get(f"{SERVER_URL}/api/health", timeout=HEALTH_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def _start_lock_path():
    """Path to the start-lock file (shared with start_backend.ps1)."""
    from .config import get_settings
    return get_settings().logs_dir / "backend.start.lock"


def _test_start_lock_alive() -> bool:
    """True if a start-lock file exists AND its PID is still alive.

    Mirrors scripts/start_backend.ps1 Test-StartLockAlive so CLI and the PS1
    script share the SAME lock file — whoever starts first blocks the other.
    """
    import os
    lock = _start_lock_path()
    if not lock.exists():
        return False
    try:
        lock_pid = int(lock.read_text(encoding="utf-8").strip().splitlines()[0])
        if lock_pid and lock_pid != os.getpid():
            # Check if that PID is alive.
            try:
                if os.name == "nt":
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x1000, False, lock_pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                    if handle:
                        kernel32.CloseHandle(handle)
                        return True
                    return False
                else:
                    os.kill(lock_pid, 0)
                    return True
            except Exception:
                return False
    except Exception:
        pass
    return False


def _write_start_lock() -> None:
    """Write our PID into the start-lock file."""
    import os
    lock = _start_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()), encoding="utf-8")


def _remove_start_lock() -> None:
    """Delete the start-lock file (idempotent)."""
    lock = _start_lock_path()
    if lock.exists():
        try:
            lock.unlink()
        except Exception:
            pass


def _auto_start_server(timeout: float = 30.0) -> bool:
    """Launch the FastAPI server in the background if it's not running.

    Uses the SAME start-lock file as scripts/start_backend.ps1, so a CLI
    auto-start and a manual start.bat never double-launch. If another start
    is already in progress (lock alive), we wait for it instead of launching
    our own.

    Returns True if the server is (or becomes) up within `timeout` seconds.
    """
    if _server_is_up():
        return True

    import os
    import subprocess
    import time
    from .config import get_settings

    # If another start is in progress (CLI or PS1), wait for it instead of racing.
    if _test_start_lock_alive():
        print("[info] another start is in progress (lock held), waiting...", file=sys.stderr)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _server_is_up():
                return True
            time.sleep(0.5)
        print("[warn] waited for another start but server still not up", file=sys.stderr)
        return False

    # Clean any stale lock, then acquire our own.
    _remove_start_lock()
    _write_start_lock()

    settings = get_settings()
    venv_python = settings.project_root / ".venv" / "Scripts" / "python.exe"
    python_exe = str(venv_python) if venv_python.exists() else sys.executable

    print("[info] server not running, auto-starting...", file=sys.stderr)
    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008 | 0x00000200

    log_dir = settings.logs_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = open(log_dir / "auto_start.log", "a", encoding="utf-8")

    try:
        subprocess.Popen(
            [python_exe, "-m", "uvicorn", "agent_news.main:app",
             "--host", "127.0.0.1", "--port", "8000"],
            cwd=str(settings.project_root),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as e:
        print(f"[warn] auto-start failed: {e}", file=sys.stderr)
        _remove_start_lock()
        return False

    # Poll /api/health until up or timeout.
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _server_is_up():
            print("[info] server is up", file=sys.stderr)
            _remove_start_lock()
            return True
        time.sleep(0.5)
    print(f"[warn] server did not come up within {timeout}s", file=sys.stderr)
    _remove_start_lock()
    return False


def _exec(op_name: str, params: dict) -> dict:
    """Execute an operation: HTTP to server if up, else auto-start then HTTP.

    Auto-ensure: if the server is down, the CLI launches it in the background
    (with the shared start-lock), waits for /api/health, then sends the op.

    Browser ops (wechat.*) MUST run in the server process — if the server
    can't be started, they fail explicitly rather than running locally (which
    would start a competing browser profile). Radar/data ops fall back to
    local in-process execution as a last resort.

    Fallback policy: local fallback is ONLY used when the server is unreachable
    (network/procdown). If the server is reachable but the operation returned
    a business error (non-200), we return that error directly — never fallback.
    """
    is_browser_op = op_name.startswith("wechat.")

    # Try HTTP first.
    if _server_is_up():
        try:
            import httpx
            r = httpx.post(
                f"{SERVER_URL}/api/operations/{op_name}/execute",
                json={"params": params},
                timeout=300,
            )
            if r.status_code == 200:
                return r.json().get("item", r.json())
            # Server reachable but business error (e.g. 404 unknown op, 422 bad params).
            # Return the error directly — do NOT fallback to local.
            return {
                "status": "failed",
                "message": f"server returned HTTP {r.status_code}: {r.text[:200]}",
                "ok": False,
            }
        except Exception as e:
            # Network error (server crashed mid-call) — fall through to auto-start.
            print(f"[warn] server unreachable ({e}), auto-starting", file=sys.stderr)

    # Server not up (or call failed) — auto-start it, then retry HTTP once.
    if _auto_start_server():
        try:
            import httpx
            r = httpx.post(
                f"{SERVER_URL}/api/operations/{op_name}/execute",
                json={"params": params},
                timeout=300,
            )
            if r.status_code == 200:
                return r.json().get("item", r.json())
            return {
                "status": "failed",
                "message": f"server returned HTTP {r.status_code}: {r.text[:200]}",
                "ok": False,
            }
        except Exception as e:
            print(f"[warn] post-auto-start HTTP failed ({e})", file=sys.stderr)

    # Server unreachable after auto-start attempt.
    if is_browser_op:
        # Browser ops MUST NOT run locally — would start a competing browser
        # profile that conflicts with the server's singleton.
        return {
            "status": "failed",
            "message": (
                "服务未运行且自动启动失败。浏览器操作必须在服务进程中执行，"
                "请手动运行 start.bat 或检查 logs/auto_start.log。"
            ),
            "ok": False,
        }

    # Radar/data ops: local fallback is safe (no browser).
    from .operations.registry import OPERATION_REGISTRY
    result = OPERATION_REGISTRY.execute(op_name, **params)
    return result.model_dump()


def cmd_list() -> int:
    """List all registered operations (always local — no server needed)."""
    from .operations.registry import OPERATION_REGISTRY
    specs = OPERATION_REGISTRY.list_specs()
    out = [
        {"name": s.name, "category": s.category, "description": s.description, "params": s.params}
        for s in specs
    ]
    _emit({"operations": out, "total": len(out)})
    return 0


def cmd_status() -> int:
    """Check if the FastAPI tool server is running."""
    up = _server_is_up()
    out = {"server_running": up, "url": SERVER_URL if up else None}
    if up:
        try:
            import httpx
            r = httpx.get(f"{SERVER_URL}/api/health", timeout=HEALTH_TIMEOUT)
            out["health"] = r.json()
        except Exception:
            pass
    _emit(out)
    return 0 if up else 1


def cmd_dashboard() -> int:
    """Open WeChat MP and check login state."""
    result = _exec("wechat.open_dashboard", {})
    _emit(result)
    return 0 if result.get("ok") else 1


def cmd_run(op_name: str, *kv_args: str) -> int:
    """Run an arbitrary operation: python -m agent_news run <op> key=val ..."""
    params: dict[str, Any] = {}
    for arg in kv_args:
        if "=" not in arg:
            print(f"[error] argument '{arg}' must be key=value", file=sys.stderr)
            return 2
        key, _, value = arg.partition("=")
        value = value.strip()
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            parsed = value
        params[key] = parsed

    from .operations.registry import OPERATION_REGISTRY
    if not OPERATION_REGISTRY.has(op_name):
        print(f"[error] operation '{op_name}' not found", file=sys.stderr)
        return 2

    result = _exec(op_name, params)
    _emit(result)
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    cmd = argv[0]
    if cmd == "list":
        return cmd_list()
    if cmd == "status":
        return cmd_status()
    if cmd == "dashboard":
        return cmd_dashboard()
    if cmd == "run":
        if len(argv) < 2:
            print("[error] usage: python -m agent_news run <op> [key=val ...]", file=sys.stderr)
            return 2
        return cmd_run(argv[1], *argv[2:])

    print(f"[error] unknown command '{cmd}'. Try: list | status | dashboard | run", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
