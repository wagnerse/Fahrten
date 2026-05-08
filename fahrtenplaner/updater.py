"""GitHub Releases-based self-update for the bundled Windows app.

Two halves:
- Version check + staged download (called from the Streamlit UI).
- Pre-launch swap (called from launcher.py before Streamlit starts).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Filled in at build time by CI; for dev runs the placeholder stays.
GITHUB_REPO = os.environ.get("FAHRTEN_GITHUB_REPO", "OWNER/REPO")

ASSET_NAME = "Fahrtenplaner.exe"


def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def install_dir() -> Path:
    if _frozen():
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def current_version() -> str:
    if _frozen():
        candidates = [Path(sys._MEIPASS) / "VERSION", install_dir() / "VERSION"]
    else:
        candidates = [install_dir() / "VERSION"]
    for p in candidates:
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return "0.0.0"


@dataclass
class Release:
    tag: str
    name: str
    body: str
    asset_url: Optional[str]


def _parse_version(s: str) -> tuple[int, ...]:
    s = s.lstrip("v").strip()
    parts = re.split(r"[.\-+]", s)
    out = []
    for p in parts:
        if p.isdigit():
            out.append(int(p))
        else:
            break
    return tuple(out) or (0,)


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def fetch_latest_release(timeout: float = 10.0) -> Optional[Release]:
    if "/" not in GITHUB_REPO or GITHUB_REPO == "OWNER/REPO":
        return None
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    asset_url = None
    for asset in data.get("assets") or []:
        if asset.get("name") == ASSET_NAME:
            asset_url = asset.get("browser_download_url")
            break
    return Release(
        tag=data.get("tag_name", ""),
        name=data.get("name") or data.get("tag_name", ""),
        body=data.get("body", "") or "",
        asset_url=asset_url,
    )


def download_update(release: Release, progress=None) -> Path:
    """Download the new .exe to the staging dir and write a PENDING marker.

    Caller must have verified that an update is actually available.
    Returns the staged path.
    """
    if not release.asset_url:
        raise RuntimeError("Release has no Fahrtenplaner.exe asset")

    staging = install_dir() / "update"
    staging.mkdir(exist_ok=True)
    target = staging / "Fahrtenplaner-new.exe"
    tmp = staging / "Fahrtenplaner-new.part"

    req = urllib.request.Request(release.asset_url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get("Content-Length", "0") or 0)
        downloaded = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress and total:
                    progress(downloaded / total)

    if target.exists():
        target.unlink()
    tmp.rename(target)

    (staging / "VERSION").write_text(release.tag.lstrip("v"), encoding="utf-8")
    (staging / "PENDING").write_text("ready", encoding="utf-8")
    return target


def apply_pending_update_if_any() -> bool:
    """Called by launcher.py before any heavy imports.

    Returns True if a swap happened (caller should re-exec the new binary).
    """
    if not _frozen() or sys.platform != "win32":
        _cleanup_old_exe()
        return False

    here = install_dir()
    pending = here / "update" / "PENDING"
    new_exe = here / "update" / "Fahrtenplaner-new.exe"

    _cleanup_old_exe()

    if not pending.exists() or not new_exe.exists():
        if pending.exists():
            pending.unlink()
        return False

    current = Path(sys.executable)
    old = current.with_suffix(current.suffix + ".old")

    try:
        if old.exists():
            old.unlink()
        os.replace(current, old)
        os.replace(new_exe, current)
        pending.unlink()
        new_version = (here / "update" / "VERSION")
        if new_version.exists():
            shutil.copyfile(new_version, here / "VERSION")
            new_version.unlink()
        return True
    except Exception:
        if old.exists() and not current.exists():
            try:
                os.replace(old, current)
            except Exception:
                pass
        return False


def _cleanup_old_exe() -> None:
    if not _frozen():
        return
    old = Path(sys.executable).with_suffix(Path(sys.executable).suffix + ".old")
    if old.exists():
        try:
            old.unlink()
        except Exception:
            pass
