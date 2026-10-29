"""get_trophies with no game, status="earned", sort_by="recent": the early-stop walk over games."""

import random
from datetime import timedelta

import pytest

from tests.fakes import at, call, trophy
from tools.common import normalize
from tools.trophies import MAX_RECENT_SCAN_GAMES


def brute_force(psn, limit, search=None, include_dlc=True, games=None):
    """Every earned trophy in the given games (default: all), newest first."""
    rows = []
    for t in psn.titles if games is None else games:
        for tr in psn.trophies[t.np_communication_id]:
            if not tr.earned:
                continue
            if not include_dlc and tr.trophy_group_id not in (None, "default"):
                continue
            if search and normalize(search) not in normalize(
                    f"{tr.trophy_name} {tr.trophy_detail} {tr.trophy_type.value}"):
                continue
            rows.append((tr.earned_date_time.isoformat(), t.title_name, tr.trophy_name))
    rows.sort(reverse=True)
    return rows[:limit]


def as_rows(out):
    return [(t["earned_at"], t["game"], t["name"]) for t in out["trophies"]]


def recent(**args):
    return call("get_trophies", {"status": "earned", **args})


def random_library(psn, rng, n_games, quirk_rate=0.5):
    """Games whose trophies overlap in time; some games' last_updated is ~1 s before their newest trophy."""
    for g in range(n_games):
        start = rng.uniform(-500, 0)
        trs = []
        for k in range(rng.randint(0, 6)):
            earned = rng.random() < 0.7
            trs.append(trophy(f"g{g}t{k}", "desc", rng.choice(["bronze", "silver", "gold"]), "10",
                              earned=earned,
                              earned_at=at(start + rng.uniform(0, 50), seconds=rng.random()) if earned else None,
                              group=rng.choice(["default", "default", "001"])))
        times = [t.earned_date_time for t in trs if t.earned_date_time]
        last = max(times) if times else None
        if last and rng.random() < quirk_rate:
            last -= timedelta(seconds=1)  # the real PSN quirk
        psn.add(f"Game {g}", trs, last_updated=last)


# ---------- correctness against brute force ----------

@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("limit", [1, 3, 10])
def test_random_libraries_match_brute_force(psn, seed, limit):
    rng = random.Random(seed)
    random_library(psn, rng, rng.randint(1, MAX_RECENT_SCAN_GAMES))
    out = recent(limit=limit)
    assert as_rows(out) == brute_force(psn, limit)
    assert out["count"] >= len(out["trophies"])


@pytest.mark.parametrize("seed", range(15))
def test_random_with_filters_match_brute_force(psn, seed):
    rng = random.Random(1000 + seed)
    random_library(psn, rng, rng.randint(1, MAX_RECENT_SCAN_GAMES))
    out = recent(limit=2, search="gold", include_dlc=False)
    assert as_rows(out) == brute_force(psn, 2, search="gold", include_dlc=False)


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("limit", [1, 2, 4])
def test_clustered_quirky_libraries_match_brute_force(psn, seed, limit):
    # Every game's newest trophy lands within a few seconds of the others, and every
    # game's last_updated is 1 s early, so the slack really matters.
    rng = random.Random(5000 + seed)
    for g in range(rng.randint(2, MAX_RECENT_SCAN_GAMES)):
        times = sorted((at(0, seconds=-rng.uniform(0, 3)) for _ in range(rng.randint(1, 3))), reverse=True)
        trs = [trophy(f"g{g}t{k}", earned=True, earned_at=t) for k, t in enumerate(times)]
        psn.add(f"Game {g}", trs, last_updated=times[0] - timedelta(seconds=1))
    assert as_rows(recent(limit=limit)) == brute_force(psn, limit)


def test_quirk_last_updated_one_second_early(psn):
    # A's newest trophy is the newest overall, but A's last_updated is 1 s early,
    # so B (accurate, 0.5 s before A's trophy) sorts first.
    t = at(0)
    psn.add("A", [trophy("A new", earned=True, earned_at=t)], last_updated=t - timedelta(seconds=1))
    psn.add("B", [trophy("B new", earned=True, earned_at=t - timedelta(seconds=0.5))])
    psn.add("C", [trophy("C old", earned=True, earned_at=at(-5))])
    out = recent(limit=1)
    assert as_rows(out) == brute_force(psn, 1)
    assert out["trophies"][0]["name"] == "A new"
    assert psn.fetched == ["B", "A"]  # C is skipped
    assert out["games_searched"] == ["B", "A"]


