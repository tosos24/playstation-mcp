import pytest
import requests
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from psnawp_api.core.psnawp_exceptions import (
    PSNAWPAuthenticationError,
    PSNAWPBadRequestError,
    PSNAWPForbiddenError,
    PSNAWPInvalidTokenError,
    PSNAWPNotFoundError,
    PSNAWPServerError,
    PSNAWPTooManyRequestsError,
    PSNAWPUnauthorizedError,
)

import psn_client
from tests.fakes import at, call, call_raw, raw_profile, trophy
from tools.common import display_name, find_games, normalize


# ---------- normalize / find_games ----------

def test_normalize():
    assert normalize("Marvel's Spider-Man 2™") == "marvel s spider man 2"
    assert normalize(None) == ""
    assert normalize("  GOD of WAR® Ragnarök ") == "god of war ragnar k"


@pytest.mark.parametrize("roman,digit", [("II", "2"), ("iii", "3"), ("IV", "4"), ("V", "5"), ("VI", "6"),
                                         ("VII", "7"), ("VIII", "8"), ("IX", "9"), ("X", "10")])
def test_normalize_roman_numerals(roman, digit):
    assert normalize(f"Game {roman}") == f"game {digit}"
    assert normalize(f"Game {roman}: Subtitle") == f"game {digit} subtitle"


def test_normalize_roman_only_standalone_words():
    assert normalize("I Am Bread") == "i am bread"
    assert normalize("Vivid Xtreme Mix") == "vivid xtreme mix"
    assert normalize("Final Fantasy VII Remake") == "final fantasy 7 remake"
    assert normalize("Alan Wake II") == normalize("Alan Wake 2")


def test_display_name():
    assert display_name("EA SPORTS FC™ 26 Trophies") == "EA SPORTS FC™ 26"
    assert display_name("Some Game trophies") == "Some Game"
    assert display_name("Some Game TROPHIES") == "Some Game"
    assert display_name("Trophies Galore") == "Trophies Galore"
    assert display_name("Trophies") == "Trophies"
    assert display_name("Plain Game") == "Plain Game"
    assert display_name(None) == ""


def test_alan_wake_2_finds_alan_wake_ii(psn):
    psn.add("Alan Wake II", [trophy()])
    psn.add("Alan Wake Remastered", [trophy()])
    assert names(find_games("Alan Wake 2")) == ["Alan Wake II"]
    assert names(find_games("alan wake ii")) == ["Alan Wake II"]


def test_game_fields_drop_trophies_suffix(psn):
    psn.add("EA SPORTS FC™ 26 Trophies", [trophy(earned=True, earned_at=at(1))])
    out = call("get_trophies", {"game_name": "EA Sports FC 26", "status": "all"})
    assert out["game"] == "EA SPORTS FC™ 26"
    out = call("get_trophies", {"status": "earned", "sort_by": "recent"})
    assert all(t["game"] == "EA SPORTS FC™ 26" for t in out["trophies"])


def names(games):
    return sorted(g.title_name for g in games)


@pytest.fixture
def library(psn):
    for n in ["Marvel's Spider-Man 2", "Marvel's Spider-Man", "Ratchet & Clank: Rift Apart",
              "Ratchet & Clank", "God of War Ragnarök", "ASTRO BOT"]:
        psn.add(n, [trophy()])
    return psn


def test_exact_normalized_wins_over_partial(library):
    # "marvel's spider-man" is also a substring of "...Spider-Man 2", but the exact match wins.
    assert names(find_games("MARVEL'S SPIDER-MAN")) == ["Marvel's Spider-Man"]


def test_exact_ignores_symbols_and_case(library):
    assert names(find_games("astro bot")) == ["ASTRO BOT"]
    assert names(find_games("ratchet & clank rift apart")) == ["Ratchet & Clank: Rift Apart"]


def test_partial_ignores_spaces_and_symbols(library):
    assert names(find_games("spiderman 2")) == ["Marvel's Spider-Man 2"]
    assert names(find_games("rift apart")) == ["Ratchet & Clank: Rift Apart"]
    assert names(find_games("astrobot")) == ["ASTRO BOT"]


def test_partial_can_be_ambiguous(library):
    assert names(find_games("spider")) == ["Marvel's Spider-Man", "Marvel's Spider-Man 2"]
    assert names(find_games("ratchet")) == ["Ratchet & Clank", "Ratchet & Clank: Rift Apart"]


def test_close_spelling(library):
    assert names(find_games("god of war ragnarok")) == ["God of War Ragnarök"]
    assert names(find_games("astor bot")) == ["ASTRO BOT"]


def test_no_match(library):
    assert find_games("zzzz qqqq") == []


# ---------- resolve via get_trophies ----------

def test_ambiguous_returns_matches(library):
    out = call("get_trophies", {"game_name": "spider"})
    assert set(out) == {"matches", "note"}
    assert sorted(m["game"] for m in out["matches"]) == ["Marvel's Spider-Man", "Marvel's Spider-Man 2"]
    assert set(out["matches"][0]) == {"game", "platforms", "progress_percent", "has_platinum",
                                      "platinum_earned", "has_dlc_trophies"}
    assert library.fetched == []  # no trophy lists fetched for an ambiguous name


def test_unknown_game_returns_error(library):
    out = call("get_trophies", {"game_name": "zzzz qqqq"})
    assert set(out) == {"error"}
    assert "zzzz qqqq" in out["error"]


