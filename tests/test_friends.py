"""Step 3: list_friends, get_player, compare_with_player (fake friends, fake players)."""

import asyncio
import dataclasses
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from psnawp_api.core.psnawp_exceptions import (
    PSNAWPForbiddenError,
    PSNAWPTooManyRequestsError,
)

import server
from tests.fakes import ago, call, call_raw, title, trophy
from tools import friends as friends_mod


def lf(**args):
    return call("list_friends", args)


def gp(name):
    return call("get_player", {"name": name})


def cmp(name, game_name=None):
    args = {"name": name}
    if game_name is not None:
        args["game_name"] = game_name
    return call("compare_with_player", args)


def tool(name):
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}[name]


# ---------- schema ----------

def test_list_friends_schema():
    props = tool("list_friends").input_schema["properties"]
    assert props["online_only"]["type"] == "boolean"
    assert props["online_only"]["default"] is False


def test_get_player_schema():
    schema = tool("get_player").input_schema
    assert schema["required"] == ["name"]


def test_compare_schema():
    schema = tool("compare_with_player").input_schema
    assert schema["required"] == ["name"]
    assert schema["properties"]["game_name"]["default"] is None


def test_friend_tools_read_only():
    for name in ("list_friends", "get_player", "compare_with_player", "get_recap"):
        ann = tool(name).annotations
        assert ann.read_only_hint is True and ann.destructive_hint in (None, False)


# ---------- list_friends ----------

@pytest.fixture
def crew(psn):
    psn.a = psn.add_friend("Alpha_1", online=False, last_online=ago(days=3))
    psn.b = psn.add_friend("Bravo-2", online=True, last_online=ago(hours=1), playing="Astro Bot", plus=True)
    psn.c = psn.add_friend("Charlie3", online=False, last_online=ago(hours=5), platform="PS4")
    psn.d = psn.add_friend("Delta", presence=False)  # hides online status
    psn.e = psn.add_friend("Echo", online=True, last_online=ago(days=1), platform="PS4")
    return psn


def test_list_friends_order_and_fields(crew):
    out = lf()
    assert out["count"] == 5
    assert out["online_count"] == 2
    names = [f["online_id"] for f in out["friends"]]
    # online first (newest last_online first), then offline by last_online, hidden last
    assert names == ["Bravo-2", "Echo", "Charlie3", "Alpha_1", "Delta"]
    bravo = out["friends"][0]
    assert bravo == {"online_id": "Bravo-2", "status": "online", "platform": "PS5", "playing": "Astro Bot",
                     "last_online": bravo["last_online"], "ps_plus": True}
    assert bravo["last_online"].endswith("Z")
    assert out["friends"][2]["platform"] == "PS4" and out["friends"][2]["playing"] is None
    assert out["friends"][2]["ps_plus"] is False
    assert out["friend_requests_received"] == []


def test_list_friends_hidden_presence(crew):
    delta = [f for f in lf()["friends"] if f["online_id"] == "Delta"][0]
    assert delta == {"online_id": "Delta", "status": "hidden", "platform": None, "playing": None,
                     "last_online": None, "ps_plus": False}


def test_list_friends_online_only(crew):
    out = lf(online_only=True)
    assert [f["online_id"] for f in out["friends"]] == ["Bravo-2", "Echo"]
    assert out["count"] == 5 and out["online_count"] == 2


def test_list_friends_online_only_none_online(psn):
    psn.add_friend("Solo", online=False, last_online=ago(days=1))
    out = lf(online_only=True)
    assert out["friends"] == [] and out["count"] == 1 and out["online_count"] == 0


def test_list_friends_empty(psn):
    assert lf() == {"count": 0, "online_count": 0, "friends": [], "friend_requests_received": []}


def test_list_friends_requests_names(crew):
    r1 = crew.add_player("Stranger_X")
    r2 = crew.add_player("Another_Y")
    crew.request_ids = [r1, r2]
    assert lf()["friend_requests_received"] == ["Stranger_X", "Another_Y"]


def test_list_friends_no_private_fields(crew):
    text = json.dumps(lf())
    assert "SECRET" not in text and "personalDetail" not in text and "accountId" not in text
    assert crew.a not in text  # account IDs aren't shown


def test_list_friends_presence_without_platform_info(psn):
    aid = psn.add_friend("Odd", presence=False)
    psn.presences[aid] = {"accountId": aid, "availability": "unavailable", "lastAvailableDate": "2026-01-02T00:00:00.000Z"}
    f = lf()["friends"][0]
    assert f["status"] == "unknown" and f["last_online"] == "2026-01-02T00:00:00.000Z"


