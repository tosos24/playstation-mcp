"""Game tools: list and rank my games (trophies + play time + library), and details for one game.

PSN keeps three separate lists that don't share IDs: my trophy list, my play time
(PS4/PS5 only) and my library (games I own). build_games() joins them into one
record per game.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from psn_client import get_library, get_title_stats, get_trophy_groups, get_trophy_titles, map_title_ids

from tools.common import display_name, normalize, read_only_tool, resolve_one_game

# A game counts as "abandoned" if it's unfinished and I haven't played it for this long.
ABANDONED_DAYS = 90

# Library package types -> console.
PACKAGE_PLATFORM = {"PS4GD": "PS4", "PSGD": "PS5"}

TROPHY_TYPES = ("platinum", "gold", "silver", "bronze")

OLDEST = datetime.min.replace(tzinfo=timezone.utc)

# PSN play time only exists from the PS4 on, so no total can be longer than the time since its launch.
PS4_LAUNCH = datetime(2013, 11, 15, tzinfo=timezone.utc)


def _name_key(name: str) -> str:
    """Loose name for matching across PSN's lists ("EA SPORTS FC™ 26 Trophies" -> "ea sports fc 26")."""
    return normalize(name).removesuffix(" trophies").strip()


def _platform_of(stat) -> str | None:
    """Console of a play-time entry ("PS4"/"PS5"), or None if PSN doesn't say (mostly apps)."""
    # PlatformCategory values are "ps4_game" / "ps5_native_game"; the enum names are PS4 / PS5.
    name = getattr(stat.category, "name", None)
    return name if name in ("PS4", "PS5") else None


def _new_record(t, name: str | None = None) -> dict:
    """Start a game record from its trophy list entry (t), or from just a name for games without one."""
    return {
        "title": t,
        "name": display_name(t.title_name) if t else name,
        "platforms": {p.value for p in t.title_platform} if t else set(),
        "play": timedelta(0),
        "play_count": 0,
        "first_played": None,
        "last_played": None,
        "has_play_data": False,
        "bad_play_time": False,
        "title_ids": set(),
    }


def _add_play(rec: dict, stat) -> None:
    """Add one play-time entry to a record (a game can have PS4 and PS5 entries)."""
    rec["has_play_data"] = True
    duration = stat.play_duration or timedelta(0)
    # PSN sometimes reports impossible totals (e.g. 50+ years). Ignore any longer than the time since
    # first played, or since the PS4 launch (also when there's no first-played date).
    started = stat.first_played_date_time
    now = datetime.now(timezone.utc)
    if duration > now - PS4_LAUNCH or (started and duration > now - started):
        rec["bad_play_time"] = True
    else:
        rec["play"] += duration
    rec["play_count"] += stat.play_count or 0
    if started and (rec["first_played"] is None or started < rec["first_played"]):
        rec["first_played"] = started
    last = stat.last_played_date_time
    if last and (rec["last_played"] is None or last > rec["last_played"]):
        rec["last_played"] = last
    if _platform_of(stat):
        rec["platforms"].add(_platform_of(stat))
    if stat.title_id:
        rec["title_ids"].add(stat.title_id)


def build_games() -> tuple[list[dict], list[str]]:
    """Join trophy list + play time into one record per game.

    Played PS4/PS5 games that aren't on my trophy list (no trophy earned yet) get a record without
    trophy data. Returns (records, skipped) where skipped are titles PSN doesn't mark as PS4/PS5
    games: apps like Netflix.
    """
    titles = get_trophy_titles()
    records = {t.np_communication_id: _new_record(t) for t in titles}
    by_name: dict[str, list] = {}
    for t in titles:
        by_name.setdefault(_name_key(t.title_name), []).append(t)

    # 1. Match play time to trophy lists by name (free). Prefer the same console if a game has two lists.
    unmatched = []
    for stat in get_title_stats():
        candidates = by_name.get(_name_key(stat.name or ""), [])
        same_console = [t for t in candidates if _platform_of(stat) in {p.value for p in t.title_platform}]
        match = (same_console or candidates or [None])[0]
        if match:
            _add_play(records[match.np_communication_id], stat)
        else:
            unmatched.append(stat)

    # 2. Look the rest up by ID ("Alan Wake 2" is "Alan Wake II" on the trophy list). Cached for a day.
    id_map = map_title_ids([s.title_id for s in unmatched if s.title_id])
    no_trophy_list: dict[str, dict] = {}
    skipped = []
    for stat in unmatched:
        npid = id_map.get(stat.title_id)
        if npid in records:
            _add_play(records[npid], stat)
        elif _platform_of(stat):
            # A real PS4/PS5 game, just not on my trophy list. Merge its PS4 + PS5 entries by name.
            key = _name_key(stat.name or "")
            rec = no_trophy_list.setdefault(key, _new_record(None, stat.name))
            _add_play(rec, stat)
        elif stat.name and stat.name not in skipped:
            skipped.append(stat.name)
    return list(records.values()) + list(no_trophy_list.values()), skipped


