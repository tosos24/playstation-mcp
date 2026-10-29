import pytest

from tests.fakes import call_list, trophy


def lib(psn):
    psn.add("Alpha", progress=40)
    psn.add("Bravo", progress=100)
    psn.add("Charlie", progress=85)
    psn.add("Delta", progress=None)
    psn.add("Echo", progress=99)


def names(rows):
    return [r["game"] for r in rows]


def test_default_excludes_completed_sorted_desc(psn):
    lib(psn)
    rows = call_list("get_trophy_progress")
    assert names(rows) == ["Echo", "Charlie", "Alpha", "Delta"]
    assert [r["progress_percent"] for r in rows] == [99, 85, 40, 0]


def test_include_completed(psn):
    lib(psn)
    rows = call_list("get_trophy_progress", {"include_completed": True})
    assert names(rows) == ["Bravo", "Echo", "Charlie", "Alpha", "Delta"]


@pytest.mark.parametrize("limit,expected", [(1, 1), (2, 2), (1000, 4)])
def test_limit(psn, limit, expected):
    lib(psn)
    assert len(call_list("get_trophy_progress", {"limit": limit})) == expected


def test_limit_zero_returns_nothing(psn):
    lib(psn)
    assert call_list("get_trophy_progress", {"limit": 0}) == []


@pytest.mark.xfail(strict=True, reason="BUG: negative limit slices titles[:-1], returning all but the last game")
def test_negative_limit_does_not_return_almost_everything(psn):
    lib(psn)
    rows = call_list("get_trophy_progress", {"limit": -1})
    assert len(rows) <= 1


def test_empty_library(psn):
    assert call_list("get_trophy_progress") == []


def test_summary_shape(psn):
    psn.add("Plat Game", [trophy(kind="platinum", earned=True), trophy(group="001")],
            progress=50, platforms=("PS4", "PS5"))
    psn.add("No Plat", [trophy()], progress=10)
    rows = call_list("get_trophy_progress")
    assert rows[0] == {
        "game": "Plat Game",
        "platforms": ["PS4", "PS5"],
        "progress_percent": 50,
        "has_platinum": True,
        "platinum_earned": True,
        "has_dlc_trophies": True,
    }
    assert rows[1]["has_platinum"] is False
    assert rows[1]["platinum_earned"] is False
    assert rows[1]["has_dlc_trophies"] is False
