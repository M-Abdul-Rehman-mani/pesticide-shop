"""Frozen-build and Windows-safe filesystem location behaviour."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.utils import paths


@pytest.fixture(autouse=True)
def _clear_path_caches() -> None:
    for cached in (paths.application_root, paths.bundle_root, paths.user_data_root):
        cached.cache_clear()


def test_source_checkout_resolves_to_the_project_root() -> None:
    assert paths.is_frozen() is False
    assert (paths.application_root() / "app" / "utils" / "paths.py").is_file()
    assert paths.resource_path("alembic.ini").is_file()


def test_frozen_build_reads_data_from_the_extraction_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A PyInstaller bundle keeps data in ``_MEIPASS`` and the exe elsewhere."""

    extracted = tmp_path / "extracted"
    installed = tmp_path / "installed"
    extracted.mkdir()
    installed.mkdir()
    (extracted / "alembic.ini").write_text("[alembic]\n")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(extracted), raising=False)
    monkeypatch.setattr(sys, "executable", str(installed / "PesticideShopManager.exe"))
    for cached in (paths.application_root, paths.bundle_root):
        cached.cache_clear()

    assert paths.is_frozen() is True
    assert paths.application_root() == installed
    assert paths.bundle_root() == extracted
    assert paths.resource_path("alembic.ini").read_text() == "[alembic]\n"


def test_relative_runtime_directories_sit_beside_a_writable_application(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "application_root", lambda: tmp_path)
    assert paths.resolve_writable(Path("logs")) == tmp_path / "logs"


def test_relative_runtime_directories_fall_back_to_user_data_when_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An install under ``C:\\Program Files`` must not try to write logs there."""

    read_only = tmp_path / "program-files"
    monkeypatch.setattr(paths, "application_root", lambda: read_only)
    monkeypatch.setattr(paths, "_is_writable", lambda _directory: False)
    monkeypatch.setattr(paths, "user_data_root", lambda: tmp_path / "appdata")
    assert paths.resolve_writable(Path("backups")) == tmp_path / "appdata" / "backups"


def test_absolute_runtime_directories_are_left_alone(tmp_path: Path) -> None:
    assert paths.resolve_writable(tmp_path / "custom") == tmp_path / "custom"


def test_user_data_root_uses_the_windows_local_app_data_directory(tmp_path: Path) -> None:
    local = tmp_path / "Local"
    assert paths._user_data_root(True, {"LOCALAPPDATA": str(local)}, tmp_path) == (
        local / "Pesticide Shop Manager"
    )
    assert paths._user_data_root(True, {}, tmp_path) == (
        tmp_path / "AppData" / "Local" / "Pesticide Shop Manager"
    )


def test_user_data_root_honours_xdg_on_other_platforms(tmp_path: Path) -> None:
    assert paths._user_data_root(False, {"XDG_DATA_HOME": str(tmp_path)}, tmp_path) == (
        tmp_path / "Pesticide Shop Manager"
    )
    assert paths._user_data_root(False, {}, tmp_path) == (
        tmp_path / ".local" / "share" / "Pesticide Shop Manager"
    )


def test_environment_file_candidates_cover_cwd_and_the_executable_directory() -> None:
    candidates = paths.environment_file_candidates()
    assert Path.cwd() / ".env" in candidates
    assert paths.application_root() / ".env" in candidates
    assert len(set(candidates)) == len(candidates)


def test_a_chosen_logo_is_copied_into_application_storage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A logo referenced where the operator found it disappears when that moves."""

    storage = tmp_path / "appdata"
    monkeypatch.setattr(paths, "resolve_writable", lambda relative: storage / relative)
    source = tmp_path / "downloads" / "company-logo.PNG"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\x89PNG\r\n\x1a\n fake image")

    stored = paths.store_shop_logo(source)
    assert stored == storage / "branding" / "shop-logo.png"
    assert stored.read_bytes() == source.read_bytes()

    # The receipt keeps printing after the original is deleted.
    source.unlink()
    assert stored.is_file()


def test_storing_a_new_logo_replaces_the_previous_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = tmp_path / "appdata"
    monkeypatch.setattr(paths, "resolve_writable", lambda relative: storage / relative)
    first = tmp_path / "one.png"
    first.write_bytes(b"first")
    second = tmp_path / "two.jpg"
    second.write_bytes(b"second")

    paths.store_shop_logo(first)
    stored = paths.store_shop_logo(second)
    assert stored.name == "shop-logo.jpg"
    assert stored.read_bytes() == b"second"
    assert sorted(path.name for path in stored.parent.glob("shop-logo.*")) == ["shop-logo.jpg"]

    # Re-storing the stored file is a no-op rather than a self-copy.
    assert paths.store_shop_logo(stored) == stored


def test_storing_a_missing_logo_is_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        paths.store_shop_logo(tmp_path / "absent.png")