def _date(dt: datetime | None) -> str | None:
    return dt.date().isoformat() if dt else None


def _progress(rec: dict) -> int | None:
    """Trophy %, or None for games not on my trophy list."""
    return (rec["title"].progress or 0) if rec["title"] else None


def _row(rec: dict) -> dict:
    """Small, clean description of one game for Claude."""
    t = rec["title"]
    row = {
        "game": rec["name"],
        "platforms": sorted(rec["platforms"]),
        "trophy_progress_percent": _progress(rec),
        "has_platinum": bool(t.defined_trophies.platinum) if t else None,
        "platinum_earned": bool(t.earned_trophies.platinum) if t else None,
        "hours_played": round(rec["play"].total_seconds() / 3600, 1) if rec["has_play_data"] else None,
        "play_count": rec["play_count"] if rec["has_play_data"] else None,
        "first_played": _date(rec["first_played"]),
        "last_played": _date(rec["last_played"]),
        "last_trophy": _date(t.last_updated_datetime) if t else None,
    }
    if not t:
        row["note"] = "Not on my trophy list (no trophy earned yet)."
    if rec["bad_play_time"]:
        row["play_time_warning"] = "PSN reported an impossible play time for part of this game; that part is left out."
    return row


def _never_played(records: list[dict]) -> list[dict]:
    """Games in my library with no play time and no earned trophy (one entry per game, even if owned on PS4 and PS5)."""
    stats = get_title_stats()
    played_ids = {s.title_id for s in stats if s.title_id}
    played_names = {_name_key(s.name or "") for s in stats}
    played_names |= {_name_key(r["name"]) for r in records if r["has_play_data"]}
    # An earned trophy means I played it, even if PSN has no play time for it.
    played_names |= {_name_key(r["name"]) for r in records
                     if r["title"] and any(getattr(r["title"].earned_trophies, k) for k in TROPHY_TYPES)}

    games: dict[str, dict] = {}
    for item in get_library():
        meta = item.get("gameMeta") or {}
        name = meta.get("name") or (item.get("titleMeta") or {}).get("name")
        if not name:
            continue
        found = re.search(r"[A-Z]{4}\d{5}_00", item.get("productId") or "")
        key = _name_key(name)
        if (found and found.group(0) in played_ids) or key in played_names:
            continue
        game = games.setdefault(key, {"game": name, "platforms": set()})
        if PACKAGE_PLATFORM.get(meta.get("packageType")):
            game["platforms"].add(PACKAGE_PLATFORM[meta.get("packageType")])
    return sorted(({"game": g["game"], "platforms": sorted(g["platforms"])} for g in games.values()),
                  key=lambda g: g["game"].lower())


SORT_KEYS = {
    "trophy_progress": lambda r: _progress(r) if r["title"] else -1,
    "playtime": lambda r: r["play"] if r["has_play_data"] else timedelta(-1),
    "recently_played": lambda r: r["last_played"] or OLDEST,
    "last_trophy": lambda r: (r["title"].last_updated_datetime if r["title"] else None) or OLDEST,
    "play_count": lambda r: r["play_count"] if r["has_play_data"] else -1,
}


def _is_abandoned(rec: dict, now: datetime) -> bool:
    """Unfinished, and not played (or no trophy earned, if there's no play data) for ABANDONED_DAYS."""
    if (_progress(rec) or 0) >= 100:
        return False
    last = rec["last_played"] or (rec["title"].last_updated_datetime if rec["title"] else None)
    return last is not None and now - last > timedelta(days=ABANDONED_DAYS)


