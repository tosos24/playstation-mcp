"""Step 4: get_recap (week / month / year)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import server
from tests.fakes import ago, call, trophy
from tools import recap as recap_mod


def recap(period=None):
    return call("get_recap", {} if period is None else {"period": period})


def won(kind="bronze", days=1.0, rate="50.0", name=None):
    return trophy(name or f"{kind}-{days}", kind=kind, earned=True, earned_at=ago(days=days), earn_rate=rate)


# ---------- schema ----------

def test_schema():
    t = {t.name: t for t in asyncio.run(server.mcp.list_tools())}["get_recap"]
    props = t.input_schema["properties"]
    assert props["period"]["enum"] == ["week", "month", "year"]
    assert props["period"]["default"] == "month"
    assert t.annotations.read_only_hint is True


def test_bad_period_rejected(psn):
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError):
        call("get_recap", {"period": "decade"})


# ---------- period starts ----------

class Frozen(datetime):
    NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.NOW


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(recap_mod, "datetime", Frozen)
    return Frozen.NOW


@pytest.mark.parametrize("period,start", [
    ("week", "2026-03-08"), ("month", "2026-02-13"), ("year", "2026-01-01")])
def test_period_dates(psn, frozen, period, start):
    out = recap(period)
    assert out["period"] == period
    assert out["from"] == start and out["to"] == "2026-03-15"


def test_period_start_function():
    now = datetime(2026, 10, 29, 8, 30, tzinfo=timezone.utc)
    assert recap_mod._period_start("week", now) == now - timedelta(days=7)
    assert recap_mod._period_start("month", now) == now - timedelta(days=30)
    assert recap_mod._period_start("year", now) == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_default_is_month(psn):
    assert recap()["period"] == "month"


@pytest.mark.parametrize("period,inside,outside", [
    ("week", timedelta(days=7) - timedelta(minutes=1), timedelta(days=7) + timedelta(minutes=1)),
    ("month", timedelta(days=30) - timedelta(minutes=1), timedelta(days=30) + timedelta(minutes=1)),
])
def test_boundaries_rolling(psn, frozen, period, inside, outside):
    psn.add("Game", [trophy("In", earned=True, earned_at=frozen - inside),
                     trophy("Out", earned=True, earned_at=frozen - outside)])
    out = recap(period)
    assert out["trophies_earned"]["total"] == 1


def test_boundary_year(psn, frozen):
    jan1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    psn.add("Game", [trophy("In", earned=True, earned_at=jan1),
                     trophy("Out", earned=True, earned_at=jan1 - timedelta(seconds=1)),
                     trophy("Also in", earned=True, earned_at=jan1 + timedelta(days=60))])
    assert recap("year")["trophies_earned"]["total"] == 2


# ---------- trophies ----------

def test_counts_by_type_and_by_game(psn):
    psn.add("Big Game Trophies", [won("bronze"), won("bronze"), won("silver", 2), won("platinum", 3),
                                  won("bronze", days=40), trophy("todo")], progress=77)
    psn.add("Small Game", [won("gold", 5)], progress=12)
    psn.add("Old Game", [won("gold", 100)])
    out = recap("month")
    assert out["trophies_earned"] == {"platinum": 1, "gold": 1, "silver": 1, "bronze": 2, "total": 5}
    assert out["trophies_by_game"] == [
        {"game": "Big Game", "trophies": 4, "progress_percent_now": 77},
        {"game": "Small Game", "trophies": 1, "progress_percent_now": 12},
    ]
    assert out["platinums_earned"] == ["Big Game"]
    assert sum(g["trophies"] for g in out["trophies_by_game"]) == out["trophies_earned"]["total"]


def test_by_game_sorted_desc(psn):
    psn.add("One", [won(days=1)])
    psn.add("Three", [won(days=3), won(days=3), won(days=3)])
    psn.add("Two", [won(days=2), won(days=2)])
    assert [g["game"] for g in recap("week")["trophies_by_game"]] == ["Three", "Two", "One"]


def test_only_games_active_in_period_are_fetched(psn):
    psn.add("Recent", [won(days=2)])
    psn.add("Stale", [won(days=20)])
    psn.add("Never", [trophy()])
    recap("week")
    assert psn.fetched == ["Recent"]


def test_active_game_with_only_old_trophies_is_left_out(psn):
    # last_updated is in the week (e.g. progress on a trophy), but every earned trophy is older
    psn.add("Game", [won(days=20)], last_updated=ago(days=1))
    out = recap("week")
    assert out["trophies_by_game"] == [] and out["trophies_earned"]["total"] == 0


def test_earned_without_date_and_unearned_are_skipped(psn):
    psn.add("Game", [trophy("No date", earned=True, earned_at=None),
                     trophy("Not earned", earned=False),
                     won(days=1)], last_updated=ago(days=1))
    assert recap("week")["trophies_earned"]["total"] == 1


def test_rarest_trophy(psn):
    psn.add("A", [won(rate="12.5", name="Common-ish"), won(rate=None, name="No rate"),
                  won(rate="0.4", days=40, name="Rare but old")])
    psn.add("B Trophies", [won("gold", rate="3.1", name="Rarest"), won(rate="9.0")])
    rarest = recap("month")["rarest_trophy"]
    assert rarest == {"name": "Rarest", "game": "B", "type": "gold", "earn_rate": 3.1}
    assert isinstance(rarest["earn_rate"], float)


def test_rarest_string_compared_as_number(psn):
    # "10.0" < "9.0" as strings; as numbers 9.0 is rarer
    psn.add("A", [won(rate="10.0", name="Ten"), won(rate="9.0", name="Nine")])
    assert recap("week")["rarest_trophy"]["name"] == "Nine"


def test_rarest_none_when_no_rates(psn):
    psn.add("A", [won(rate=None)])
    out = recap("week")
    assert out["rarest_trophy"] is None and out["trophies_earned"]["total"] == 1


def test_several_platinums(psn):
    psn.add("P1", [won("platinum", 1)])
    psn.add("P2", [won("platinum", 2)])
    assert sorted(recap("week")["platinums_earned"]) == ["P1", "P2"]


def test_empty_library(psn):
    out = recap("year")
    assert out["trophies_earned"] == {"platinum": 0, "gold": 0, "silver": 0, "bronze": 0, "total": 0}
    assert out["trophies_by_game"] == [] and out["platinums_earned"] == []
    assert out["rarest_trophy"] is None
    assert out["games_played"] == [] and out["games_started"] == []
    assert "PS4/PS5" in out["note"]


def test_capped_at_12_games(psn):
    n = recap_mod.MAX_RECAP_GAMES + 2
    assert recap_mod.MAX_RECAP_GAMES == 12
    for i in range(n):
        psn.add(f"G{i:02d}", [won(days=1 + i * 0.1)])
    out = recap("week")
    assert len(psn.fetched) == 12
    assert psn.fetched == [f"G{i:02d}" for i in range(12)]  # newest first
    assert out["trophies_earned"]["total"] == 12
    assert "12 most recently active" in out["note"] and "2 more" in out["note"]


def test_no_cap_note_at_exactly_12(psn):
    for i in range(12):
        psn.add(f"G{i:02d}", [won(days=1)])
    assert "more games" not in recap("week")["note"]


# ---------- games played / started ----------

def test_games_played_and_started(psn):
    alan = psn.add("Alan Wake II", [won(days=100)])
    psn.play("Alan Wake 2", 30, title_id="PPSA02571_00", first=ago(days=3), last=ago(days=1))
    psn.title_map["PPSA02571_00"] = alan.np_communication_id
    psn.add("EA SPORTS FC™ 26 Trophies", [])
    psn.play("EA SPORTS FC™ 26", 100, first=ago(days=200), last=ago(days=2))
    psn.play("Netflix", 50, platform=None, title_id="CUSA00127_00", first=ago(days=1), last=ago(days=1))
    psn.play("no trophies yet", 2, platform="PS4", first=ago(days=4), last=ago(days=4))
    psn.play("Old Thing", 5, first=ago(days=400), last=ago(days=60))
    out = recap("week")
    assert out["games_played"] == ["Alan Wake II", "EA SPORTS FC™ 26", "no trophies yet"]
    assert out["games_started"] == ["Alan Wake II", "no trophies yet"]
    assert "Netflix" not in out["games_played"]
    assert "Old Thing" not in recap("month")["games_played"]
    if ago(days=60).year == datetime.now(timezone.utc).year:
        assert "Old Thing" in recap("year")["games_played"]


def test_games_played_ps4_and_ps5_entries_once(psn):
    psn.add("Cross Game", [], platforms=("PS4", "PS5"))
    psn.play("Cross Game", 5, platform="PS4", last=ago(days=2), first=ago(days=300))
    psn.play("Cross Game", 5, platform="PS5", last=ago(days=1), first=ago(days=3))
    out = recap("week")
    assert out["games_played"] == ["Cross Game"]
    assert out["games_started"] == []  # first played (either console) was 300 days ago


def test_recap_note_and_no_hours(psn):
    psn.add("G", [won(days=1)])
    psn.play("G", 10, last=ago(days=1))
    out = recap("week")
    assert "hours" not in str(out["games_played"])
    assert set(out) >= {"period", "from", "to", "trophies_earned", "trophies_by_game", "platinums_earned",
                        "rarest_trophy", "games_played", "games_started", "note"}
