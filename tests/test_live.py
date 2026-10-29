"""Smoke tests against the real PSN account. Run once with RUN_LIVE=1 (about 15-25 PSN requests).

They assert invariants only, never values that change as the owner plays.
"""

import json
import re
from datetime import datetime, timezone

import pytest

import psn_client
from tests.fakes import call, call_list
from tools.common import display_name, normalize
from tools.games import _name_key

pytestmark = pytest.mark.live


def recent_titles():
    return sorted((t for t in psn_client.get_trophy_titles() if t.last_updated_datetime),
                  key=lambda t: t.last_updated_datetime, reverse=True)


def unique_recent_game():
    """The most recently played game whose name is unique on the list (so it resolves to one game)."""
    titles = psn_client.get_trophy_titles()
    for t in recent_titles():
        if sum(1 for o in titles if normalize(o.title_name) == normalize(t.title_name)) == 1:
            return t
    pytest.skip("no uniquely named game")


def total(ts):
    return ts.bronze + ts.silver + ts.gold + ts.platinum


def test_progress_sorted_and_unfinished():
    rows = call("list_games", {"limit": 10})["games"]
    assert 0 < len(rows) <= 10
    pct = [r["trophy_progress_percent"] for r in rows]
    assert pct == sorted(pct, reverse=True)
    assert all(p < 100 for p in pct)


def test_one_game_counts_match_psn_totals():
    game = unique_recent_game()
    out = call("get_trophies", {"game_name": game.title_name, "status": "all", "limit": 1000})
    assert out["game"] == display_name(game.title_name)
    assert out["count"] == total(game.defined_trophies) == len(out["trophies"])
    earned = call("get_trophies", {"game_name": game.title_name, "status": "earned", "limit": 1000})
    assert earned["count"] == total(game.earned_trophies)
    assert sum(t["earned"] for t in out["trophies"]) == earned["count"]
    for t in out["trophies"]:
        assert t["earn_rate"] is None or isinstance(t["earn_rate"], float)
        assert t["type"] in ("bronze", "silver", "gold", "platinum")
        assert (t["earned_at"] is not None) == t["earned"]
    times = [t["earned_at"] for t in earned["trophies"]]
    assert times == sorted(times, reverse=True)


def test_latest_trophy_matches_brute_force():
    out = call("get_trophies", {"status": "earned", "sort_by": "recent", "limit": 1})
    assert out["count"] >= 1
    newest = []
    for t in recent_titles()[:3]:
        newest += [(tr.earned_date_time, t.title_name) for tr in psn_client.get_title_trophies(t)
                   if tr.earned and tr.earned_date_time]
    best = max(newest)
    got = out["trophies"][0]
    assert got["earned_at"] == best[0].isoformat()
    assert got["game"] == best[1]
    assert len(out["games_searched"]) <= 3


def test_profile_has_no_private_fields():
    out = call("get_my_profile")
    text = json.dumps(out)
    assert "personalDetail" not in text and "deviceId" not in text
    raw = psn_client.get_profile()
    for name in (raw.get("personalDetail") or {}).values():
        if isinstance(name, str) and len(name) > 2:
            assert name not in text
    for d in psn_client.get_devices():
        if d.get("deviceId"):
            assert d["deviceId"] not in text
    assert re.fullmatch(r"https://profile\.playstation\.com/.+", out["profile_link"])
    assert out["trophies"]["total"] == sum(out["trophies"][k] for k in ("platinum", "gold", "silver", "bronze"))
    assert out["games_on_trophy_list"] == len(psn_client.get_trophy_titles())
    assert isinstance(out["trophy_level"], int)


# ---------- step 2: list_games / get_game_details (about 10-20 more requests, mostly one-off title-ID lookups) ----------

def test_list_games_playtime_sorted_and_possible():
    out = call("list_games", {"sort_by": "playtime", "limit": 10_000})
    rows = out["games"]
    assert out["count"] == len(rows) > 0
    hours = [r["hours_played"] if r["hours_played"] is not None else -1 for r in rows]
    assert hours == sorted(hours, reverse=True)
    now = datetime.now(timezone.utc)
    for r in rows:
        assert not (r["game"] or "").lower().endswith(" trophies"), r["game"]
        if r["hours_played"] and r["first_played"]:
            first = datetime.fromisoformat(r["first_played"]).replace(tzinfo=timezone.utc)
            assert r["hours_played"] <= (now - first).total_seconds() / 3600 + 24, r
        if r["trophy_progress_percent"] is None:
            assert r["has_platinum"] is None and r["last_trophy"] is None
    assert "note" in out
    names = [r["game"] for r in rows]
    assert all(n not in names for n in out.get("left_out", []))


