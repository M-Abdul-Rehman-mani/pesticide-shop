"""Timestamps are read and printed in the shop's time zone, not the server's."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

from app.config.settings import get_settings
from app.database.session import set_session_timezone


def test_connections_use_the_shop_time_zone() -> None:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        set_session_timezone(engine, "UTC")
        with engine.connect() as connection:
            assert connection.scalar(text("SHOW TimeZone")) == "UTC"

        # A change in Settings takes effect for the very next connection.
        set_session_timezone(engine, "Asia/Karachi")
        with engine.connect() as connection:
            assert connection.scalar(text("SHOW TimeZone")) == "Asia/Karachi"
            offset = connection.scalar(text("SELECT extract(timezone FROM now())"))
            assert int(offset) == 5 * 3600
    finally:
        engine.dispose()


@pytest.mark.parametrize("zone", ["Asia/Karachi", "America/New_York", "UTC"])
def test_everything_follows_the_system_time_zone(zone: str) -> None:
    """An old APP_TIMEZONE is ignored; the system zone drives settings, clock and dates.

    Run in a fresh interpreter because the system zone is read once per process.
    """

    script = (
        "from datetime import date\n"
        "from app.config.settings import get_settings\n"
        "from app.utils.clock import local_now, local_today, system_timezone_name\n"
        "s = get_settings()\n"
        "assert system_timezone_name() == s.app_timezone, s.app_timezone\n"
        "assert local_today() == date.today()\n"
        "assert local_now().utcoffset() == local_now().astimezone().utcoffset()\n"
        "print(s.app_timezone)\n"
    )
    environment = {**os.environ, "TZ": zone, "APP_TIMEZONE": "Pacific/Auckland"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == zone
