"""get_trophies: one game, and scans across games (other than earned + recent, see test_recent.py)."""

import pytest

from tests.fakes import at, call, trophy
from tools.trophies import MAX_SCAN_GAMES


def game_trophies():
    return [
        trophy("Plat", "Get all trophies", "platinum", "2.5", earned=False),
        trophy("Easy Start", "Finish the tutorial", "bronze", "90.1", earned=True, earned_at=at(1)),
        trophy("Gold Rush", "Collect 1000 GOLD coins", "gold", "6.6", earned=False),
        trophy("Secret!", "Find the hidden room", "silver", "12.0", earned=True, earned_at=at(3), hidden=True),
        trophy("DLC Boss", "Beat the DLC boss", "gold", "1.2", earned=False, group="001"),
        trophy("DLC Walk", "Enter the DLC area", "bronze", "40.0", earned=True, earned_at=at(2), group="001"),
        trophy("No Rate", "Rate unknown", "bronze", None, earned=False),
    ]


@pytest.fixture
def one(psn):
    psn.add("Test Game", game_trophies(), platforms=("PS5",))
    return psn


def get(**args):
    return call("get_trophies", {"game_name": "Test Game", **args})


def tnames(out):
    return [t["name"] for t in out["trophies"]]


# ---------- shape ----------

def test_one_game_shape(one):
    out = get(status="all")
    assert out["game"] == "Test Game"
    for k in ("platforms", "progress_percent", "has_platinum", "platinum_earned", "has_dlc_trophies"):
        assert k in out
    assert out["count"] == 7
    assert "games_searched" not in out and "note" not in out
    for t in out["trophies"]:
        assert set(t) == {"name", "description", "type", "earn_rate", "earned", "earned_at", "hidden", "dlc"}
    assert one.fetched == ["Test Game"]


def test_trophy_fields(one):
    by = {t["name"]: t for t in get(status="all")["trophies"]}
    s = by["Secret!"]
    assert s["earn_rate"] == 12.0 and isinstance(s["earn_rate"], float)
    assert by["Gold Rush"]["earn_rate"] == 6.6
    assert s["type"] == "silver"
    assert s["earned"] is True and s["hidden"] is True and s["dlc"] is False
    assert s["earned_at"] == at(3).isoformat()
    assert s["earned_at"].endswith("+00:00")
    assert by["Gold Rush"]["earned_at"] is None and by["Gold Rush"]["earned"] is False
    assert by["DLC Boss"]["dlc"] is True
    assert by["No Rate"]["earn_rate"] is None


def test_none_fields_do_not_crash(psn):
    psn.add("Odd", [trophy(name=None, detail=None, kind=None, earn_rate=None, earned=None, group=None, hidden=None)])
    out = call("get_trophies", {"game_name": "Odd", "status": "all"})
    t = out["trophies"][0]
    assert t == {"name": None, "description": None, "type": None, "earn_rate": None, "earned": False,
                 "earned_at": None, "hidden": False, "dlc": False}


# ---------- status ----------

def test_default_is_unearned_easiest(one):
    out = get()
    assert tnames(out) == ["Gold Rush", "Plat", "DLC Boss", "No Rate"]
    assert out["count"] == 4


def test_status_earned_default_recent(one):
    out = get(status="earned")
    assert tnames(out) == ["Secret!", "DLC Walk", "Easy Start"]


def test_status_all(one):
    out = get(status="all")
    assert out["count"] == 7
    # easiest is the default for "all"
    assert tnames(out)[0] == "Easy Start"


# ---------- sort ----------

def test_sort_easiest_desc_none_last(one):
    rates = [t["earn_rate"] for t in get(status="all", sort_by="easiest")["trophies"]]
    assert rates == [90.1, 40.0, 12.0, 6.6, 2.5, 1.2, None]


def test_sort_rarest_asc_none_last(one):
    rates = [t["earn_rate"] for t in get(status="all", sort_by="rarest")["trophies"]]
    assert rates == [1.2, 2.5, 6.6, 12.0, 40.0, 90.1, None]


def test_sort_recent_earned_first_newest_first(one):
    out = get(status="all", sort_by="recent")
    assert tnames(out)[:3] == ["Secret!", "DLC Walk", "Easy Start"]
    assert all(t["earned_at"] is None for t in out["trophies"][3:])


def test_unearned_recent_acts_as_easiest(one):
    assert tnames(get(sort_by="recent")) == tnames(get(sort_by="easiest")) == ["Gold Rush", "Plat", "DLC Boss",
                                                                              "No Rate"]


