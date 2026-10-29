"""list_games, get_game_details and the build_games() join (trophy list + play time + library)."""

import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from psnawp_api.core.psnawp_exceptions import PSNAWPTooManyRequestsError

import server
from tests.fakes import ago, call, call_raw, group, stat, trophy
from tools import games as games_mod
from tools.games import build_games


def lg(**args):
    return call("list_games", args)


def names(out):
    return [g["game"] for g in out["games"]]


def row(out, name):
    hits = [g for g in out["games"] if g["game"] == name]
    assert len(hits) == 1, (name, names(out))
    return hits[0]


def done(n, *, at=None):
    """n earned bronze trophies."""
    return [trophy(f"T{i}", earned=True, earned_at=at or ago(days=5)) for i in range(n)]


def todo(n):
    return [trophy(f"U{i}") for i in range(n)]


# ---------- schema ----------

def tool_schema(name):
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}[name].input_schema


def test_list_games_schema():
    props = tool_schema("list_games")["properties"]
    assert props["sort_by"]["enum"] == ["trophy_progress", "playtime", "recently_played", "last_trophy", "play_count"]
    assert props["sort_by"]["default"] == "trophy_progress"
    enums = [a for a in props["filter"]["anyOf"] if "enum" in a]
    assert enums[0]["enum"] == ["unfinished", "all", "completed", "platinum_earned", "abandoned", "never_played"]
    assert props["filter"]["default"] is None
    assert props["limit"]["default"] == 20


def test_get_game_details_schema():
    schema = tool_schema("get_game_details")
    assert schema["required"] == ["game_name"]
    assert schema["properties"]["game_name"]["type"] == "string"


# ---------- the real-data quirks, in one library ----------

@pytest.fixture
def real(psn):
    """A library shaped like the owner's real one."""
    psn.ea = psn.add("EA SPORTS FC™ 26 Trophies", done(1) + todo(3), platforms=("PS5",))
    psn.alan = psn.add("Alan Wake II", done(2) + todo(2), platforms=("PS5",))
    psn.alan_stat = psn.play("Alan Wake 2", 30, title_id="PPSA02571_00")
    psn.title_map["PPSA02571_00"] = psn.alan.np_communication_id
    psn.play("EA SPORTS FC™ 26", 100)
    psn.glitch = psn.add("Glitchy Game", done(1) + todo(1), platforms=("PS4",))
    psn.play("Glitchy Game", 463_000, platform="PS4", first=ago(days=400))
    psn.both = psn.add("Cross Game", done(1) + todo(1), platforms=("PS4", "PS5"))
    psn.play("Cross Game", 10, platform="PS4", last=ago(days=50), first=ago(days=300))
    psn.play("Cross Game", 5, platform="PS5", last=ago(days=2), first=ago(days=100))
    psn.play("Netflix", 200, platform=None, title_id="CUSA00127_00")
    psn.play("Fortnite", 40, platform="PS4", first=ago(days=900), last=ago(days=300))
    psn.play("Fortnite", 60, platform="PS5", first=ago(days=600), last=ago(days=10))
    psn.ps3 = psn.add("Old PS3 Game", done(4), platforms=("PS3",), progress=100)
    miles = psn.add("Marvel's Spider-Man: Miles Morales", done(1) + todo(1))
    psn.play("Miles Morales", 8, title_id="PPSA00777_00")
    psn.title_map["PPSA00777_00"] = miles.np_communication_id
    return psn


def test_alan_wake_2_joins_by_title_id(real):
    out = lg(sort_by="playtime")
    alan = row(out, "Alan Wake II")
    assert alan["hours_played"] == 30.0
    assert "Alan Wake 2" not in names(out)


def test_only_unmatched_stats_are_looked_up(real):
    build_games()
    looked_up = {tid for batch in real.mapped for tid in batch}
    # "Alan Wake 2" already matches "Alan Wake II" by name (Roman numerals), so it isn't looked up.
    by_name = {s.title_id for s in real.stats if s.name in ("EA SPORTS FC™ 26", "Glitchy Game", "Cross Game",
                                                            "Alan Wake 2")}
    assert looked_up.isdisjoint(by_name)
    assert looked_up == {s.title_id for s in real.stats if s.name in ("Netflix", "Fortnite", "Miles Morales")}