def test_list_friends_last_online_falls_back_to_last_available(psn):
    aid = psn.add_friend("Fallback", online=False, last_online=ago(days=2))
    del psn.presences[aid]["primaryPlatformInfo"]["lastOnlineDate"]
    assert lf()["friends"][0]["last_online"] == psn.presences[aid]["lastAvailableDate"]


def test_list_friends_psn_error(psn, monkeypatch):
    def boom():
        raise PSNAWPTooManyRequestsError("slow")
    monkeypatch.setattr(friends_mod, "get_friend_ids", boom)
    with pytest.raises(ToolError, match="rate limit"):
        call_raw("list_friends")


# ---------- _resolve_player via get_player ----------

@pytest.mark.parametrize("query", ["bravo-2", "BRAVO 2", "bravo2", "  Bravo_2 ", "brav", "o-2"])
def test_friend_loose_name(crew, query):
    out = gp(query)
    assert out["online_id"] == "Bravo-2" and out["is_friend"] is True
    assert crew.player_lookups == []  # found among friends, no exact-name lookup


def test_friend_exact_beats_partial(psn):
    psn.add_friend("Sam")
    psn.add_friend("Samurai")
    assert gp("sam")["online_id"] == "Sam"


def test_several_friends_match(psn):
    psn.add_friend("Kid_Zero")
    psn.add_friend("KidOne")
    psn.add_friend("Other")
    out = gp("kid")
    assert out["matches"] == ["KidOne", "Kid_Zero"]
    assert "note" in out


@pytest.mark.parametrize("query", ["", "   ", "™", "_-!"])
def test_empty_or_symbol_name(psn, query):
    psn.add_friend("Anyone")
    out = gp(query)
    assert "error" in out and "matches" not in out
    assert psn.player_lookups == []


def test_non_friend_exact_name(psn):
    psn.add_friend("Buddy")
    psn.add_player("Rando_77")
    out = gp("  Rando_77 ")
    assert out["online_id"] == "Rando_77" and out["is_friend"] is False
    assert psn.player_lookups == ["Rando_77"]  # stripped


def test_unknown_player_error(psn):
    psn.add_friend("Buddy")
    out = gp("Nobody_Here")
    assert "error" in out and "Nobody_Here" in out["error"]
    out = cmp("Nobody_Here")
    assert "error" in out


def test_friend_found_by_account_id(crew):
    gp("echo")
    assert crew.user_lookups == [crew.e]


# ---------- get_player fields ----------

def test_get_player_full(psn):
    titles = [title(f"Game {i}", progress=i * 10, platforms=("PS4", "PS5") if i == 6 else ("PS5",),
                    last_updated=ago(days=10 - i)) for i in range(7)]
    titles.append(title("Never Trophied", last_updated=None))
    titles.append(title("Old Trophies", last_updated=ago(days=50), progress=None))
    aid = psn.add_friend("Gamer", online=True, last_online=ago(hours=1), playing="Game 6", plus=True,
                         about="hi there")
    psn.summaries[aid] = dataclasses.replace(psn.summaries[aid], trophy_level=321)
    psn.summaries[aid] = dataclasses.replace(
        psn.summaries[aid], earned_trophies=dataclasses.replace(psn.summaries[aid].earned_trophies,
                                                                bronze=10, silver=5, gold=2, platinum=1))
    psn.player_titles[aid] = titles
    out = gp("gamer")
    assert out["online_id"] == "Gamer" and out["is_friend"] is True
    assert out["about_me"] == "hi there" and out["ps_plus"] is True
    assert out["status"] == "online" and out["playing"] == "Game 6" and out["platform"] == "PS5"
    assert out["trophy_level"] == 321
    assert out["trophies"] == {"platinum": 1, "gold": 2, "silver": 5, "bronze": 10, "total": 18}
    assert out["games_on_trophy_list"] == 9
    recent = out["recent_games"]
    assert [g["game"] for g in recent] == ["Game 6", "Game 5", "Game 4", "Game 3", "Game 2"]
    assert recent[0]["platforms"] == ["PS4", "PS5"]
    assert recent[0]["progress_percent"] == 60
    assert recent[0]["last_trophy"] == ago(days=4).date().isoformat()
    assert "SECRET" not in json.dumps(out)


def test_get_player_recent_games_trophies_suffix_and_none(psn):
    aid = psn.add_player("P2")
    psn.player_titles[aid] = [title("Foo Trophies", last_updated=None, progress=None)]
    out = gp("P2")
    assert out["recent_games"] == [{"game": "Foo", "platforms": ["PS5"], "progress_percent": 0, "last_trophy": None}]


