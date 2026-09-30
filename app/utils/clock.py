"""The one clock and time zone every part of the application follows: the system's."""

from __future__ import annotations

import logging
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tzlocal

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def system_timezone_name() -> str:
    """The operating system's IANA time zone, such as ``Asia/Karachi``.

    Windows zone names ("Pakistan Standard Time") are mapped to their IANA name.
    In a container the ``TZ`` environment variable is the system zone. Read once
    per process, like the C runtime behind ``date.today()``, so a zone changed
    while the application runs takes effect for both after a restart.
    """

    try:
        name = tzlocal.get_localzone_name()
        if name:
            ZoneInfo(name)
            return name
    except (ZoneInfoNotFoundError, ValueError, OSError):
        logger.exception("The system time zone could not be read")
    logger.warning("The system time zone is unknown; falling back to UTC.")
    return "UTC"


def system_timezone() -> ZoneInfo:
    return ZoneInfo(system_timezone_name())


def local_now() -> datetime:
    """The system clock as an aware datetime in the system time zone."""

    return datetime.now(system_timezone())


def local_today() -> date:
    return local_now().date()
