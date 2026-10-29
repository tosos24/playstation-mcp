"""Recap tool: what I did on PlayStation in the last week, month or this year."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from psn_client import get_title_trophies, get_trophy_titles

from tools.common import display_name, read_only_tool
from tools.games import build_games

TROPHY_TYPES = ("platinum", "gold", "silver", "bronze")
# Each game's trophies cost a request (~6 s), so a recap checks at most this many games.
MAX_RECAP_GAMES = 12


def _period_start(period: str, now: datetime) -> datetime:
    """Start of the period: last 7 days, last 30 days, or since 1 January this year (UTC)."""
    if period == "week":
        return now - timedelta(days=7)
    if period == "month":
        return now - timedelta(days=30)
    return datetime(now.year, 1, 1, tzinfo=timezone.utc)


def register(mcp):
    tool = read_only_tool(mcp)

    @tool
    def get_recap(period: Literal["week", "month", "year"] = "month") -> dict:
        """Summarise my PlayStation activity: trophies earned, games played and games started.

        period: "week" = last 7 days, "month" = last 30 days, "year" = since 1 January this year.
        Use for "what did I do on PlayStation this month?", "my year in review", "how many
        trophies did I get this week?".

        PSN only has total hours per game, not hours per period, so the recap lists the games
        played in the period without hours. For all-time rankings use list_games.
        """
        now = datetime.now(timezone.utc)
        start = _period_start(period, now)

        # Trophies: only games whose last trophy falls in the period can have new ones.
        active = sorted((t for t in get_trophy_titles() if t.last_updated_datetime and t.last_updated_datetime >= start),
                        key=lambda t: t.last_updated_datetime, reverse=True)
        checked = active[:MAX_RECAP_GAMES]
        counts = dict.fromkeys(TROPHY_TYPES, 0)
        by_game, platinums, rarest = [], [], None
        for game in checked:
            earned = [tr for tr in get_title_trophies(game)
                      if tr.earned and tr.earned_date_time and tr.earned_date_time >= start]
            if not earned:
                continue
            by_game.append({"game": display_name(game.title_name), "trophies": len(earned),
                            "progress_percent_now": game.progress or 0})
            for tr in earned:
                kind = tr.trophy_type.value if tr.trophy_type else None
                if kind in counts:
                    counts[kind] += 1
                if kind == "platinum":
                    platinums.append(display_name(game.title_name))
                rate = float(tr.trophy_earn_rate) if tr.trophy_earn_rate is not None else None
                if rate is not None and (rarest is None or rate < rarest["earn_rate"]):
                    rarest = {"name": tr.trophy_name, "game": display_name(game.title_name),
                              "type": kind, "earn_rate": rate}
        by_game.sort(key=lambda g: g["trophies"], reverse=True)

        # Games: the same merged records as list_games (PS4/PS5 play time, trophy-list names, no apps).
        records, _ = build_games()
        played = {r["name"] for r in records if r["last_played"] and r["last_played"] >= start}
        started = {r["name"] for r in records if r["first_played"] and r["first_played"] >= start}

        result = {
            "period": period,
            "from": start.date().isoformat(),
            "to": now.date().isoformat(),
            "trophies_earned": {**counts, "total": sum(counts.values())},
            "trophies_by_game": by_game,
            "platinums_earned": platinums,
            "rarest_trophy": rarest,
            "games_played": sorted(played, key=str.lower),
            "games_started": sorted(started, key=str.lower),
            "note": "Games played are PS4/PS5 only (PSN has no hours per period).",
        }
        if len(active) > len(checked):
            result["note"] += (f" Trophies were counted in my {len(checked)} most recently active games only; "
                               f"{len(active) - len(checked)} more games had trophy activity in this period.")
        return result