def test_get_player_empty_about_me_is_none(psn):
    psn.add_player("Quiet", about="")
    out = gp("Quiet")
    assert out["about_me"] is None
    assert out["status"] == "hidden"  # no presence for a non-friend
    assert out["recent_games"] == [] and out["games_on_trophy_list"] == 0


@pytest.mark.parametrize("where", ["summary", "titles"])
def test_get_player_private_trophies(psn, where):
    aid = psn.add_friend("Secretive", online=True, last_online=ago(hours=2))
    psn.player_errors[(aid, where)] = PSNAWPForbiddenError("private")
    out = gp("secretive")
    assert out["trophies"] == "private"
    assert "trophy_level" not in out or where == "titles"
    assert out["online_id"] == "Secretive" and out["status"] == "online"


def test_get_player_matches_passthrough(psn):
    psn.add_friend("Twin_A")
    psn.add_friend("Twin_B")
    assert gp("twin")["matches"] == ["Twin_A", "Twin_B"]


def test_get_player_other_psn_error_is_tool_error(psn):
    aid = psn.add_friend("Busy")
    psn.player_errors[(aid, "summary")] = PSNAWPTooManyRequestsError("slow")
    with pytest.raises(ToolError, match="rate limit"):
        call_raw("get_player", {"name": "Busy"})


# ---------- compare_with_player: overall ----------

def my_profile(level=400, bronze=100, silver=20, gold=5, platinum=2):
    return {"trophySummary": {"level": level, "earnedTrophies": {"bronze": bronze, "silver": silver,
                                                                 "gold": gold, "platinum": platinum}}}


def test_compare_overall(psn):
    psn.profile = my_profile()
    mine = [psn.add(f"Shared {i}", last_updated=ago(days=30 + i), progress=50 + i) for i in range(3)]
    psn.add("Mine Only", last_updated=ago(days=1))
    aid = psn.add_friend("Rival")
    psn.summaries[aid] = dataclasses.replace(psn.summaries[aid], trophy_level=250)
    theirs = [
        dataclasses.replace(mine[0], progress=10, last_updated_datetime=ago(days=60)),
        dataclasses.replace(mine[1], progress=None, last_updated_datetime=ago(days=2)),  # they played it recently
        dataclasses.replace(mine[2], progress=100, last_updated_datetime=None),
        title("Theirs Only", last_updated=ago(days=1)),
    ]
    psn.player_titles[aid] = theirs
    out = cmp("rival")
    assert out["player"] == "Rival"
    assert out["my_level"] == 400 and out["their_level"] == 250
    assert out["my_trophies"] == {"platinum": 2, "gold": 5, "silver": 20, "bronze": 100, "total": 127}
    assert out["their_trophies"]["total"] == 0
    assert out["shared_games_count"] == 3
    # newest by either of us: Shared 1 (their 2 days), Shared 0 (my 30 days), Shared 2 (my 32 days)
    assert out["shared_games"] == [
        {"game": "Shared 1", "my_progress_percent": 51, "their_progress_percent": 0},
        {"game": "Shared 0", "my_progress_percent": 50, "their_progress_percent": 10},
        {"game": "Shared 2", "my_progress_percent": 52, "their_progress_percent": 100},
    ]
    assert "10" in out["note"]


def test_compare_overall_capped(psn):
    psn.profile = my_profile()
    aid = psn.add_friend("Many")
    games = [psn.add(f"G{i:02d}", last_updated=ago(days=i)) for i in range(friends_mod.MAX_SHARED + 5)]
    psn.player_titles[aid] = list(games)
    out = cmp("many")
    assert out["shared_games_count"] == friends_mod.MAX_SHARED + 5
    assert len(out["shared_games"]) == friends_mod.MAX_SHARED == 10
    assert [g["game"] for g in out["shared_games"]] == [f"G{i:02d}" for i in range(10)]


def test_compare_overall_matches_by_np_id_not_name(psn):
    psn.profile = my_profile()
    mine = psn.add("Same Name", np_id="NPWR11111_00")
    aid = psn.add_friend("Pal")
    psn.player_titles[aid] = [title("Same Name", np_id="NPWR22222_00"),  # other console's list
                              title("Renamed On Their Side", np_id=mine.np_communication_id)]
    out = cmp("pal")
    assert out["shared_games_count"] == 1
    assert out["shared_games"][0]["game"] == "Same Name"


def test_compare_overall_nothing_shared(psn):
    psn.profile = my_profile()
    psn.add("Mine")
    psn.add_friend("Nada")
    out = cmp("nada")
    assert out["shared_games_count"] == 0 and out["shared_games"] == []


def test_compare_overall_missing_profile_fields(psn):
    psn.profile = {}
    psn.add_friend("Pal")
    out = cmp("pal")
    assert out["my_level"] is None
    assert out["my_trophies"] == {"platinum": 0, "gold": 0, "silver": 0, "bronze": 0, "total": 0}