def test_unmatched_name_joins_by_title_id(real):
    g = row(lg(sort_by="playtime"), "Marvel's Spider-Man: Miles Morales")
    assert g["hours_played"] == 8.0
    assert "Miles Morales" not in names(lg(sort_by="playtime"))


def test_trophies_suffix_dropped_and_play_time_joined(real):
    out = lg(sort_by="playtime")
    ea = row(out, "EA SPORTS FC™ 26")
    assert ea["hours_played"] == 100.0
    assert ea["trophy_progress_percent"] == 25
    for g in lg(sort_by="trophy_progress", filter="all", limit=100)["games"]:
        assert not g["game"].lower().endswith(" trophies")


def test_impossible_play_time_dropped_and_flagged(real):
    out = lg(sort_by="playtime", limit=100)
    g = row(out, "Glitchy Game")
    assert g["hours_played"] == 0.0
    assert "play_time_warning" in g
    assert g["play_count"] == 1
    assert names(out)[0] != "Glitchy Game"
    for other in out["games"]:
        if other["game"] != "Glitchy Game":
            assert "play_time_warning" not in other



def test_impossible_play_time_without_first_played_dropped_and_flagged(psn):
    psn.add("No Start Date", done(1) + todo(1))
    psn.stats.append(dataclasses.replace(stat("No Start Date", 463_000), first_played_date_time=None))
    g = row(lg(sort_by="playtime"), "No Start Date")
    assert g["hours_played"] == 0.0 and "play_time_warning" in g
    assert g["first_played"] is None


def test_play_time_longer_than_since_ps4_launch_dropped_even_with_old_first_played(psn):
    psn.add("Too Long", done(1) + todo(1))
    since_launch = datetime.now(timezone.utc) - games_mod.PS4_LAUNCH
    # first_played wrongly far in the past, so only the PS4-launch limit catches it.
    psn.play("Too Long", duration=since_launch + timedelta(days=1), first=datetime(1990, 1, 1, tzinfo=timezone.utc))
    g = row(lg(sort_by="playtime"), "Too Long")
    assert g["hours_played"] == 0.0 and "play_time_warning" in g


def test_play_time_without_first_played_under_limit_kept(psn):
    psn.add("Plausible", done(1) + todo(1))
    psn.stats.append(dataclasses.replace(stat("Plausible", 5_000), first_played_date_time=None))
    g = row(lg(sort_by="playtime"), "Plausible")
    assert g["hours_played"] == 5000.0 and "play_time_warning" not in g

def test_ps4_and_ps5_entries_summed(real):
    g = row(lg(sort_by="playtime", limit=100), "Cross Game")
    assert g["hours_played"] == 15.0
    assert g["play_count"] == 2
    assert g["first_played"] == ago(days=300).date().isoformat()
    assert g["last_played"] == ago(days=2).date().isoformat()
    assert g["platforms"] == ["PS4", "PS5"]


def test_apps_left_out_and_reported(real):
    out = lg(sort_by="playtime", limit=100)
    assert "Netflix" not in names(out)
    assert out["left_out"] == ["Netflix"]
    assert "note" in out


def test_played_game_without_trophy_list(real):
    out = lg(sort_by="playtime", limit=100)
    f = row(out, "Fortnite")  # PS4 + PS5 entries merged into one record
    assert f["hours_played"] == 100.0
    assert f["play_count"] == 2
    assert f["platforms"] == ["PS4", "PS5"]
    for k in ("trophy_progress_percent", "has_platinum", "platinum_earned", "last_trophy"):
        assert f[k] is None, k
    assert "trophy list" in f["note"]
    assert f["first_played"] == ago(days=900).date().isoformat()
    assert f["last_played"] == ago(days=10).date().isoformat()


def test_game_without_play_data(real):
    g = row(lg(sort_by="trophy_progress", filter="all", limit=100), "Old PS3 Game")
    assert g["hours_played"] is None and g["play_count"] is None
    assert g["first_played"] is None and g["last_played"] is None
    assert g["trophy_progress_percent"] == 100
    assert g["last_trophy"] is not None
    assert "note" not in g


def test_build_games_skipped_and_records(real):
    records, skipped = build_games()
    assert skipped == ["Netflix"]
    assert sum(1 for r in records if r["name"] == "Fortnite") == 1
    assert len(records) == len(real.titles) + 1


