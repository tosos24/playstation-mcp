"""Logs in to PlayStation once and shares that login with every tool.

Also keeps a short in-memory cache, because PSN only allows about
300 requests per 15 minutes.
"""

import os
import time
from pathlib import Path

from dotenv import load_dotenv
from psnawp_api import PSNAWP
from psnawp_api.models.trophies import PlatformType

# Load .env from this file's folder, so it works no matter who starts the server.
load_dotenv(Path(__file__).resolve().parent / ".env")

CACHE_SECONDS = 10 * 60
HOUR = 60 * 60

_me = None
_cache: dict[str, tuple[float, object]] = {}


def get_me():
    """Return my logged-in PSN account. Logs in on first use only."""
    global _me
    if _me is None:
        npsso = os.getenv("NPSSO")
        if not npsso:
            raise RuntimeError("NPSSO is missing. Put it in the .env file (see .env.example).")
        _me = PSNAWP(npsso).me()
    return _me


def cached(key: str, fetch, seconds: int = CACHE_SECONDS):
    """Return a saved result for `key` if it's fresher than `seconds`, otherwise call fetch() and save it."""
    now = time.time()
    if key in _cache and now - _cache[key][0] < seconds:
        return _cache[key][1]
    value = fetch()
    _cache[key] = (now, value)
    return value


def get_trophy_titles() -> list:
    """Every game on my trophy list, with progress. Cached."""
    return cached("trophy_titles", lambda: list(get_me().trophy_titles(limit=None)))


def pick_platform(title) -> PlatformType:
    """Pick the console to ask PSN about, for games released on more than one."""
    for p in (PlatformType.PS5, PlatformType.PS4, PlatformType.PS3, PlatformType.PS_VITA):
        if p in title.title_platform:
            return p
    return next(iter(title.title_platform), PlatformType.PS5)


def get_title_trophies(title) -> list:
    """Every trophy for one game (base game + DLC), with whether I've earned it. Cached."""
    return cached(
        f"trophies:{title.np_communication_id}",
        lambda: list(
            get_me().trophies(
                np_communication_id=title.np_communication_id,
                platform=pick_platform(title),
                include_progress=True,
                trophy_group_id="all",
            )
        ),
    )


def get_profile() -> dict:
    """My PSN profile (raw), including trophy level and counts. Cached for an hour."""
    return cached("profile", lambda: get_me().get_profile_legacy()["profile"], HOUR)


def get_devices() -> list[dict]:
    """Consoles registered to my account (raw). Cached for an hour."""
    return cached("devices", lambda: get_me().get_account_devices(), HOUR)


def forget_login():
    """Drop the saved login so the next call logs in again (e.g. after the NPSSO is renewed)."""
    global _me
    _me = None
