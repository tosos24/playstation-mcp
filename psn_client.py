"""Logs in to PlayStation once and shares that login with every tool.

Also keeps a short in-memory cache, because PSN only allows about
300 requests per 15 minutes.
"""

import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from psnawp_api import PSNAWP
from psnawp_api.core.psnawp_exceptions import PSNAWPError
from psnawp_api.models.trophies import PlatformType

# Load .env from this file's folder, so it works no matter who starts the server.
load_dotenv(Path(__file__).resolve().parent / ".env")

CACHE_SECONDS = 10 * 60
HOUR = 60 * 60
DAY = 24 * HOUR

_me = None
_cache: dict[str, tuple[float, object]] = {}

# Tools run in parallel threads (and the startup warm-up runs in another), so these
# locks make sure two callers never fetch the same thing from PSN at the same time.
_login_lock = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


def get_me():
    """Return my logged-in PSN account. Logs in on first use only."""
    global _me
    with _login_lock:
        if _me is None:
            npsso = os.getenv("NPSSO")
            if not npsso:
                raise RuntimeError("NPSSO is missing. Put it in the .env file (see .env.example).")
            _me = PSNAWP(npsso).me()
        return _me


def cached(key: str, fetch, seconds: int = CACHE_SECONDS):
    """Return a saved result for `key` if it's fresher than `seconds`, otherwise call fetch() and save it."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    with _key_locks_guard:
        lock = _key_locks.setdefault(key, threading.Lock())
    with lock:
        # Someone else may have fetched it while we waited for the lock.
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < seconds:
            return hit[1]
        value = fetch()
        _cache[key] = (time.time(), value)
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


def get_title_stats() -> list:
    """Play time per title (PS4/PS5 only): hours, play count, first/last played. Cached."""
    return cached("title_stats", lambda: list(get_me().title_stats(limit=None)))


def get_library() -> list[dict]:
    """Games I own (PS4/PS5 entitlements, raw). Cached for an hour."""
    return cached("library", lambda: list(get_me().game_entitlements(limit=None, page_size=200)), HOUR)


def get_trophy_groups(title):
    """Trophy progress per group (base game, each DLC) for one game. Cached."""
    return cached(
        f"groups:{title.np_communication_id}",
        lambda: get_me().trophy_groups_summary(
            np_communication_id=title.np_communication_id,
            platform=pick_platform(title),
            include_progress=True,
        ),
    )


# PSN rejects more than 5 title IDs per lookup.
TITLE_ID_BATCH = 5


def map_title_ids(title_ids: list[str]) -> dict[str, str | None]:
    """Map play-time title IDs (e.g. PPSA02571_00) to trophy list IDs. Cached per ID for a day.

    Titles without a trophy list (apps like Netflix) map to None.
    """
    result, missing = {}, []
    for tid in dict.fromkeys(title_ids):
        hit = _cache.get(f"title_map:{tid}")
        if hit and time.time() - hit[0] < DAY:
            result[tid] = hit[1]
        else:
            missing.append(tid)

    def lookup(batch):
        return {t.np_title_id: t.np_communication_id
                for t in get_me().trophy_titles_for_title(title_ids=batch)}

    for i in range(0, len(missing), TITLE_ID_BATCH):
        batch = missing[i:i + TITLE_ID_BATCH]
        try:
            found = lookup(batch)
        except PSNAWPError:
            # One bad ID can fail the whole batch, so retry them one at a time.
            found = {}
            for tid in batch:
                try:
                    found.update(lookup([tid]))
                except PSNAWPError:
                    pass
        for tid in batch:
            result[tid] = found.get(tid)
            _cache[f"title_map:{tid}"] = (time.time(), found.get(tid))
    return result


def forget_login():
    """Drop the saved login so the next call logs in again (e.g. after the NPSSO is renewed)."""
    global _me
    _me = None
