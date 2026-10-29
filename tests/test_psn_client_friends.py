"""psn_client step-3 fetchers: friends, batched profiles and presences, other players (fake PSN, no network)."""

import time
from types import SimpleNamespace

import pytest
from psnawp_api.core.psnawp_exceptions import PSNAWPNotFoundError
from psnawp_api.models.trophies import PlatformType

import psn_client
from psn_client import ACCOUNT_BATCH, HOUR, PRESENCE_SECONDS
from tests.fakes import raw_presence, title

# Captured at import, before any psn fixture patches the module.
real = {name: getattr(psn_client, name) for name in (
    "get_friend_ids", "get_friend_request_ids", "get_profiles", "get_presences", "get_player", "get_psnawp",
    "get_player_by_account_id", "get_player_summary", "get_player_trophy_titles", "get_player_title_trophies")}


class ProfileAPI:
    """Stands in for psn_client._profile_api: records (path, params) and answers like PSN."""

    def __init__(self, drop=0):
        self.calls = []
        self.drop = drop  # how many profiles to leave out of a batch answer

    def __call__(self, path, **params):
        self.calls.append((path, params))
        if path.endswith("/friends"):
            return {"friends": ["111", "222"], "nextOffset": None}
        if path.endswith("/friends/receivedRequests"):
            return {"receivedRequests": [{"accountId": "900", "requestedDate": "x"}, {"accountId": "901"}]}
        if path == "/profiles":
            ids = params["accountIds"].split(",")
            profiles = [{"onlineId": f"name-{a}"} for a in ids]
            return {"profiles": profiles[self.drop:]}
        if path.endswith("/profiles"):  # /{account_id}/profiles
            return {"onlineId": f"single-{path.split('/')[1]}"}
        raise AssertionError(path)


@pytest.fixture
def api(monkeypatch):
    fake = ProfileAPI()
    monkeypatch.setattr(psn_client, "_profile_api", fake)
    monkeypatch.setattr(psn_client, "get_me", lambda: pytest.fail("should go through _profile_api"))
    return fake


def ids(n, start=0):
    return [str(1000 + i) for i in range(start, start + n)]


# ---------- friend IDs ----------

def test_friend_ids_one_request_cached(api):
    assert real["get_friend_ids"]() == ["111", "222"]
    assert real["get_friend_ids"]() == ["111", "222"]
    assert api.calls == [("/me/friends", {"limit": 1000})]
    assert "friend_ids" in psn_client._cache


def test_friend_request_ids(api):
    assert real["get_friend_request_ids"]() == ["900", "901"]
    real["get_friend_request_ids"]()
    assert api.calls == [("/me/friends/receivedRequests", {})]


def test_friend_request_ids_none(monkeypatch):
    monkeypatch.setattr(psn_client, "_profile_api", lambda path, **p: {})
    assert real["get_friend_request_ids"]() == []


def test_profile_api_uses_session(monkeypatch):
    got = {}

    class Auth:
        def get(self, url, params):
            got.update(url=url, params=params)
            return SimpleNamespace(json=lambda: {"ok": 1})

    monkeypatch.setattr(psn_client, "get_me", lambda: SimpleNamespace(authenticator=Auth()))
    assert psn_client._profile_api("/me/friends", limit=5) == {"ok": 1}
    assert got == {"url": "https://m.np.playstation.com/api/userProfile/v1/internal/users/me/friends",
                   "params": {"limit": 5}}
    psn_client._profile_api("/x")
    assert got["params"] is None


# ---------- get_profiles ----------

def test_profiles_matched_by_order(api):
    out = real["get_profiles"](["a", "b", "c"])
    assert out == {"a": {"onlineId": "name-a"}, "b": {"onlineId": "name-b"}, "c": {"onlineId": "name-c"}}
    assert api.calls == [("/profiles", {"accountIds": "a,b,c"})]