# ---------- matching details ----------

def test_name_match_prefers_same_console(psn):
    ps4 = psn.add("Twin Game", done(1) + todo(3), platforms=("PS4",))
    ps5 = psn.add("Twin Game", done(3) + todo(1), platforms=("PS5",))
    psn.play("Twin Game", 7, platform="PS5")
    psn.play("Twin Game", 3, platform="PS4")
    records, _ = build_games()
    by_id = {r["title"].np_communication_id: r for r in records if r["title"]}
    assert by_id[ps4.np_communication_id]["play"] == timedelta(hours=3)
    assert by_id[ps5.np_communication_id]["play"] == timedelta(hours=7)


def test_name_match_any_console_if_none_same(psn):
    psn.add("Only PS4 List", done(1) + todo(1), platforms=("PS4",))
    psn.play("Only PS4 List", 4, platform="PS5")
    g = row(lg(sort_by="playtime"), "Only PS4 List")
    assert g["hours_played"] == 4.0
    assert psn.mapped in ([], [[]])


def test_app_matching_trophy_list_by_name_is_kept(psn):
    psn.add("Weird App Game", done(1) + todo(1))
    psn.play("Weird App Game", 2, platform=None)
    out = lg(sort_by="playtime")
    assert row(out, "Weird App Game")["hours_played"] == 2.0
    assert "left_out" not in out


def test_unmatched_stat_mapped_to_unknown_trophy_list_becomes_no_list_record(psn):
    psn.add("Something", done(1))
    psn.play("Mystery", 3, platform="PS5", title_id="PPSA00001_00")
    psn.title_map["PPSA00001_00"] = "NPWR99999_00"  # not on my list
    g = row(lg(sort_by="playtime"), "Mystery")
    assert g["trophy_progress_percent"] is None


def test_apps_deduped_in_left_out(psn):
    psn.play("Netflix", 1, platform=None)
    psn.play("Netflix", 2, platform=None)
    psn.play("YouTube", 2, platform=None)
    out = lg(sort_by="recently_played")
    assert out["left_out"] == ["Netflix", "YouTube"]
    assert out["count"] == 0 and out["games"] == []


def test_roman_numeral_name_match_without_lookup(psn):
    psn.add("Final Fantasy VII Remake", done(1) + todo(1))
    psn.play("FINAL FANTASY 7 REMAKE", 20)
    g = row(lg(sort_by="playtime"), "Final Fantasy VII Remake")
    assert g["hours_played"] == 20.0


def test_play_time_limit_boundary_kept(psn):
    psn.add("Marathon", done(1) + todo(1))
    psn.play("Marathon", 24 * 9, first=ago(days=10))
    g = row(lg(sort_by="playtime"), "Marathon")
    assert g["hours_played"] == 216.0 and "play_time_warning" not in g


def test_hours_rounded_to_one_decimal(psn):
    psn.add("Short", done(1) + todo(1))
    psn.play("Short", duration=timedelta(hours=1, minutes=51, seconds=21))
    assert row(lg(sort_by="playtime"), "Short")["hours_played"] == 1.9


def test_zero_and_missing_durations(psn):
    psn.add("Zero", done(1) + todo(1))
    psn.play("Zero", duration=None, play_count=None)
    g = row(lg(sort_by="playtime"), "Zero")
    assert g["hours_played"] == 0.0 and g["play_count"] == 0


def test_nameless_stat_does_not_crash(psn):
    psn.add("Real", done(1) + todo(1))
    psn.play(None, 2, platform="PS5")
    psn.play(None, 2, platform=None)
    out = lg(sort_by="playtime", filter="all")
    assert out["count"] == 2
    assert "left_out" not in out
    lg(filter="never_played")
    lg(filter="abandoned")


def test_empty_everything(psn):
    out = lg()
    assert out == {"sort_by": "trophy_progress", "filter": "unfinished", "count": 0, "games": []}
    assert lg(filter="never_played") == {"filter": "never_played", "count": 0, "games": []}


# ---------- sorting ----------