@pytest.mark.parametrize("name", ["™", " ®: ", "!!!"])
def test_symbols_only_game_name_asks_for_a_name(library, name):
    out = call("get_trophies", {"game_name": name})
    assert set(out) == {"error"}
    assert "game name" in out["error"]
    assert library.fetched == []


def test_exact_name_from_matches_resolves(library):
    out = call("get_trophies", {"game_name": "Marvel's Spider-Man"})
    assert out["game"] == "Marvel's Spider-Man"


def test_same_name_on_two_platforms_is_reachable(psn):
    psn.add("Ghost Game", [trophy("old")], platforms=("PS4",))
    psn.add("Ghost Game", [trophy("new")], platforms=("PS5",))
    out = call("get_trophies", {"game_name": "Ghost Game"})
    assert len(out["matches"]) == 2
    assert "console" in out["note"]
    for hint, plat, name in [("Ghost Game PS5", "PS5", "new"), ("ghost game (PS4)", "PS4", "old")]:
        out = call("get_trophies", {"game_name": hint})
        assert "matches" not in out
        assert out["platforms"] == [plat]
        assert out["trophies"][0]["name"] == name


def test_console_hint_ignored_when_it_matches_nothing(psn):
    psn.add("Ghost Game", [trophy()], platforms=("PS4",))
    out = call("get_trophies", {"game_name": "Ghost Game PS5"})
    assert out["game"] == "Ghost Game"


# ---------- error mapping ----------

AUTH = [PSNAWPAuthenticationError, PSNAWPInvalidTokenError, PSNAWPUnauthorizedError]


def raise_in(psn, where, exc):
    psn.raise_on[where] = exc


@pytest.mark.parametrize("cls", AUTH)
def test_auth_errors_reset_login(psn, monkeypatch, cls):
    monkeypatch.setattr(psn_client, "_me", object())
    raise_in(psn, "titles", cls('{"error": {"code": 2, "message": "Invalid token"}}'))
    with pytest.raises(ToolError) as info:
        call_raw("get_trophies", {"game_name": "x"})
    assert not isinstance(info.value, UnexpectedToolError)
    assert "NPSSO" in str(info.value)
    assert psn_client._me is None


@pytest.mark.parametrize("cls,text", [
    (PSNAWPForbiddenError, "private"),
    (PSNAWPNotFoundError, "no data"),
    (PSNAWPTooManyRequestsError, "rate limit"),
])
def test_specific_errors(psn, monkeypatch, cls, text):
    sentinel = object()
    monkeypatch.setattr(psn_client, "_me", sentinel)
    raise_in(psn, "titles", cls('{"error": {"code": 1, "message": "Forbidden"}}'))
    with pytest.raises(ToolError) as info:
        call_raw("get_trophies", {"game_name": "x"})
    assert not isinstance(info.value, UnexpectedToolError)
    assert text in str(info.value)
    assert psn_client._me is sentinel  # only auth errors drop the login


@pytest.mark.parametrize("cls", [PSNAWPServerError, PSNAWPBadRequestError])
def test_other_psn_errors_include_message(psn, cls):
    raise_in(psn, "titles", cls('{"error": {"code": 500, "message": "Upstream broke"}}'))
    with pytest.raises(ToolError) as info:
        call_raw("get_trophies", {"game_name": "x"})
    assert not isinstance(info.value, UnexpectedToolError)
    assert "PSN request failed: Upstream broke" in str(info.value)


def test_plain_text_psn_error(psn):
    raise_in(psn, "titles", PSNAWPServerError("Bad gateway"))
    with pytest.raises(ToolError) as info:
        call_raw("get_trophies", {"game_name": "x"})
    assert "PSN request failed: Bad gateway" in str(info.value)


def test_missing_npsso_message_reaches_claude(monkeypatch):
    # No fake PSN: the real login runs and finds no NPSSO.
    monkeypatch.delenv("NPSSO", raising=False)
    monkeypatch.setattr(psn_client, "_me", None)
    with pytest.raises(ToolError) as info:
        call_raw("get_trophies", {"game_name": "x"})
    assert not isinstance(info.value, UnexpectedToolError)
    assert "NPSSO is missing" in str(info.value)


@pytest.mark.parametrize("exc", [requests.exceptions.ConnectionError("boom"), requests.exceptions.Timeout()])
def test_network_error_message(psn, exc):
    raise_in(psn, "titles", exc)
    with pytest.raises(ToolError) as info:
        call_raw("get_trophies", {"game_name": "x"})
    assert not isinstance(info.value, UnexpectedToolError)
    assert "Couldn't reach PSN" in str(info.value)
    assert "Try again" in str(info.value)


def test_error_while_fetching_trophies(psn):
    psn.add("Solo", [trophy()])
    raise_in(psn, "trophies", PSNAWPNotFoundError("{}"))
    with pytest.raises(ToolError) as info:
        call_raw("get_trophies", {"game_name": "Solo"})
    assert not isinstance(info.value, UnexpectedToolError)
    assert "no data" in str(info.value)


def test_error_in_profile_tool(psn):
    psn.profile = raw_profile()
    raise_in(psn, "profile", PSNAWPTooManyRequestsError("{}"))
    with pytest.raises(ToolError) as info:
        call_raw("get_my_profile")
    assert "rate limit" in str(info.value)