def test_profiles_batches_of_100(api):
    wanted = ids(ACCOUNT_BATCH * 2 + 5)
    out = real["get_profiles"](wanted)
    assert list(out) == wanted
    assert [len(p["accountIds"].split(",")) for _, p in api.calls] == [100, 100, 5]
    assert ACCOUNT_BATCH == 100
    assert all(out[a] == {"onlineId": f"name-{a}"} for a in wanted)


def test_profiles_exactly_100_is_one_request(api):
    real["get_profiles"](ids(100))
    assert len(api.calls) == 1


def test_profiles_dedupes(api):
    out = real["get_profiles"](["a", "b", "a"])
    assert list(out) == ["a", "b"]
    assert api.calls == [("/profiles", {"accountIds": "a,b"})]


def test_profiles_empty_makes_no_request(api):
    assert real["get_profiles"]([]) == {}
    assert api.calls == []


def test_profiles_count_mismatch_falls_back_per_account(api):
    api.drop = 1
    out = real["get_profiles"](["a", "b", "c"])
    assert out == {"a": {"onlineId": "single-a"}, "b": {"onlineId": "single-b"}, "c": {"onlineId": "single-c"}}
    assert api.calls == [("/profiles", {"accountIds": "a,b,c"}),
                         ("/a/profiles", {}), ("/b/profiles", {}), ("/c/profiles", {})]


def test_profiles_fallback_only_for_the_bad_batch(api):
    wanted = ids(150)
    orig = api.__call__

    def flaky(path, **params):
        api.calls.append((path, params))
        if path == "/profiles" and params["accountIds"].startswith(wanted[100]):
            return {"profiles": [{"onlineId": "x"}]}  # second batch: too few
        api.calls.pop()
        return orig(path, **params)

    psn_client._profile_api = flaky  # monkeypatch fixture restores it
    out = real["get_profiles"](wanted)
    assert out[wanted[0]] == {"onlineId": f"name-{wanted[0]}"}
    assert out[wanted[120]] == {"onlineId": f"single-{wanted[120]}"}
    assert len(api.calls) == 2 + 50


def test_profiles_missing_profiles_key_falls_back(monkeypatch):
    calls = []

    def f(path, **p):
        calls.append(path)
        return {} if path == "/profiles" else {"onlineId": "solo"}
    monkeypatch.setattr(psn_client, "_profile_api", f)
    assert real["get_profiles"](["z"]) == {"z": {"onlineId": "solo"}}
    assert calls == ["/profiles", "/z/profiles"]


class OneBadAccount(ProfileAPI):
    """Batch answers come back short, and account "bad" fails on its own (deleted or invalid)."""

    def __init__(self):
        super().__init__(drop=1)

    def __call__(self, path, **params):
        if path == "/bad/profiles":
            self.calls.append((path, params))
            raise PSNAWPNotFoundError("account does not exist")
        return super().__call__(path, **params)


def test_profiles_fallback_skips_a_bad_account(monkeypatch):
    api = OneBadAccount()
    monkeypatch.setattr(psn_client, "_profile_api", api)
    out = real["get_profiles"](["a", "bad", "c"])
    assert out == {"a": {"onlineId": "single-a"}, "bad": {}, "c": {"onlineId": "single-c"}}
    # The empty answer isn't cached, so the bad account is asked about again next time.
    assert "profile:bad" not in psn_client._cache
    assert {"profile:a", "profile:c"} <= set(psn_client._cache)
    api.calls.clear()
    real["get_profiles"](["a", "bad"])
    assert api.calls == [("/profiles", {"accountIds": "bad"}), ("/bad/profiles", {})]


def test_list_friends_works_with_one_bad_account(monkeypatch):
    from tests.fakes import call
    from tools import friends as friends_mod

    monkeypatch.setattr(psn_client, "_profile_api", OneBadAccount())
    monkeypatch.setattr(friends_mod, "get_friend_ids", lambda: ["a", "bad", "c"])
    monkeypatch.setattr(friends_mod, "get_friend_request_ids", lambda: [])
    monkeypatch.setattr(friends_mod, "get_profiles", real["get_profiles"])
    monkeypatch.setattr(friends_mod, "get_presences", lambda ids: {})
    out = call("list_friends")
    assert out["count"] == 3  # the bad account is kept, so the count stays honest
    assert sorted(f["online_id"] or "" for f in out["friends"]) == ["", "single-a", "single-c"]