@pytest.fixture
def ranked(psn):
    psn.a = psn.add("A Game", done(9) + todo(1), last_updated=ago(days=30))   # 90%
    psn.b = psn.add("B Game", done(5) + todo(5), last_updated=ago(days=1))    # 50%
    psn.c = psn.add("C Game", done(10), last_updated=ago(days=200))           # 100%
    psn.d = psn.add("D Game", done(1) + todo(9), last_updated=None)           # 10%
    psn.play("A Game", 5, play_count=50, last=ago(days=20))
    psn.play("B Game", 50, play_count=3, last=ago(days=3))
    psn.play("C Game", 20, play_count=10, last=ago(days=150))
    psn.play("No List", 1, play_count=100, last=ago(days=0, hours=1))
    return psn


def test_sort_trophy_progress_default_unfinished(ranked):
    out = lg()
    assert out["sort_by"] == "trophy_progress" and out["filter"] == "unfinished"
    assert names(out) == ["A Game", "B Game", "D Game"]
    assert "note" not in out and "left_out" not in out


def test_explicit_none_filter_same_as_default(ranked):
    assert lg(filter=None) == lg()


def test_sort_trophy_progress_all_puts_no_list_last(ranked):
    out = lg(filter="all")
    assert names(out) == ["C Game", "A Game", "B Game", "D Game", "No List"]


def test_sort_playtime_defaults_to_all(ranked):
    out = lg(sort_by="playtime")
    assert out["filter"] == "all"
    assert names(out) == ["B Game", "C Game", "A Game", "No List", "D Game"]
    assert "note" in out
    assert "left_out" not in out  # nothing was skipped


def test_sort_recently_played(ranked):
    out = lg(sort_by="recently_played")
    assert names(out)[:4] == ["No List", "B Game", "A Game", "C Game"]
    assert names(out)[-1] == "D Game"
    assert "note" in out


def test_sort_play_count(ranked):
    out = lg(sort_by="play_count")
    assert names(out) == ["No List", "A Game", "C Game", "B Game", "D Game"]
    assert "note" in out


def test_sort_last_trophy(ranked):
    out = lg(sort_by="last_trophy")
    assert out["filter"] == "all"
    assert names(out)[:3] == ["B Game", "A Game", "C Game"]
    assert set(names(out)[3:]) == {"D Game", "No List"}
    assert "note" not in out


@pytest.mark.parametrize("sort_by", ["trophy_progress", "last_trophy"])
def test_no_play_note_for_trophy_sorts(ranked, sort_by):
    ranked.play("Netflix", 1, platform=None)
    out = lg(sort_by=sort_by)
    assert "note" not in out and "left_out" not in out


@pytest.mark.parametrize("sort_by", ["playtime", "recently_played", "play_count"])
def test_left_out_for_play_sorts(ranked, sort_by):
    ranked.play("Netflix", 1, platform=None)
    out = lg(sort_by=sort_by)
    assert out["left_out"] == ["Netflix"]
    assert "Netflix" in out["note"] or "apps" in out["note"]


# ---------- filters ----------

def test_filter_completed(ranked):
    out = lg(filter="completed", sort_by="playtime")
    assert names(out) == ["C Game"]


def test_filter_unfinished_excludes_no_trophy_list(ranked):
    out = lg(filter="unfinished", sort_by="playtime")
    assert names(out) == ["B Game", "A Game", "D Game"]


def test_filter_all_count(ranked):
    out = lg(filter="all", limit=2)
    assert out["count"] == 5 and len(out["games"]) == 2


def test_filter_platinum_earned(psn):
    plat = [trophy("P", kind="platinum", earned=True, earned_at=ago(days=3))]
    psn.add("Plat Done", plat + done(2))
    psn.add("Plat Missing", [trophy("P", kind="platinum")] + done(2))
    psn.add("No Plat", done(2))
    psn.play("Lonely", 3)
    out = lg(filter="platinum_earned")
    assert names(out) == ["Plat Done"]
    assert out["games"][0]["has_platinum"] is True and out["games"][0]["platinum_earned"] is True


