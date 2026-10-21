"""Trophy tools: completion progress and missing trophies."""

import difflib
import re

from psn_client import get_title_trophies, get_trophy_titles


def _normalize(name: str) -> str:
    """Lowercase and strip symbols like ™ ® : so names compare loosely."""
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _find_games(game_name: str) -> list:
    """Find games on my list matching a name: exact first, then partial, then close spellings."""
    titles = get_trophy_titles()
    wanted = _normalize(game_name)
    by_name = {}
    for t in titles:
        by_name.setdefault(_normalize(t.title_name), []).append(t)

    if wanted in by_name:
        return by_name[wanted]
    # Partial match, ignoring spaces too, so "spiderman 2" finds "Spider-Man 2".
    squash = lambda s: s.replace(" ", "")
    partial = [t for t in titles if squash(wanted) in squash(_normalize(t.title_name))]
    if partial:
        return partial
    close = difflib.get_close_matches(wanted, by_name.keys(), n=5, cutoff=0.6)
    return [t for name in close for t in by_name[name]]


def _summary(t) -> dict:
    """Small, clean description of one game."""
    return {
        "game": t.title_name,
        "platforms": sorted(p.value for p in t.title_platform),
        "progress_percent": t.progress or 0,
        "has_platinum": bool(t.defined_trophies.platinum),
        "platinum_earned": bool(t.earned_trophies.platinum),
        "has_dlc_trophies": bool(t.has_trophy_groups),
    }


def register(mcp):
    @mcp.tool()
    def get_trophy_progress(limit: int = 20, include_completed: bool = False) -> list[dict]:
        """List my PlayStation games by trophy completion %, highest first.

        Use this for questions like "which games am I closest to platinum on?" or
        "what are my most completed games?". By default games already at 100% are
        left out; set include_completed=True to include them.

        Notes: PSN's % is weighted by trophy points (a gold counts more than a
        bronze) and includes DLC trophies. Some games have no platinum at all
        (see has_platinum). Trophy % is not the same as finishing the story.
        """
        titles = get_trophy_titles()
        if not include_completed:
            titles = [t for t in titles if (t.progress or 0) < 100]
        titles = sorted(titles, key=lambda t: t.progress or 0, reverse=True)
        return [_summary(t) for t in titles[:limit]]

    @mcp.tool()
    def get_missing_trophies(game_name: str, include_dlc: bool = True) -> dict:
        """List the trophies I haven't earned yet for one game, easiest (most earned) first.

        game_name can be loose ("spiderman 2", "ratchet"). If more than one game
        matches (e.g. PS4 and PS5 versions), this returns "matches" instead, and
        you should ask which one, then call again with the exact name.

        Each trophy has: name, description, type (bronze/silver/gold/platinum),
        earn_rate (% of all players who have it) and whether it's DLC.
        Hidden trophies may show "hidden": true and no useful description.
        """
        games = _find_games(game_name)
        if not games:
            return {"error": f"No game on my trophy list matches '{game_name}'."}
        if len(games) > 1:
            return {"matches": [_summary(t) for t in games]}

        game = games[0]
        missing = []
        for tr in get_title_trophies(game):
            is_dlc = tr.trophy_group_id not in (None, "default")
            if tr.earned or (is_dlc and not include_dlc):
                continue
            missing.append({
                "name": tr.trophy_name,
                "description": tr.trophy_detail,
                "type": tr.trophy_type.value if tr.trophy_type else None,
                # PSN sends this as text like "6.6", so turn it into a number.
                "earn_rate": float(tr.trophy_earn_rate) if tr.trophy_earn_rate is not None else None,
                "hidden": bool(tr.trophy_hidden),
                "dlc": is_dlc,
            })
        # Most-earned first: those are usually the easiest to get.
        missing.sort(key=lambda m: m["earn_rate"] or 0, reverse=True)
        return {**_summary(game), "missing_count": len(missing), "missing": missing}