def test_cross_game_unearned_recent_acts_as_easiest(psn):
    many(psn)
    rates = [t["earn_rate"] for t in call("get_trophies", {"sort_by": "recent"})["trophies"]]
    assert rates == sorted(rates, reverse=True)


def test_sort_easiest_on_earned(one):
    assert tnames(get(status="earned", sort_by="easiest")) == ["Easy Start", "DLC Walk", "Secret!"]


def test_rarest_with_zero_rate(psn):
    psn.add("Zero", [trophy("A", earn_rate="0.0"), trophy("B", earn_rate="5.0"), trophy("C", earn_rate=None)])
    out = call("get_trophies", {"game_name": "Zero", "sort_by": "rarest"})
    assert tnames(out) == ["A", "B", "C"]
    out = call("get_trophies", {"game_name": "Zero", "sort_by": "easiest"})
    assert tnames(out)[0] == "B"
    assert tnames(out)[-1] == "C"  # unknown rate goes last, after a real 0.0


def test_easiest_puts_unknown_rate_after_zero(psn):
    # Unknown rate listed first, so a stable sort can't hide a tie with 0.0.
    psn.add("Zero", [trophy("C", earn_rate=None), trophy("A", earn_rate="0.0"), trophy("B", earn_rate="5.0")])
    out = call("get_trophies", {"game_name": "Zero", "sort_by": "easiest"})
    assert tnames(out) == ["B", "A", "C"]


# ---------- filters ----------

def test_exclude_dlc(one):
    out = get(status="all", include_dlc=False)
    assert out["count"] == 5
    assert not any(t["dlc"] for t in out["trophies"])


@pytest.mark.parametrize("search,expected", [
    ("gold", {"Gold Rush", "DLC Boss"}),          # type "gold" + "GOLD coins" in description
    ("GOLD", {"Gold Rush", "DLC Boss"}),
    ("secret", {"Secret!"}),
    ("SECRET!!", {"Secret!"}),                    # symbols ignored
    ("hidden-room", {"Secret!"}),                 # description, symbol-insensitive
    ("platinum", {"Plat"}),                       # by type
    ("dlc boss", {"DLC Boss"}),
    ("nothing like this", set()),
])
def test_search(one, search, expected):
    out = get(status="all", search=search)
    assert set(tnames(out)) == expected
    assert out["count"] == len(expected)


def test_search_and_status_combine(one):
    out = get(status="earned", search="dlc")
    assert tnames(out) == ["DLC Walk"]


def test_when_did_i_get_platinum(psn):
    psn.add("Platted", [trophy("The End", "All done", "platinum", "3.0", earned=True, earned_at=at(5)),
                        trophy("Other", "x", "bronze", "50", earned=True, earned_at=at(4))])
    out = call("get_trophies", {"game_name": "platted", "status": "earned", "search": "platinum"})
    assert out["count"] == 1
    assert out["trophies"][0]["earned_at"] == at(5).isoformat()


# ---------- limits ----------

@pytest.mark.parametrize("limit,n", [(0, 1), (-5, 1), (1, 1), (2, 2), (500, 7)])
def test_limit(one, limit, n):
    out = get(status="all", limit=limit)
    assert len(out["trophies"]) == n
    assert out["count"] == 7  # count is before the limit


def test_game_with_no_trophies(psn):
    psn.add("Empty", [])
    out = call("get_trophies", {"game_name": "Empty", "status": "all"})
    assert out["count"] == 0 and out["trophies"] == []


def test_empty_library_with_game_name(psn):
    assert "error" in call("get_trophies", {"game_name": "anything"})


# ---------- several games (not earned+recent) ----------

def many(psn, n=8, completed=()):
    for i in range(n):
        done = i in completed
        psn.add(f"Game {i}",
                [trophy(f"G{i} earned", "d", "bronze", "50.0", earned=True, earned_at=at(-i)),
                 trophy(f"G{i} todo", "d", "gold", str(10.0 + i), earned=done,
                        earned_at=at(-i, -1) if done else None)],
                progress=100 if done else 50)
    psn.titles.reverse()  # list order should not matter; the tool sorts by last trophy


def test_cross_game_unearned_scans_recent_games_and_notes(psn):
    many(psn)
    out = call("get_trophies", {})
    assert out["games_searched"] == [f"Game {i}" for i in range(MAX_SCAN_GAMES)]
    assert "note" in out and str(MAX_SCAN_GAMES) in out["note"]
    assert len(psn.fetched) == MAX_SCAN_GAMES
    assert out["count"] == MAX_SCAN_GAMES
    # easiest first
    assert [t["earn_rate"] for t in out["trophies"]] == sorted((t["earn_rate"] for t in out["trophies"]),
                                                               reverse=True)
    assert all("game" in t for t in out["trophies"])


