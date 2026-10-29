import pytest
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
from tests.fakes import call, call_raw, raw_profile, trophy
from tools.common import find_games, normalize


# ---------- normalize / find_games ----------

def test_normalize():
    assert normalize("Marvel's Spider-Man 2™") == "marvel s spider man 2"
    assert normalize(None) == ""
    assert normalize("  GOD of WAR® Ragnarök ") == "god of war ragnar k"


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


def test_exact_name_from_matches_resolves(library):
    out = call("get_trophies", {"game_name": "Marvel's Spider-Man"})
    assert out["game"] == "Marvel's Spider-Man"


@pytest.mark.xfail(strict=True, reason="BUG: two games with the same name (e.g. PS4 + PS5 lists) can never "
                                      "be picked: the exact name always returns 'matches' again")
def test_same_name_on_two_platforms_is_reachable(psn):
    psn.add("Ghost Game", [trophy()], platforms=("PS4",))
    psn.add("Ghost Game", [trophy()], platforms=("PS5",))
    out = call("get_trophies", {"game_name": "Ghost Game"})
    assert "matches" not in out


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