def register(mcp):
    tool = read_only_tool(mcp)

    @tool
    def list_games(
        sort_by: Literal["trophy_progress", "playtime", "recently_played", "last_trophy", "play_count"] = "trophy_progress",
        filter: Literal["unfinished", "all", "completed", "platinum_earned", "abandoned", "never_played"] | None = None,
        limit: int = 20,
    ) -> dict:
        """List and rank my PlayStation games by trophy progress, play time or recency.

        Examples:
        - "Which games am I closest to platinum on?" -> sort_by="trophy_progress" (default filter: unfinished)
        - "My most played games?" -> sort_by="playtime"
        - "Most hours but never finished?" -> sort_by="playtime", filter="unfinished"
        - "What have I been playing lately?" -> sort_by="recently_played"
        - "Which games did I abandon?" -> filter="abandoned"
        - "What have I bought but never played?" -> filter="never_played"
        - "My completed games / platinums?" -> filter="completed" or "platinum_earned"

        filter defaults to "unfinished" when sorting by trophy_progress, otherwise "all".
        "abandoned" = under 100% and not played for 90+ days. "never_played" lists games I own with
        no play time at all (sort_by is ignored; may include PS Plus games I claimed).

        For one game in depth use get_game_details; for individual trophies use get_trophies.

        Notes: trophy_progress_percent is weighted by trophy points and includes DLC. hours_played
        and play_count only exist for PS4/PS5 games (null otherwise). Games I played but earned no
        trophy in have null trophy fields. Apps like Netflix are left out.
        """
        limit = max(1, limit)
        records, skipped = build_games()

        if filter == "never_played":
            games = _never_played(records)
            return {"filter": filter, "count": len(games), "games": games[:limit]}

        filter = filter or ("unfinished" if sort_by == "trophy_progress" else "all")
        now = datetime.now(timezone.utc)
        keep = {
            "all": lambda r: True,
            "unfinished": lambda r: r["title"] is not None and _progress(r) < 100,
            "completed": lambda r: r["title"] is not None and _progress(r) >= 100,
            "platinum_earned": lambda r: r["title"] is not None and bool(r["title"].earned_trophies.platinum),
            "abandoned": lambda r: _is_abandoned(r, now),
        }[filter]
        chosen = sorted((r for r in records if keep(r)), key=SORT_KEYS[sort_by], reverse=True)
        result = {"sort_by": sort_by, "filter": filter, "count": len(chosen),
                  "games": [_row(r) for r in chosen[:limit]]}
        if sort_by in ("playtime", "recently_played", "play_count"):
            result["note"] = ("Play time is PS4/PS5 only. Titles PSN doesn't label as PS4/PS5 games are left out "
                              "(mostly apps like Netflix, but a few games too): see left_out.")
            if skipped:
                result["left_out"] = skipped
        return result

    @tool
    def get_game_details(game_name: str) -> dict:
        """Everything about one of my games: trophy progress (base game vs each DLC), hours, play count,
        first and last played.

        Use for "tell me about my Spider-Man 2", "how many hours in X?", "is it the DLC holding me
        back in X?". game_name can be loose. If several games match, this returns "matches":
        ask which one, then call again with the exact name.

        Only works for games on my trophy list. For games I don't own, use web search.
        For the individual trophies in a game, use get_trophies.
        """
        game, reply = resolve_one_game(game_name)
        if reply:
            if "error" in reply:
                reply["error"] += " For games I don't own, use web search."
            return reply

        records, _ = build_games()
        rec = next(r for r in records if r["title"] and r["title"].np_communication_id == game.np_communication_id)
        details = _row(rec)

        groups = []
        for g in get_trophy_groups(game).trophy_groups:
            groups.append({
                "name": g.trophy_group_name,
                "dlc": g.trophy_group_id not in (None, "default"),
                "progress_percent": g.progress or 0,
                "earned": {k: getattr(g.earned_trophies, k) for k in TROPHY_TYPES},
                "total": {k: getattr(g.defined_trophies, k) for k in TROPHY_TYPES},
            })
        details["trophy_groups"] = groups
        details["trophies_earned"] = {k: getattr(game.earned_trophies, k) for k in TROPHY_TYPES}
        details["trophies_total"] = {k: getattr(game.defined_trophies, k) for k in TROPHY_TYPES}
        if not rec["has_play_data"]:
            details["note"] = "No play time from PSN for this game (only PS4/PS5 games have it)."
        return details
