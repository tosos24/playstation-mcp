"""Friends tools: who's online, another player's profile, and comparing trophies with them.

PSN's privacy settings apply: a player can hide their trophies or online status, and
the tools report that ("private") instead of failing.
"""

import re

from psnawp_api.core.psnawp_exceptions import PSNAWPForbiddenError, PSNAWPNotFoundError

from psn_client import (
    get_friend_ids,
    get_friend_request_ids,
    get_player,
    get_player_by_account_id,
    get_player_summary,
    get_player_title_trophies,
    get_player_trophy_titles,
    get_presences,
    get_profile,
    get_profiles,
    get_title_trophies,
    get_trophy_titles,
)

from tools.common import display_name, read_only_tool, resolve_one_game

TROPHY_TYPES = ("platinum", "gold", "silver", "bronze")
# How many trophy names to list on each side of a per-game comparison.
MAX_LISTED = 15
# How many shared games to show in an overall comparison.
MAX_SHARED = 10


def _presence_fields(presence: dict | None) -> dict:
    """Online status, console and current game from a PSN presence record."""
    if not presence:
        return {"status": "hidden", "platform": None, "playing": None, "last_online": None}
    info = presence.get("primaryPlatformInfo") or {}
    games = presence.get("gameTitleInfoList") or []
    return {
        "status": info.get("onlineStatus") or "unknown",
        "platform": info.get("platform"),
        "playing": games[0].get("titleName") if games else None,
        "last_online": info.get("lastOnlineDate") or presence.get("lastAvailableDate"),
    }


def _friends_by_name() -> dict[str, str]:
    """My friends' PSN names -> account IDs."""
    ids = get_friend_ids()
    profiles = get_profiles(ids)
    return {profiles[aid].get("onlineId"): aid for aid in ids if profiles.get(aid, {}).get("onlineId")}