def test_cross_game_unearned_skips_completed(psn):
    many(psn, completed={0, 2})
    out = call("get_trophies", {})
    assert out["games_searched"] == ["Game 1", "Game 3", "Game 4", "Game 5", "Game 6"]
    assert "Game 0" not in psn.fetched and "Game 2" not in psn.fetched


def test_cross_game_all_includes_completed(psn):
    many(psn, completed={0})
    out = call("get_trophies", {"status": "all"})
    assert out["games_searched"][0] == "Game 0"
    assert out["count"] == 2 * MAX_SCAN_GAMES


def test_cross_game_earned_rarest(psn):
    many(psn, completed={0, 1})
    out = call("get_trophies", {"status": "earned", "sort_by": "rarest"})
    assert "note" in out
    assert [t["earn_rate"] for t in out["trophies"]][:2] == [10.0, 11.0]


def test_cross_game_fewer_games_than_cap(psn):
    many(psn, n=2)
    out = call("get_trophies", {"status": "all"})
    assert len(out["games_searched"]) == 2
    assert "2 most recently" in out["note"]


def test_cross_game_empty_library(psn):
    out = call("get_trophies", {})
    assert out["count"] == 0 and out["trophies"] == [] and out["games_searched"] == []


def test_games_without_last_updated_go_last(psn):
    psn.add("Never", [trophy("n")], last_updated=None)
    for i in range(MAX_SCAN_GAMES):
        psn.add(f"G{i}", [trophy(f"t{i}", earned=True, earned_at=at(i)), trophy(f"u{i}")])
    out = call("get_trophies", {"status": "all"})
    assert "Never" not in out["games_searched"]


# ---------- search="platinum" without a game ----------

@pytest.fixture
def plat_lib(psn):
    # Newest first: NoPlat, PlatDone, PlatTodo, ... ; NoPlat has a trophy described as "platinum".
    psn.add("NoPlat", [trophy("Shiny", "Find the platinum sword", "gold", "5", earned=True, earned_at=at(10))],
            progress=100)
    psn.add("PlatDone", [trophy("PD Plat", "all", "platinum", "4", earned=True, earned_at=at(9))], progress=100)
    psn.add("PlatTodo", [trophy("PT Plat", "all", "platinum", "8", earned=False),
                         trophy("PT b", "b", "bronze", "50", earned=True, earned_at=at(8))], progress=40)
    for i in range(6):
        psn.add(f"Filler {i}", [trophy(f"F{i}", "f", "bronze", "50", earned=True, earned_at=at(7 - i)),
                                trophy(f"F{i}u", "f", "bronze", "50")], progress=50)
    psn.add("OldPlat", [trophy("OP Plat", "all", "platinum", "1", earned=True, earned_at=at(-100))], progress=100)
    return psn


def test_platinum_search_earned_jumps_to_platted_games(plat_lib):
    out = call("get_trophies", {"status": "earned", "search": "platinum"})
    assert out["games_searched"] == ["PlatDone", "OldPlat"]
    assert [t["name"] for t in out["trophies"]] == ["PD Plat", "OP Plat"]
    assert not any("Filler" in g for g in plat_lib.fetched)


def test_platinum_search_unearned(plat_lib):
    out = call("get_trophies", {"status": "unearned", "search": "platinum"})
    assert out["games_searched"] == ["PlatTodo"]
    assert [t["name"] for t in out["trophies"]] == ["PT Plat"]


def test_platinum_search_all(plat_lib):
    out = call("get_trophies", {"status": "all", "search": "Platinum"})
    assert out["games_searched"] == ["PlatDone", "PlatTodo", "OldPlat"]
    assert {t["name"] for t in out["trophies"]} == {"PD Plat", "PT Plat", "OP Plat"}


def test_platinum_search_symbols_normalized(plat_lib):
    out = call("get_trophies", {"status": "earned", "search": " PLATINUM! "})
    assert {t["name"] for t in out["trophies"]} == {"PD Plat", "OP Plat"}


def test_non_platinum_search_scans_recent(plat_lib):
    out = call("get_trophies", {"status": "all", "search": "sword"})
    assert out["games_searched"][0] == "NoPlat"
    assert [t["name"] for t in out["trophies"]] == ["Shiny"]
