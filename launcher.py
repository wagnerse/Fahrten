"""Desktop launcher: applies any pending update, starts Streamlit as a child process,
opens a pywebview window pointed at it, and tears the child down on close.

When invoked with --streamlit-server, runs Streamlit in-process instead (this is the
child-process mode; the parent re-execs itself with that flag). Streamlit's bootstrap
installs signal handlers, so it must own the main thread of its process.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _ensure_paths_on_syspath() -> None:
    root = _project_root()
    for p in (str(root), str(root / "fahrtenplaner")):
        if p not in sys.path:
            sys.path.insert(0, p)


def _bake_build_env() -> None:
    """If a _build_config.py was generated in CI, surface its values as env vars."""
    try:
        from fahrtenplaner import _build_config  # type: ignore
    except ImportError:
        return
    for k, v in getattr(_build_config, "ENV", {}).items():
        os.environ.setdefault(k, v)


# --------------------------------------------------------------------------- #
# Child-process mode: run Streamlit on this process's main thread.
# --------------------------------------------------------------------------- #

def _run_streamlit_server(port: int, dev: bool = False) -> int:
    _ensure_paths_on_syspath()
    _bake_build_env()

    from streamlit.web import bootstrap

    app_path = str(_project_root() / "fahrtenplaner" / "app.py")
    flag_options = {
        "server.headless": True,
        "server.port": port,
        "server.address": "127.0.0.1",
        "server.runOnSave": dev,
        "browser.gatherUsageStats": False,
        "global.developmentMode": False,
    }
    bootstrap.load_config_options(flag_options=flag_options)
    bootstrap.run(app_path, False, [], flag_options)
    return 0


# --------------------------------------------------------------------------- #
# Parent-process mode: update-swap, spawn child, open window.
# --------------------------------------------------------------------------- #

def _free_port(preferred: int = 8501) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def _wait_for_server(url: str, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.3)
    return False


def _spawn_streamlit_child(port: int, dev: bool = False) -> subprocess.Popen:
    extra = ["--dev"] if dev else []
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--streamlit-server", "--port", str(port), *extra]
    else:
        cmd = [sys.executable, sys.argv[0], "--streamlit-server", "--port", str(port), *extra]

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

    return subprocess.Popen(cmd, creationflags=creationflags, close_fds=True)


def _run_parent(dev: bool = False) -> int:
    _ensure_paths_on_syspath()
    from fahrtenplaner import updater

    # Don't apply pending updates while iterating in dev mode.
    if not dev and updater.apply_pending_update_if_any():
        subprocess.Popen([sys.executable], close_fds=True)
        return 0

    port = _free_port(8501)
    url = f"http://127.0.0.1:{port}"

    child = _spawn_streamlit_child(port, dev=dev)
    try:
        if not _wait_for_server(url):
            sys.stderr.write("Streamlit-Server konnte nicht gestartet werden.\n")
            return 1

        import webview

        suffix = " — Dev" if dev else ""
        title = f"Fahrtenplaner {updater.current_version()}{suffix}"
        webview.create_window(title, url, width=1400, height=900, min_size=(900, 600))
        webview.start(debug=dev)
        return 0
    finally:
        try:
            child.terminate()
            child.wait(timeout=5)
        except Exception:
            try:
                child.kill()
            except Exception:
                pass


# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--streamlit-server", action="store_true")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Auto-reload on .py changes; right-click → Inspect available; update check skipped.",
    )
    args, _ = parser.parse_known_args(argv)

    if args.streamlit_server:
        return _run_streamlit_server(args.port, dev=args.dev)
    return _run_parent(dev=args.dev)


if __name__ == "__main__":
    sys.exit(main())
