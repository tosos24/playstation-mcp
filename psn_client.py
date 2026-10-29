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
from psnawp_api.core.psnawp_exceptions import PSNAWPBadRequestError, PSNAWPNotFoundError
from psnawp_api.models.trophies import PlatformType
from psnawp_api.utils.endpoints import API_PATH, BASE_PATH

# Load .env from this file's folder, so it works no matter who starts the server.
load_dotenv(Path(__file__).resolve().parent / ".env")

CACHE_SECONDS = 10 * 60
HOUR = 60 * 60
DAY = 24 * HOUR

_psnawp = None
_me = None
_cache: dict[str, tuple[float, object]] = {}

# Tools run in parallel threads (and the startup warm-up runs in another), so these
# locks make sure two callers never fetch the same thing from PSN at the same time.
_login_lock = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


def get_me():
    """Return my logged-in PSN account. Logs in on first use only."""
    global _psnawp, _me
    with _login_lock:
        if _me is None:
            npsso = os.getenv("NPSSO")
            if not npsso:
                raise RuntimeError("NPSSO is missing. Put it in the .env file (see .env.example).")
            _psnawp = PSNAWP(npsso)
            _me = _psnawp.me()
        return _me


def get_psnawp() -> PSNAWP:
    """The logged-in PSNAWP object, for looking up other players."""
    get_me()
    return _psnawp


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

# One lock for all title-ID lookups, so the warm-up and a tool call never look up the same IDs twice.
_title_map_lock = threading.Lock()


def map_title_ids(title_ids: list[str]) -> dict[str, str | None]:
    """Map play-time title IDs (e.g. PPSA02571_00) to trophy list IDs. Cached per ID for a day.

    Titles without a trophy list (apps like Netflix) map to None.
    """
    def split_cached(tids):
        hits, misses = {}, []
        for tid in tids:
            hit = _cache.get(f"title_map:{tid}")
            if hit and time.time() - hit[0] < DAY:
                hits[tid] = hit[1]
            else:
                misses.append(tid)
        return hits, misses

    def lookup(batch):
        return {t.np_title_id: t.np_communication_id
                for t in get_me().trophy_titles_for_title(title_ids=batch)}

    result, missing = split_cached(dict.fromkeys(title_ids))
    if not missing:
        return result
    with _title_map_lock:
        # Someone else may have looked some of them up while we waited for the lock.
        hits, missing = split_cached(missing)
        result.update(hits)
        for i in range(0, len(missing), TITLE_ID_BATCH):
            batch = missing[i:i + TITLE_ID_BATCH]
            try:
                found = lookup(batch)
            except PSNAWPBadRequestError:
                # One bad ID can fail the whole batch, so retry them one at a time.
                # Other errors (rate limit, server, login) propagate, so nothing wrong gets cached.
                found = {}
                for tid in batch:
                    try:
                        found.update(lookup([tid]))
                    except (PSNAWPBadRequestError, PSNAWPNotFoundError):
                        pass  # this ID has no trophy list
            for tid in batch:
                result[tid] = found.get(tid)
                _cache[f"title_map:{tid}"] = (time.time(), found.get(tid))
    return result


# ---------- Friends and other players ----------

# PSN accepts up to 100 account IDs per batch request.
ACCOUNT_BATCH = 100
PRESENCE_SECONDS = 60


def _profile_api(path: str, **params):
    """GET from PSN's profile API with the logged-in session (for endpoints psnawp doesn't wrap)."""
    return get_me().authenticator.get(url=f"{BASE_PATH['profile_uri']}{path}", params=params or None).json()


def get_friend_ids() -> list[str]:
    """Account IDs of my friends. One request (psnawp's friends_list() costs one per friend). Cached."""
    return cached("friend_ids", lambda: _profile_api(API_PATH["friends_list"].format(account_id="me"), limit=1000)["friends"])


def get_friend_request_ids() -> list[str]:
    """Account IDs of people who sent me a friend request. Cached."""
    return cached("friend_request_ids", lambda: [
        r["accountId"] for r in _profile_api(API_PATH["friends_request"].format(account_id="me")).get("receivedRequests", [])
    ])


def get_profiles(account_ids: list[str]) -> dict[str, dict]:
    """Basic profiles (onlineId, aboutMe, isPlus, ...) for many accounts, batched. Cached per account for an hour."""
    result, missing = {}, []
    for aid in dict.fromkeys(account_ids):
        hit = _cache.get(f"profile:{aid}")
        if hit and time.time() - hit[0] < HOUR:
            result[aid] = hit[1]
        else:
            missing.append(aid)
    for i in range(0, len(missing), ACCOUNT_BATCH):
        batch = missing[i:i + ACCOUNT_BATCH]
        profiles = _profile_api("/profiles", accountIds=",".join(batch)).get("profiles", [])
        if len(profiles) != len(batch):
            # The batch answer has no account IDs, only the same order. If the counts don't match,
            # fall back to one request per account rather than risk mixing people up.
            profiles = [_profile_api(API_PATH["profiles"].format(account_id=aid)) for aid in batch]
        for aid, prof in zip(batch, profiles):
            result[aid] = prof
            _cache[f"profile:{aid}"] = (time.time(), prof)
    return result


def get_presences(account_ids: list[str]) -> dict[str, dict]:
    """Online status and current game for many accounts, batched. Cached for a minute."""
    result = {}
    ids = list(dict.fromkeys(account_ids))
    for i in range(0, len(ids), ACCOUNT_BATCH):
        batch = ids[i:i + ACCOUNT_BATCH]
        presences = cached(
            "presences:" + ",".join(batch),
            lambda: get_me().get_presences(batch).get("basicPresences", []),
            PRESENCE_SECONDS,
        )
        result.update({p.get("accountId"): p for p in presences})
    return result


def get_player(online_id: str):
    """Another player by exact PSN name (psnawp User). Cached for an hour."""
    return cached(f"player:{online_id.lower()}", lambda: get_psnawp().user(online_id=online_id), HOUR)


def get_player_summary(player):
    """A player's trophy level and counts (psnawp TrophySummary). Cached."""
    return cached(f"player_summary:{player.account_id}", lambda: player.trophy_summary())


def get_player_trophy_titles(player) -> list:
    """Every game on a player's trophy list. Cached."""
    return cached(f"player_titles:{player.account_id}", lambda: list(player.trophy_titles(limit=None)))


def get_player_title_trophies(player, title) -> list:
    """A player's trophies (with earned flags) for one of my games. Cached."""
    return cached(
        f"player_trophies:{player.account_id}:{title.np_communication_id}",
        lambda: list(player.trophies(
            np_communication_id=title.np_communication_id,
            platform=pick_platform(title),
            include_progress=True,
            trophy_group_id="all",
        )),
    )


def forget_login():
    """Drop the saved login so the next call logs in again (e.g. after the NPSSO is renewed)."""
    global _psnawp, _me
    _psnawp = None
    _me = None
