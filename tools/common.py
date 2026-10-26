"""Shared helpers for all tool modules: finding games, describing them, and the tool decorator."""

import difflib
import functools
import re

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from psnawp_api.core.psnawp_exceptions import (
    PSNAWPAuthenticationError,
    PSNAWPError,
    PSNAWPForbiddenError,
    PSNAWPInvalidTokenError,
    PSNAWPNotFoundError,
    PSNAWPTooManyRequestsError,
    PSNAWPUnauthorizedError,
)

from psn_client import forget_login, get_trophy_titles

# Every tool here only reads data, and talks to PSN (an outside service).
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)


def _friendly_message(exc: PSNAWPError) -> str:
    """Turn a PSN error into a message that tells Claude (and me) what to do next."""
    if isinstance(exc, (PSNAWPAuthenticationError, PSNAWPInvalidTokenError, PSNAWPUnauthorizedError)):
        forget_login()
        return ("PSN login failed: the NPSSO token has probably expired. "
                "Get a new one and update NPSSO in the .env file or Render's Environment tab.")
    if isinstance(exc, PSNAWPForbiddenError):
        return "PSN refused: this profile or data is private."
    if isinstance(exc, PSNAWPNotFoundError):
        return "PSN has no data for that (it may not exist, or nothing has been earned/played yet)."
    if isinstance(exc, PSNAWPTooManyRequestsError):
        return "PSN rate limit reached. Wait a few minutes and try again."
    return f"PSN request failed: {exc}"


def read_only_tool(mcp):
    """Like @mcp.tool(), but marks the tool read-only and turns PSN errors into clear messages."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except PSNAWPError as exc:
                raise ToolError(_friendly_message(exc)) from exc
        return mcp.tool(annotations=READ_ONLY)(wrapper)
    return decorator


def normalize(text: str) -> str:
    """Lowercase and strip symbols like ™ ® : so names compare loosely."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def find_games(game_name: str) -> list:
    """Find games on my list matching a name: exact first, then partial, then close spellings."""
    titles = get_trophy_titles()
    wanted = normalize(game_name)
    by_name = {}
    for t in titles:
        by_name.setdefault(normalize(t.title_name), []).append(t)

    if wanted in by_name:
        return by_name[wanted]
    # Partial match, ignoring spaces too, so "spiderman 2" finds "Spider-Man 2".
    squash = lambda s: s.replace(" ", "")
    partial = [t for t in titles if squash(wanted) in squash(normalize(t.title_name))]
    if partial:
        return partial
    close = difflib.get_close_matches(wanted, by_name.keys(), n=5, cutoff=0.6)
    return [t for name in close for t in by_name[name]]


def game_summary(t) -> dict:
    """Small, clean description of one game on my trophy list."""
    return {
        "game": t.title_name,
        "platforms": sorted(p.value for p in t.title_platform),
        "progress_percent": t.progress or 0,
        "has_platinum": bool(t.defined_trophies.platinum),
        "platinum_earned": bool(t.earned_trophies.platinum),
        "has_dlc_trophies": bool(t.has_trophy_groups),
    }


def resolve_one_game(game_name: str):
    """Return (game, None) for a single match, or (None, reply) where reply is an error or a list of matches."""
    games = find_games(game_name)
    if not games:
        return None, {"error": f"No game on my trophy list matches '{game_name}'."}
    if len(games) > 1:
        return None, {"matches": [game_summary(t) for t in games],
                      "note": "Several games match. Ask which one, then call again with its exact name."}
    return games[0], None