def test_profiles_cached_per_account_for_an_hour(api):
    real["get_profiles"](["a", "b"])
    assert set(k for k in psn_client._cache if k.startswith("profile:")) == {"profile:a", "profile:b"}
    api.calls.clear()
    out = real["get_profiles"](["b", "c"])
    assert out == {"b": {"onlineId": "name-b"}, "c": {"onlineId": "name-c"}}
    assert api.calls == [("/profiles", {"accountIds": "c"})]  # b came from the cache
    # just under an hour: still cached; just over: fetched again
    t, v = psn_client._cache["profile:a"]
    psn_client._cache["profile:a"] = (time.time() - HOUR + 5, v)
    api.calls.clear()
    real["get_profiles"](["a"])
    assert api.calls == []
    psn_client._cache["profile:a"] = (time.time() - HOUR - 1, v)
    real["get_profiles"](["a"])
    assert api.calls == [("/profiles", {"accountIds": "a"})]


def test_profiles_not_mixed_with_my_own_profile_key(api):
    psn_client._cache["profile"] = (time.time(), {"mine": True})
    real["get_profiles"](["a"])
    assert psn_client._cache["profile"][1] == {"mine": True}


# ---------- get_presences ----------

class Me:
    def __init__(self):
        self.calls = []

    def get_presences(self, account_ids):
        self.calls.append(list(account_ids))
        # Answer in a different order, and leave out anyone whose id ends in "9" (hidden).
        return {"basicPresences": [raw_presence(a, online=True) for a in reversed(account_ids) if not a.endswith("9")]}


@pytest.fixture
def me(monkeypatch):
    fake = Me()
    monkeypatch.setattr(psn_client, "get_me", lambda: fake)
    return fake


def test_presences_keyed_by_account_id(me):
    out = real["get_presences"](["a1", "b2", "c9"])
    assert set(out) == {"a1", "b2"}
    assert out["a1"]["accountId"] == "a1" and out["a1"]["primaryPlatformInfo"]["onlineStatus"] == "online"
    assert me.calls == [["a1", "b2", "c9"]]


def test_presences_batched(me):
    wanted = ids(205)
    out = real["get_presences"](wanted)
    assert [len(c) for c in me.calls] == [100, 100, 5]
    assert set(out) == {a for a in wanted if not a.endswith("9")}


def test_presences_dedupe_and_empty(me):
    real["get_presences"](["a", "a", "b"])
    assert me.calls == [["a", "b"]]
    assert real["get_presences"]([]) == {}
    assert len(me.calls) == 1


def test_presences_cached_for_a_minute(me):
    real["get_presences"](["a", "b"])
    real["get_presences"](["a", "b"])
    assert len(me.calls) == 1
    assert PRESENCE_SECONDS == 60
    key = "presences:a,b"
    t, v = psn_client._cache[key]
    psn_client._cache[key] = (time.time() - 61, v)
    real["get_presences"](["a", "b"])
    assert len(me.calls) == 2


def test_presences_missing_list(monkeypatch):
    monkeypatch.setattr(psn_client, "get_me", lambda: SimpleNamespace(get_presences=lambda ids: {}))
    assert real["get_presences"](["a"]) == {}


def test_presences_each_batch_gets_its_own_ids(me):
    wanted = ids(150)
    real["get_presences"](wanted)
    assert me.calls == [wanted[:100], wanted[100:]]
    assert {k for k in psn_client._cache if k.startswith("presences:")} == {
        "presences:" + ",".join(wanted[:100]), "presences:" + ",".join(wanted[100:])}


# ---------- other players ----------