def test_filter_abandoned(psn):
    psn.add("Old Unfinished", done(1) + todo(1))
    psn.play("Old Unfinished", 5, last=ago(days=91))
    psn.add("Recent Unfinished", done(1) + todo(1))
    psn.play("Recent Unfinished", 5, last=ago(days=10))
    psn.add("Old Finished", done(2), last_updated=ago(days=400))
    psn.play("Old Finished", 5, last=ago(days=300))
    # No play data: falls back to the last trophy date.
    psn.add("Old PS3", done(1) + todo(1), platforms=("PS3",), last_updated=ago(days=500))
    psn.add("New PS3", done(1) + todo(1), platforms=("PS3",), last_updated=ago(days=5))
    psn.add("Never Trophied", todo(2), platforms=("PS3",), last_updated=None)
    # Recent play wins over an old trophy.
    psn.add("Played Lately", done(1) + todo(1), last_updated=ago(days=300))
    psn.play("Played Lately", 1, last=ago(days=1))
    # Played without a trophy list, long ago.
    psn.play("Old No List", 2, last=ago(days=200))
    out = lg(filter="abandoned", limit=100)
    assert set(names(out)) == {"Old Unfinished", "Old PS3", "Old No List"}


def test_abandoned_boundary(psn):
    psn.add("Edge", done(1) + todo(1))
    psn.play("Edge", 1, last=ago(days=89))
    assert lg(filter="abandoned")["count"] == 0


def test_filter_abandoned_with_sort_trophy_progress(psn):
    psn.add("Old", done(1) + todo(3))
    psn.play("Old", 5, last=ago(days=100))
    out = lg(filter="abandoned", sort_by="trophy_progress")
    assert out["filter"] == "abandoned" and names(out) == ["Old"]


# ---------- never played ----------

@pytest.fixture
def owned(psn):
    psn.add("Played Game", done(1) + todo(1))
    psn.play("Played Game", 3, title_id="PPSA11111_00")
    psn.own("Played Game", title_id="PPSA11111_00")
    psn.own("Played Elsewhere Name", title_id="PPSA11111_00")   # same title ID, other name -> played
    psn.play("Renamed In Stats", 1, title_id="PPSA22222_00")
    psn.own("Renamed In Stats™", title_id="PPSA99999_00")       # different ID, same loose name -> played
    psn.own("Unplayed Twin", platform="PS4")
    psn.own("Unplayed Twin", platform="PS5")                     # PS4 + PS5 -> one entry
    psn.own("zebra Unplayed", platform="PS5")
    psn.own("Apple Unplayed", platform="PS4")
    psn.own("Mystery Package", platform="PSXX")                  # unknown package type
    psn.library.append({"productId": "UP0006-PPSA55555_00-X", "gameMeta": {}, "titleMeta": {"name": "Title Meta Only"}})
    psn.library.append({"productId": "UP0006-PPSA66666_00-X", "gameMeta": None, "titleMeta": None})
    psn.library.append({"productId": None, "gameMeta": {"name": "No Product", "packageType": "PSGD"}})
    return psn


def test_never_played(owned):
    out = lg(filter="never_played", limit=100)
    assert set(out) == {"filter", "count", "games"}
    assert out["filter"] == "never_played"
    by_name = {g["game"]: g for g in out["games"]}
    assert set(by_name) == {"Unplayed Twin", "zebra Unplayed", "Apple Unplayed", "Mystery Package",
                            "Title Meta Only", "No Product"}
    assert by_name["Unplayed Twin"]["platforms"] == ["PS4", "PS5"]
    assert by_name["Mystery Package"]["platforms"] == []
    assert names(out) == sorted(names(out), key=str.lower)
    assert out["count"] == 6


@pytest.mark.parametrize("sort_by", ["trophy_progress", "playtime", "recently_played", "last_trophy", "play_count"])
def test_never_played_ignores_sort(owned, sort_by):
    assert lg(filter="never_played", sort_by=sort_by, limit=100) == lg(filter="never_played", limit=100)


def test_never_played_excludes_no_trophy_list_games(psn):
    psn.play("Fortnite", 10, platform="PS5", title_id="PPSA01234_00")
    psn.own("Fortnite", platform="PS5", title_id="PPSA07777_00")
    psn.own("FORTNITE", platform="PS4")
    assert lg(filter="never_played")["count"] == 0


def test_never_played_excludes_title_id_match_even_for_apps(psn):
    psn.play("Some App Name", 1, platform=None, title_id="CUSA00127_00")
    psn.own("Different Store Name", platform="PS4", title_id="CUSA00127_00")
    assert lg(filter="never_played")["count"] == 0


