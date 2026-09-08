"""Filesystem locations that stay correct on Windows and inside frozen builds."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

_APPLICATION_DIRECTORY_NAME = "Pesticide Shop Manager"


def is_frozen() -> bool:
    """Return ``True`` when running from a PyInstaller bundle."""

    return bool(getattr(sys, "frozen", False))


@lru_cache(maxsize=1)
def application_root() -> Path:
    """Return the directory that hosts the executable or the source checkout."""

    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def bundle_root() -> Path:
    """Return the directory holding read-only bundled data files."""

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(str(meipass)).resolve()
    return application_root()


def resource_path(*parts: str) -> Path:
    """Return a read-only data file shipped beside the application."""

    return bundle_root().joinpath(*parts)


def _user_data_root(windows: bool, environment: Mapping[str, str], home: Path) -> Path:
    """Return the per-user data directory for one platform and environment."""

    if windows:
        base = environment.get("LOCALAPPDATA") or environment.get("APPDATA")
        candidate = Path(base) if base else home / "AppData" / "Local"
    else:
        base = environment.get("XDG_DATA_HOME")
        candidate = Path(base) if base else home / ".local" / "share"
    return candidate / _APPLICATION_DIRECTORY_NAME


@lru_cache(maxsize=1)
def user_data_root() -> Path:
    """Return a per-user directory that is always writable on every platform."""

    return _user_data_root(os.name == "nt", os.environ, Path.home())


def _is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".write-test-", delete=True):
            return True
    except OSError:
        return False


def resolve_writable(path: Path) -> Path:
    """Anchor a relative runtime directory next to the application, or in user data.

    Absolute paths are returned untouched. Relative paths -- the defaults for logs
    and backups -- are resolved against the application directory when it is
    writable, which keeps a portable checkout self-contained, and otherwise against
    the per-user data directory so an installation under ``C:\\Program Files`` still
    works.
    """

    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    beside_application = application_root() / expanded
    if _is_writable(beside_application.parent):
        return beside_application
    return user_data_root() / expanded


def environment_file_candidates() -> tuple[Path, ...]:
    """Return the ``.env`` locations searched at startup, most specific first."""

    seen: dict[Path, None] = {}
    for candidate in (Path.cwd() / ".env", application_root() / ".env", bundle_root() / ".env"):
        seen.setdefault(candidate, None)
    return tuple(seen)