class FakePSNAWP:
    def __init__(self):
        self.calls = []

    def user(self, online_id=None, account_id=None):
        self.calls.append(online_id)
        if online_id == "ghost":
            raise PSNAWPNotFoundError("nope")
        return SimpleNamespace(online_id=online_id, account_id="acc-" + online_id.lower())


@pytest.fixture
def psnawp(monkeypatch):
    fake = FakePSNAWP()
    monkeypatch.setattr(psn_client, "get_me", lambda: None)
    monkeypatch.setattr(psn_client, "_psnawp", fake)
    return fake


def test_get_psnawp_logs_in_first(monkeypatch):
    order = []
    sentinel = object()
    monkeypatch.setattr(psn_client, "_psnawp", sentinel)
    monkeypatch.setattr(psn_client, "get_me", lambda: order.append("login"))
    assert real["get_psnawp"]() is sentinel
    assert order == ["login"]


def test_get_player_cached_an_hour_case_insensitive(psnawp):
    u = real["get_player"]("Some_Guy")
    assert u.online_id == "Some_Guy"
    assert real["get_player"]("some_guy") is u
    assert psnawp.calls == ["Some_Guy"]
    assert "player:some_guy" in psn_client._cache
    t, v = psn_client._cache["player:some_guy"]
    psn_client._cache["player:some_guy"] = (time.time() - HOUR - 1, v)
    real["get_player"]("Some_Guy")
    assert len(psnawp.calls) == 2


def test_get_player_not_found_not_cached(psnawp):
    for _ in range(2):
        with pytest.raises(PSNAWPNotFoundError):
            real["get_player"]("ghost")
    assert psnawp.calls == ["ghost", "ghost"]
    assert "player:ghost" not in psn_client._cache


def test_get_player_by_account_id_cached_an_hour(monkeypatch):
    calls = []

    class ByID:
        def user(self, online_id=None, account_id=None):
            assert online_id is None
            calls.append(account_id)
            return SimpleNamespace(online_id="Friend", account_id=account_id)

    monkeypatch.setattr(psn_client, "get_me", lambda: None)
    monkeypatch.setattr(psn_client, "_psnawp", ByID())
    u = real["get_player_by_account_id"]("123")
    assert real["get_player_by_account_id"]("123") is u
    assert calls == ["123"]  # two lookups, one request
    t, v = psn_client._cache["player_id:123"]
    psn_client._cache["player_id:123"] = (time.time() - HOUR - 1, v)
    real["get_player_by_account_id"]("123")
    assert calls == ["123", "123"]


class Player:
    account_id = "acc-1"

    def __init__(self):
        self.calls = []

    def trophy_summary(self):
        self.calls.append("summary")
        return "SUMMARY"

    def trophy_titles(self, limit):
        self.calls.append(("titles", limit))
        return iter(["t1", "t2"])  # a generator, like psnawp's

    def trophies(self, **kw):
        self.calls.append(("trophies", kw))
        return iter(["x"])


def test_player_summary_and_titles_cached_per_player():
    p = Player()
    assert real["get_player_summary"](p) == "SUMMARY"
    assert real["get_player_summary"](p) == "SUMMARY"
    assert real["get_player_trophy_titles"](p) == ["t1", "t2"]
    assert real["get_player_trophy_titles"](p) == ["t1", "t2"]
    assert p.calls == ["summary", ("titles", None)]
    assert {"player_summary:acc-1", "player_titles:acc-1"} <= set(psn_client._cache)


def test_player_title_trophies_uses_my_game_ids():
    p = Player()
    g = title("Cross", platforms=("PS4", "PS5"), np_id="NPWR12345_00")
    assert real["get_player_title_trophies"](p, g) == ["x"]
    real["get_player_title_trophies"](p, g)
    assert p.calls == [("trophies", {"np_communication_id": "NPWR12345_00", "platform": PlatformType.PS5,
                                     "include_progress": True, "trophy_group_id": "all"})]
    assert "player_trophies:acc-1:NPWR12345_00" in psn_client._cache
    # my own trophies for the same game use a different key
    assert "trophies:NPWR12345_00" not in psn_client._cache