def test_never_played_matches_joined_trophy_name(psn):
    alan = psn.add("Alan Wake II", done(1) + todo(1))
    psn.play("Alan Wake 2", 30, title_id="PPSA02571_00")
    psn.title_map["PPSA02571_00"] = alan.np_communication_id
    psn.own("Alan Wake II", title_id="PPSA09999_00")  # store name like the trophy list, other title ID
    assert lg(filter="never_played")["count"] == 0


@pytest.mark.parametrize("limit,expected", [(0, 1), (-5, 1), (1, 1), (2, 2), (1000, 6)])
def test_never_played_limit(owned, limit, expected):
    out = lg(filter="never_played", limit=limit)
    assert len(out["games"]) == expected
    assert out["count"] == 6


# ---------- limits ----------

@pytest.mark.parametrize("limit,expected", [(0, 1), (-1, 1), (1, 1), (3, 3), (10_000, 5)])
def test_limit(ranked, limit, expected):
    out = lg(filter="all", limit=limit)
    assert len(out["games"]) == expected
    assert out["count"] == 5


def test_default_limit_20(psn):
    for i in range(30):
        psn.add(f"Game {i:02d}", done(1) + todo(1))
    out = lg()
    assert out["count"] == 30 and len(out["games"]) == 20


# ---------- row shape ----------

def test_row_shape(ranked):
    g = row(lg(sort_by="playtime"), "A Game")
    assert set(g) == {"game", "platforms", "trophy_progress_percent", "has_platinum", "platinum_earned",
                      "hours_played", "play_count", "first_played", "last_played", "last_trophy"}
    assert g["platforms"] == ["PS5"]
    assert g["trophy_progress_percent"] == 90
    assert g["hours_played"] == 5.0 and g["play_count"] == 50
    assert g["last_played"] == ago(days=20).date().isoformat()
    assert g["last_trophy"] == ago(days=30).date().isoformat()


def test_none_progress_is_zero(psn):
    psn.add("Nothing Yet", todo(2))
    psn.titles[-1] = dataclasses.replace(psn.titles[-1], progress=None)
    assert row(lg(), "Nothing Yet")["trophy_progress_percent"] == 0


def test_psn_error_in_stats_maps_to_tool_error(psn):
    psn.add("X", done(1) + todo(1))
    psn.raise_on["stats"] = PSNAWPTooManyRequestsError("slow down")
    with pytest.raises(ToolError) as info:
        call_raw("list_games", {})
    assert not isinstance(info.value, UnexpectedToolError)
    assert "rate limit" in str(info.value)


def test_psn_error_in_library_maps_to_tool_error(psn):
    psn.raise_on["library"] = PSNAWPTooManyRequestsError("slow down")
    with pytest.raises(ToolError) as info:
        call_raw("list_games", {"filter": "never_played"})
    assert "rate limit" in str(info.value)


# ---------- get_game_details ----------

@pytest.fixture
def detailed(psn):
    t = psn.add("Marvel's Spider-Man 2", done(3) + todo(2), platforms=("PS5",))
    psn.set_groups(
        t,
        group("default", "Marvel's Spider-Man 2", earned={"bronze": 3}, defined={"bronze": 4, "platinum": 1},
              progress=60),
        group("001", "The DLC", earned={}, defined={"gold": 1}, progress=None),
    )
    psn.play("Marvel's Spider-Man 2", 42.25, play_count=12, first=ago(days=200), last=ago(days=4))
    psn.spidey = t
    return psn


def details(name):
    return call("get_game_details", {"game_name": name})


def test_details_shape(detailed):
    out = details("spiderman 2")
    assert out["game"] == "Marvel's Spider-Man 2"
    assert out["hours_played"] == 42.2 or out["hours_played"] == 42.3
    assert out["play_count"] == 12
    assert out["first_played"] == ago(days=200).date().isoformat()
    assert out["last_played"] == ago(days=4).date().isoformat()
    assert out["trophies_earned"] == {"platinum": 0, "gold": 0, "silver": 0, "bronze": 3}
    assert out["trophies_total"] == {"platinum": 0, "gold": 0, "silver": 0, "bronze": 5}
    assert "note" not in out


