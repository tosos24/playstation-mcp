"""Smoke tests against the real PSN account. Run once with RUN_LIVE=1 (about 15-25 PSN requests).

They assert invariants only, never values that change as the owner plays.
"""

import json
import re

import pytest

import psn_client
from tests.fakes import call, call_list
from tools.common import display_name, normalize

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
