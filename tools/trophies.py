"""Trophy tools: finding individual trophies (missing, recent, rarest, by name)."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from psn_client import get_title_trophies, get_trophy_titles

from tools.common import display_name, game_summary, normalize, read_only_tool, resolve_one_game

# Fetching one game's trophies costs ~6 s (psnawp waits between requests), so cap scans across games.
MAX_SCAN_GAMES = 5
# For "my latest trophies" the newest trophies are almost always in the first few games.
MAX_RECENT_SCAN_GAMES = 10

OLDEST = datetime.min.replace(tzinfo=timezone.utc)
# PSN's per-game "last trophy" time is sometimes rounded ~1 s earlier than the trophy itself.
CLOCK_SLACK = timedelta(minutes=1)


def _trophy_dict(tr, game_name: str | None = None) -> dict:
    """Small, clean description of one trophy."""
    d = {
        "name": tr.trophy_name,
        "description": tr.trophy_detail,
        "type": tr.trophy_type.value if tr.trophy_type else None,
        # PSN sends this as text like "6.6", so turn it into a number.
        "earn_rate": float(tr.trophy_earn_rate) if tr.trophy_earn_rate is not None else None,
        "earned": bool(tr.earned),
        "earned_at": tr.earned_date_time.isoformat() if tr.earned_date_time else None,
        "hidden": bool(tr.trophy_hidden),
        "dlc": tr.trophy_group_id not in (None, "default"),
    }
    if game_name:
        d = {"game": game_name, **d}
    return d


def _keep(tr, status: str, search: str | None, include_dlc: bool) -> bool:
    """Should this trophy be in the answer?"""
    if status == "earned" and not tr.earned:
        return False
    if status == "unearned" and tr.earned:
        return False
    if not include_dlc and tr.trophy_group_id not in (None, "default"):
        return False
    if search:
        kind = tr.trophy_type.value if tr.trophy_type else ""
        text = normalize(f"{tr.trophy_name} {tr.trophy_detail} {kind}")
        if normalize(search) not in text:
            return False
    return True


def _sort(trophies: list[dict], sort_by: str) -> None:
    """Sort trophy dicts in place."""
    if sort_by == "recent":
        trophies.sort(key=lambda t: t["earned_at"] or "", reverse=True)
    elif sort_by == "rarest":
        trophies.sort(key=lambda t: t["earn_rate"] if t["earn_rate"] is not None else 100)
    else:  # easiest: most players have it
        trophies.sort(key=lambda t: t["earn_rate"] if t["earn_rate"] is not None else -1, reverse=True)


def _games_by_last_trophy() -> list:
    """My games, the one I most recently earned a trophy in first."""
    return sorted(get_trophy_titles(), key=lambda t: t.last_updated_datetime or OLDEST, reverse=True)


def register(mcp):
    tool = read_only_tool(mcp)

    @tool
    def get_trophies(
        game_name: str | None = None,
        status: Literal["unearned", "earned", "all"] = "unearned",
        sort_by: Literal["easiest", "rarest", "recent"] | None = None,
        search: str | None = None,
        include_dlc: bool = True,
        limit: int = 20,
    ) -> dict:
        """Find individual PlayStation trophies: missing ones, recently earned ones, rarest, or one by name.

        Examples:
        - "What am I missing in Spider-Man 2?" -> game_name="spider-man 2" (defaults: unearned, easiest first)
        - "What was my latest trophy?" -> status="earned", sort_by="recent", limit=1
        - "Latest trophy in Ratchet?" -> game_name="ratchet", status="earned", sort_by="recent", limit=1
        - "When did I get the platinum in X?" -> game_name="X", status="earned", search="platinum"
          (search matches trophy name, description and type, e.g. "platinum" or "gold")
        - "My rarest trophies?" -> status="earned", sort_by="rarest"

        game_name can be loose ("spiderman 2"). If several games match, this returns
        "matches" instead: ask which one, then call again with the exact name. Add the console
        to pick between same-named lists, e.g. "Ghost Game PS5" (ps3, ps4, ps5, vita).

        sort_by: "easiest" = most players have it, "rarest" = fewest players have it,
        "recent" = newest earned first. Default: "recent" for earned, "easiest" otherwise.

        Without game_name, only my most recently active games are searched (see "note"
        and "games_searched" in the result). For whole-library rankings of games, use
        list_games instead.

        Each trophy has: name, description, type (bronze/silver/gold/platinum),
        earn_rate (% of all players who have it), earned, earned_at (UTC), hidden, dlc.
        """
        sort_by = sort_by or ("recent" if status == "earned" else "easiest")
        limit = max(1, limit)

        # One game: everything comes from a single (cached) lookup.
        if game_name:
            game, reply = resolve_one_game(game_name)
            if reply:
                return reply
            found = [_trophy_dict(tr) for tr in get_title_trophies(game)
                     if _keep(tr, status, search, include_dlc)]
            _sort(found, sort_by)
            return {**game_summary(game), "count": len(found), "trophies": found[:limit]}

        games = _games_by_last_trophy()

        # Platinums are special: the game list already says which games have one earned (or not),
        # so jump straight to those games instead of guessing.
        if search and normalize(search) == "platinum":
            want_earned = status == "earned"
            games = [g for g in games if g.defined_trophies.platinum
                     and (status == "all" or bool(g.earned_trophies.platinum) == want_earned)]

        # Latest earned trophies across all games. Each game knows when I last earned a trophy
        # in it, so walk games newest-first and stop once no older game could beat what we have.
        if status == "earned" and sort_by == "recent":
            found, searched, capped = [], [], False
            for game in games:
                # A trophy with no earned date sorts last, so any dated one could still beat it: keep going.
                if len(found) >= limit and found[limit - 1]["earned_at"]:
                    cutoff = datetime.fromisoformat(found[limit - 1]["earned_at"])
                    last = game.last_updated_datetime
                    if last is None or last + CLOCK_SLACK < cutoff:
                        break
                # This game could still have newer trophies, but we've searched enough games.
                if len(searched) == MAX_RECENT_SCAN_GAMES:
                    capped = True
                    break
                searched.append(display_name(game.title_name))
                found += [_trophy_dict(tr, display_name(game.title_name)) for tr in get_title_trophies(game)
                          if _keep(tr, status, search, include_dlc)]
                _sort(found, "recent")
            reply = {"games_searched": searched}
            if capped:
                reply["note"] = (f"Only searched my {len(searched)} most recently active games, to stay fast. "
                                 "Pass game_name to search a specific game.")
            return {**reply, "count": len(found), "trophies": found[:limit]}

        # Anything else across games: check only the most recently active ones, to stay fast.
        if status == "unearned":
            games = [g for g in games if (g.progress or 0) < 100]
        scope = games[:MAX_SCAN_GAMES]
        found = []
        for game in scope:
            found += [_trophy_dict(tr, display_name(game.title_name)) for tr in get_title_trophies(game)
                      if _keep(tr, status, search, include_dlc)]
        _sort(found, sort_by)
        return {
            "games_searched": [display_name(g.title_name) for g in scope],
            "note": (f"Only searched my {len(scope)} most recently active games, to stay fast. "
                     "Pass game_name to search a specific game."),
            "count": len(found),
            "trophies": found[:limit],
        }