@pytest.mark.parametrize("where", ["summary", "titles"])
def test_compare_overall_private(psn, where):
    psn.profile = my_profile()
    aid = psn.add_friend("Hidden")
    psn.player_errors[(aid, where)] = PSNAWPForbiddenError("private")
    out = cmp("hidden")
    assert out == {"player": "Hidden", "my_level": 400,
                   "my_trophies": {"platinum": 2, "gold": 5, "silver": 20, "bronze": 100, "total": 127},
                   "their_trophies": "private"}


def test_compare_non_friend(psn):
    psn.profile = my_profile()
    psn.add_player("Pro_Gamer")
    assert cmp("Pro_Gamer")["player"] == "Pro_Gamer"


def test_compare_player_matches_and_errors(psn):
    psn.add_friend("Twin_A")
    psn.add_friend("Twin_B")
    assert cmp("twin")["matches"] == ["Twin_A", "Twin_B"]
    assert "error" in cmp("™")


# ---------- compare_with_player: one game ----------

def shared_game(psn, mine_flags, their_flags, name="Spider Game", friend="Buddy"):
    """A game I have with len(mine_flags) trophies; the friend's copy uses the same trophy IDs."""
    mine = [trophy(f"T{i:02d}", earned=e, earned_at=ago(days=1) if e else None) for i, e in enumerate(mine_flags)]
    g = psn.add(name, mine)
    aid = psn.add_friend(friend)
    psn.player_trophies[(aid, g.np_communication_id)] = [
        dataclasses.replace(t, earned=e, earned_date_time=ago(days=2) if e else None)
        for t, e in zip(mine, their_flags)
    ]
    return g, aid


def test_compare_game(psn):
    shared_game(psn, [True, True, False, False, True], [True, False, True, False, False])
    out = cmp("buddy", "spider")
    assert out == {
        "game": "Spider Game", "player": "Buddy", "total_trophies": 5,
        "i_earned": 3, "they_earned": 2, "both_earned": 1,
        "only_i_have": ["T01", "T04"], "only_they_have": ["T02"],
        "note": out["note"],
    }
    assert "15" in out["note"]


def test_compare_game_lists_capped(psn):
    n = friends_mod.MAX_LISTED + 5
    shared_game(psn, [True] * n + [False] * n, [False] * n + [True] * n)
    out = cmp("buddy", "Spider Game")
    assert out["i_earned"] == out["they_earned"] == n and out["both_earned"] == 0
    assert len(out["only_i_have"]) == len(out["only_they_have"]) == friends_mod.MAX_LISTED == 15
    assert out["only_i_have"] == [f"T{i:02d}" for i in range(15)]


def test_compare_game_private(psn):
    g, aid = shared_game(psn, [True], [True])
    psn.player_errors[(aid, "trophies")] = PSNAWPForbiddenError("private")
    assert cmp("buddy", "spider") == {"game": "Spider Game", "player": "Buddy", "their_trophies": "private"}


def test_compare_game_not_played(psn):
    g, aid = shared_game(psn, [True], [True])
    del psn.player_trophies[(aid, g.np_communication_id)]
    out = cmp("buddy", "spider")
    assert out["game"] == "Spider Game" and out["player"] == "Buddy"
    assert "hasn't played" in out["note"]
    assert "i_earned" not in out


def test_compare_game_name_errors_pass_through(psn):
    shared_game(psn, [True], [True], name="Ghost Game")
    psn.add("Ghost Game", [trophy()], platforms=("PS4",))
    out = cmp("buddy", "ghost game")
    assert "matches" in out and len(out["matches"]) == 2
    assert "error" in cmp("buddy", "Zzqx Nothing")
    assert "error" in cmp("buddy", "™")


def test_compare_game_trophies_suffix(psn):
    shared_game(psn, [True], [False], name="Big Game Trophies")
    assert cmp("buddy", "big game")["game"] == "Big Game"


def test_compare_game_player_resolved_first(psn):
    psn.add("Some Game", [trophy()])
    out = cmp("Nobody_Here", "some game")
    assert "error" in out and "Nobody_Here" in out["error"]


def test_compare_empty_game_name_means_overall(psn):
    psn.profile = my_profile()
    psn.add_friend("Pal")
    assert "shared_games" in cmp("pal", "")


# ---------- known-to-be-arguable behaviour ----------

def test_friend_name_with_roman_letter_typed_without_separator(psn):
    psn.add_friend("Dark_V")
    out = gp("darkv")
    assert out.get("online_id") == "Dark_V", out