def _name_key(name: str) -> str:
    """A PSN name for loose matching: lowercase letters and digits only.

    Not tools.common.normalize: that maps Roman numerals for game names, which would turn "Dark_V" into "dark5".
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _resolve_player(name: str):
    """Find a player: my friends first (loose name match), then any exact PSN name.

    Returns (user, is_friend, None) or (None, None, reply) where reply is an error or a list of matches.
    """
    wanted = _name_key(name)
    if not wanted:
        return None, None, {"error": "Give a PSN name (online ID)."}
    friends = _friends_by_name()
    exact = [n for n in friends if _name_key(n) == wanted]
    partial = exact or [n for n in friends if wanted in _name_key(n)]
    if len(partial) > 1:
        return None, None, {"matches": sorted(partial),
                            "note": "Several friends match. Ask which one, then call again with the exact name."}
    if partial:
        return get_player_by_account_id(friends[partial[0]]), True, None
    try:
        return get_player(name.strip()), False, None
    except PSNAWPNotFoundError:
        return None, None, {"error": f"No friend or PSN player called '{name}'. PSN names must be exact for non-friends."}


def _counts(trophy_set) -> dict:
    counts = {k: getattr(trophy_set, k) for k in TROPHY_TYPES}
    counts["total"] = sum(counts.values())
    return counts


def register(mcp):
    tool = read_only_tool(mcp)

    @tool
    def list_friends(online_only: bool = False) -> dict:
        """List my PSN friends with who's online, on which console, and what they're playing.

        Use for "who's online?", "what are my friends playing?", "who are my friends?",
        "do I have friend requests?". Online friends come first, then by last time online.
        status "hidden" means that friend hides their online status.

        For one player's profile and trophies use get_player; to compare trophies use
        compare_with_player.
        """
        ids = get_friend_ids()
        profiles = get_profiles(ids)
        presences = get_presences(ids)
        friends = []
        for aid in ids:
            prof = profiles.get(aid, {})
            friends.append({
                "online_id": prof.get("onlineId"),
                **_presence_fields(presences.get(aid)),
                "ps_plus": bool(prof.get("isPlus")),
            })
        online = [f for f in friends if f["status"] == "online"]
        friends.sort(key=lambda f: (f["status"] == "online", f["last_online"] or ""), reverse=True)

        request_ids = get_friend_request_ids()
        request_names = [p.get("onlineId") for p in get_profiles(request_ids).values()] if request_ids else []
        return {
            "count": len(friends),
            "online_count": len(online),
            "friends": [f for f in friends if f["status"] == "online"] if online_only else friends,
            "friend_requests_received": request_names,
        }

    @tool
    def get_player(name: str) -> dict:
        """Look up a PSN player (a friend, or anyone by exact PSN name): profile, online status,
        trophy level and the games they've played most recently.

        Use for "what level is X?", "what's X been playing?", "is X online?". Friends can be named
        loosely; other players need their exact PSN name. If several friends match, this returns
        "matches": ask which one.

        Players can hide their trophies; then "trophies" says "private".
        """
        user, is_friend, reply = _resolve_player(name)
        if reply:
            return reply
        prof = get_profiles([user.account_id]).get(user.account_id, {})
        result = {
            "online_id": user.online_id,
            "is_friend": is_friend,
            "about_me": prof.get("aboutMe") or None,
            "ps_plus": bool(prof.get("isPlus")),
            **_presence_fields(get_presences([user.account_id]).get(user.account_id)),
        }
        try:
            summary = get_player_summary(user)
            titles = get_player_trophy_titles(user)
            result["trophy_level"] = summary.trophy_level
            result["trophies"] = _counts(summary.earned_trophies)
            result["games_on_trophy_list"] = len(titles)
            recent = sorted(titles, key=lambda t: t.last_updated_datetime.isoformat() if t.last_updated_datetime else "",
                            reverse=True)[:5]
            result["recent_games"] = [
                {"game": display_name(t.title_name), "platforms": sorted(p.value for p in t.title_platform),
                 "progress_percent": t.progress or 0,
                 "last_trophy": t.last_updated_datetime.date().isoformat() if t.last_updated_datetime else None}
                for t in recent
            ]
        except PSNAWPForbiddenError:
            result["trophies"] = "private"
        return result

    @tool
    def compare_with_player(name: str, game_name: str | None = None) -> dict:
        """Compare my trophies with a friend's (or any player's, by exact PSN name).

        - Without game_name: trophy levels and totals side by side, plus our shared games with
          both our progress.
        - With game_name (one of my games, loose name): which trophies we both have, only I have,
          and only they have.

        Use for "how do I compare with X?", "who's further in Spider-Man 2, me or X?",
        "which trophies does X have that I don't in Y?". If they hide their trophies, the
        result says "private".
        """
        user, _, reply = _resolve_player(name)
        if reply:
            return reply

        if game_name:
            game, reply = resolve_one_game(game_name)
            if reply:
                return reply
            mine = {t.trophy_id: t for t in get_title_trophies(game)}
            try:
                theirs = {t.trophy_id: t for t in get_player_title_trophies(user, game)}
            except PSNAWPForbiddenError:
                return {"game": display_name(game.title_name), "player": user.online_id, "their_trophies": "private"}
            except PSNAWPNotFoundError:
                return {"game": display_name(game.title_name), "player": user.online_id,
                        "note": f"{user.online_id} hasn't played this game (no trophies for it)."}
            me_ids = {i for i, t in mine.items() if t.earned}
            them_ids = {i for i, t in theirs.items() if t.earned}
            names = lambda ids: [mine[i].trophy_name for i in sorted(ids) if i in mine][:MAX_LISTED]
            return {
                "game": display_name(game.title_name),
                "player": user.online_id,
                "total_trophies": len(mine),
                "i_earned": len(me_ids),
                "they_earned": len(them_ids),
                "both_earned": len(me_ids & them_ids),
                "only_i_have": names(me_ids - them_ids),
                "only_they_have": names(them_ids - me_ids),
                "note": "Counts are trophies, not PSN's points-weighted %. Lists show up to 15 names each.",
            }

        mine = get_profile().get("trophySummary", {})
        my_counts = {k: (mine.get("earnedTrophies") or {}).get(k, 0) for k in TROPHY_TYPES}
        my_counts["total"] = sum(my_counts.values())
        result = {"player": user.online_id, "my_level": mine.get("level")}
        try:
            summary = get_player_summary(user)
            their_titles = get_player_trophy_titles(user)
        except PSNAWPForbiddenError:
            return {**result, "my_trophies": my_counts, "their_trophies": "private"}
        result.update({"their_level": summary.trophy_level, "my_trophies": my_counts,
                       "their_trophies": _counts(summary.earned_trophies)})

        my_titles = {t.np_communication_id: t for t in get_trophy_titles()}
        shared = [(my_titles[t.np_communication_id], t) for t in their_titles if t.np_communication_id in my_titles]
        latest = lambda pair: max(x.last_updated_datetime.isoformat() if x.last_updated_datetime else "" for x in pair)
        shared.sort(key=latest, reverse=True)
        result["shared_games_count"] = len(shared)
        result["shared_games"] = [
            {"game": display_name(m.title_name), "my_progress_percent": m.progress or 0,
             "their_progress_percent": t.progress or 0}
            for m, t in shared[:MAX_SHARED]
        ]
        result["note"] = f"shared_games shows the {MAX_SHARED} most recently played by either of us."
        return result