def test_default_sort_for_earned_is_recent(psn):
    psn.add("Old", [trophy("old", earned=True, earned_at=at(-2))])
    psn.add("New", [trophy("new", earned=True, earned_at=at(0))])
    assert [t["name"] for t in call("get_trophies", {"status": "earned", "limit": 1})["trophies"]] == ["new"]


# ---------- early stop ----------

def staircase(psn, n, per_game=3):
    """Game i's trophies are all older than game i-1's; no overlap."""
    for i in range(n):
        psn.add(f"Game {i}", [trophy(f"g{i}t{k}", earned=True, earned_at=at(-i, seconds=-k)) for k in range(per_game)]
                + [trophy(f"g{i}u", earned=False)])


def test_latest_one_fetches_one_game(psn):
    staircase(psn, 8)
    out = recent(limit=1)
    assert psn.fetched == ["Game 0"]
    assert out["games_searched"] == ["Game 0"]
    assert as_rows(out) == brute_force(psn, 1)
    assert "note" not in out or isinstance(out["note"], str)


@pytest.mark.parametrize("limit,games", [(3, 1), (4, 2), (6, 2), (7, 3)])
def test_stops_once_limit_filled(psn, limit, games):
    staircase(psn, 8)
    out = recent(limit=limit)
    assert len(psn.fetched) == games
    assert as_rows(out) == brute_force(psn, limit)


def test_capped_at_max_recent_scan_games(psn):
    staircase(psn, MAX_RECENT_SCAN_GAMES + 4)
    out = recent(limit=500)
    assert len(psn.fetched) == MAX_RECENT_SCAN_GAMES
    assert out["games_searched"] == [f"Game {i}" for i in range(MAX_RECENT_SCAN_GAMES)]
    assert out["count"] == 3 * MAX_RECENT_SCAN_GAMES
    first_ten = psn.titles[:MAX_RECENT_SCAN_GAMES]
    assert as_rows(out) == brute_force(psn, 500, games=first_ten)


def test_more_than_cap_but_latest_still_exact(psn):
    staircase(psn, 25)
    assert as_rows(recent(limit=5)) == brute_force(psn, 5)
    assert len(psn.fetched) == 2


def test_games_with_no_last_updated_stop_the_walk(psn):
    psn.add("Played", [trophy("p", earned=True, earned_at=at(0))])
    psn.add("Never", [trophy("n")], last_updated=None)
    out = recent(limit=1)
    assert psn.fetched == ["Played"]
    assert out["count"] == 1


def test_game_with_earned_trophies_none_found_keeps_walking(psn):
    # Newest game has only DLC trophies; with include_dlc=False it contributes nothing.
    psn.add("DLC only", [trophy("d", earned=True, earned_at=at(0), group="001")])
    psn.add("Base", [trophy("b", earned=True, earned_at=at(-1))])
    out = recent(limit=1, include_dlc=False)
    assert [t["name"] for t in out["trophies"]] == ["b"]


def test_limit_zero_means_one(psn):
    staircase(psn, 3)
    out = recent(limit=0)
    assert len(out["trophies"]) == 1
    assert psn.fetched == ["Game 0"]


def test_empty_library(psn):
    out = recent(limit=1)
    assert out == {"games_searched": [], "count": 0, "trophies": []}


def test_nothing_earned(psn):
    psn.add("Fresh", [trophy("x")], last_updated=None)
    out = recent()
    assert out["count"] == 0 and out["trophies"] == []


def test_recent_output_shape(psn):
    staircase(psn, 2)
    out = recent(limit=2)
    t = out["trophies"][0]
    assert t["game"] == "Game 0"
    assert set(t) == {"game", "name", "description", "type", "earn_rate", "earned", "earned_at", "hidden", "dlc"}
    assert t["earned"] is True


@pytest.mark.xfail(strict=True, reason="BUG: an earned trophy with no earned_date_time crashes the early-stop "
                                      "(datetime.fromisoformat(None)) instead of being sorted last")
def test_earned_trophy_without_date_does_not_crash(psn):
    psn.add("Weird", [trophy("dateless", earned=True, earned_at=None)], last_updated=at(0))
    psn.add("Other", [trophy("o", earned=True, earned_at=at(-1))])
    out = recent(limit=1)
    assert out["count"] >= 1