def test_list_games_all_covers_trophy_list():
    out = call("list_games", {"filter": "all", "limit": 10_000})
    with_list = [r for r in out["games"] if r["trophy_progress_percent"] is not None]
    assert len(with_list) == len(psn_client.get_trophy_titles())
    pct = [r["trophy_progress_percent"] for r in with_list]
    assert pct == sorted(pct, reverse=True)
    assert all(not (r["game"] or "").lower().endswith(" trophies") for r in out["games"])


def test_never_played_not_played():
    never = call("list_games", {"filter": "never_played", "limit": 10_000})
    assert never["count"] == len(never["games"])
    keys = [_name_key(g["game"]) for g in never["games"]]
    assert len(keys) == len(set(keys))  # one entry per game, even if owned on PS4 and PS5
    played = call("list_games", {"sort_by": "playtime", "limit": 10_000})["games"]
    played_keys = {_name_key(r["game"]) for r in played if r["hours_played"] is not None}
    assert not set(keys) & played_keys
    stat_ids = {s.title_id for s in psn_client.get_title_stats() if s.title_id}
    for item in psn_client.get_library():
        found = re.search(r"[A-Z]{4}\d{5}_00", item.get("productId") or "")
        name = (item.get("gameMeta") or {}).get("name") or (item.get("titleMeta") or {}).get("name")
        if found and found.group(0) in stat_ids and name:
            assert _name_key(name) not in keys, name


def test_game_details_groups_sum_to_totals():
    game = unique_recent_game()
    out = call("get_game_details", {"game_name": game.title_name})
    assert "matches" not in out and "error" not in out, out
    assert out["game"] == display_name(game.title_name)
    groups = out["trophy_groups"]
    assert groups and groups[0]["dlc"] is False
    for k in ("platinum", "gold", "silver", "bronze"):
        assert sum(g["earned"][k] for g in groups) == out["trophies_earned"][k], k
        assert sum(g["total"][k] for g in groups) == out["trophies_total"][k], k
    assert (len(groups) > 1) == bool(game.has_trophy_groups)


def test_game_details_unknown_game():
    out = call("get_game_details", {"game_name": "Zzqx Definitely Not A Game 9000"})
    assert "error" in out and "web search" in out["error"]


# ---------- steps 3 and 4: friends and recap (about 10-25 more requests) ----------

def no_secrets(out):
    text = json.dumps(out)
    assert "deviceId" not in text and "personalDetail" not in text


@pytest.fixture(scope="module")
def friends_out():
    return call("list_friends")


def test_list_friends_matches_friend_ids(friends_out):
    out = friends_out
    no_secrets(out)
    assert out["count"] == len(psn_client.get_friend_ids()) == len(out["friends"])
    assert all(f["online_id"] for f in out["friends"])
    online = [f["status"] == "online" for f in out["friends"]]
    assert online == sorted(online, reverse=True)
    assert out["online_count"] == sum(online)
    assert isinstance(out["friend_requests_received"], list)


def test_get_player_first_friend(friends_out):
    if not friends_out["friends"]:
        pytest.skip("no friends")
    name = friends_out["friends"][0]["online_id"]
    out = call("get_player", {"name": name})
    no_secrets(out)
    assert out.get("is_friend") is True, "first friend not resolved as a friend"
    assert out["online_id"] == name
    if out["trophies"] != "private":
        t = out["trophies"]
        assert t["total"] == sum(t[k] for k in ("platinum", "gold", "silver", "bronze"))
        assert len(out["recent_games"]) <= min(5, out["games_on_trophy_list"])


def test_compare_overall_first_friend(friends_out):
    if not friends_out["friends"]:
        pytest.skip("no friends")
    name = friends_out["friends"][0]["online_id"]
    out = call("compare_with_player", {"name": name})
    no_secrets(out)
    if out.get("their_trophies") == "private":
        pytest.skip("friend's trophies are private")
    user = psn_client.get_psnawp().user(online_id=name)  # one request; titles below come from the cache
    theirs = len(psn_client.get_player_trophy_titles(user))
    assert out["shared_games_count"] <= min(len(psn_client.get_trophy_titles()), theirs)
    assert len(out["shared_games"]) == min(out["shared_games_count"], 10)


def test_recap_week_totals():
    out = call("get_recap", {"period": "week"})
    no_secrets(out)
    t = out["trophies_earned"]
    assert t["total"] == sum(g["trophies"] for g in out["trophies_by_game"])
    assert t["total"] == sum(t[k] for k in ("platinum", "gold", "silver", "bronze"))
    counts = [g["trophies"] for g in out["trophies_by_game"]]
    assert counts == sorted(counts, reverse=True)
    assert len(out["platinums_earned"]) == t["platinum"]
    assert set(out["games_started"]) <= set(out["games_played"])