def test_details_groups(detailed):
    groups = details("Marvel's Spider-Man 2")["trophy_groups"]
    assert [g["name"] for g in groups] == ["Marvel's Spider-Man 2", "The DLC"]
    assert [g["dlc"] for g in groups] == [False, True]
    assert groups[0]["progress_percent"] == 60
    assert groups[1]["progress_percent"] == 0  # None -> 0
    assert groups[0]["earned"] == {"platinum": 0, "gold": 0, "silver": 0, "bronze": 3}
    assert groups[0]["total"] == {"platinum": 1, "gold": 0, "silver": 0, "bronze": 4}
    assert groups[1]["total"]["gold"] == 1


def test_details_group_id_none_is_base_game(psn):
    t = psn.add("Groupless", done(1))
    psn.set_groups(t, group(None, "Groupless"))
    assert details("Groupless")["trophy_groups"][0]["dlc"] is False


def test_details_trophies_suffix_and_no_play_data(psn):
    t = psn.add("EA SPORTS FC™ 26 Trophies", done(1) + todo(1), platforms=("PS4",))
    psn.set_groups(t, group())
    out = details("ea sports fc 26")
    assert out["game"] == "EA SPORTS FC™ 26"
    assert out["hours_played"] is None and out["play_count"] is None
    assert "play time" in out["note"].lower()


def test_details_alan_wake_2_finds_alan_wake_ii(psn):
    t = psn.add("Alan Wake II", done(1) + todo(1))
    psn.set_groups(t, group())
    psn.play("Alan Wake 2", 30, title_id="PPSA02571_00")
    psn.title_map["PPSA02571_00"] = t.np_communication_id
    out = details("Alan Wake 2")
    assert out["game"] == "Alan Wake II"
    assert out["hours_played"] == 30.0


def test_details_play_time_warning(psn):
    t = psn.add("Glitchy", done(1) + todo(1), platforms=("PS4",))
    psn.set_groups(t, group())
    psn.play("Glitchy", 463_000, platform="PS4", first=ago(days=30))
    psn.play("Glitchy", 12, platform="PS5", first=ago(days=30))
    out = details("Glitchy")
    assert out["hours_played"] == 12.0
    assert "play_time_warning" in out


def test_details_matches(psn):
    psn.add("Ratchet & Clank", done(1))
    psn.add("Ratchet & Clank: Rift Apart", done(1))
    out = details("ratchet")
    assert set(out) == {"matches", "note"}
    assert {m["game"] for m in out["matches"]} == {"Ratchet & Clank", "Ratchet & Clank: Rift Apart"}
    assert psn.mapped == []  # no play-time work for an ambiguous name


def test_details_matches_strip_trophies_suffix(psn):
    psn.add("Hogwarts Legacy Trophies", done(1))
    psn.add("Hogwarts Legacy Deluxe", done(1))
    out = details("hogwarts")
    assert all(not m["game"].endswith("Trophies") for m in out["matches"])


def test_details_no_match_mentions_web_search(psn):
    psn.add("Astro Bot", done(1))
    out = details("Zzzzqqq Unknown")
    assert set(out) == {"error"}
    assert "Zzzzqqq Unknown" in out["error"]
    assert "web search" in out["error"]


def test_details_game_without_trophy_list_is_not_found(psn):
    psn.add("Astro Bot", done(1))
    psn.play("Fortnite", 100)
    out = details("Fortnite")
    assert "error" in out


def test_details_psn_error(detailed):
    detailed.raise_on["groups"] = PSNAWPTooManyRequestsError("slow")
    with pytest.raises(ToolError) as info:
        call_raw("get_game_details", {"game_name": "Marvel's Spider-Man 2"})
    assert "rate limit" in str(info.value)


def test_details_and_list_agree(detailed):
    d = details("Marvel's Spider-Man 2")
    g = row(lg(sort_by="playtime"), "Marvel's Spider-Man 2")
    for k, v in g.items():
        assert d[k] == v, k


def test_build_games_uses_module_level_names():
    # The conftest psn fixture patches these names in tools.games; make sure they're really used from there.
    for name in ("get_library", "get_title_stats", "get_trophy_groups", "get_trophy_titles", "map_title_ids"):
        assert hasattr(games_mod, name), name
