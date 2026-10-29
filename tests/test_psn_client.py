"""psn_client: the per-key cache locks, the step-2 fetchers and map_title_ids (against a fake psnawp account)."""

import threading
import time
from types import SimpleNamespace

import pytest
from psnawp_api.core.psnawp_exceptions import PSNAWPBadRequestError, PSNAWPTooManyRequestsError
from psnawp_api.models.trophies import PlatformType

import psn_client
from psn_client import DAY, HOUR, TITLE_ID_BATCH, cached
from tests.fakes import title, trophy

# Captured at import, before any psn fixture patches the module.
real_map_title_ids = psn_client.map_title_ids
real_get_title_stats = psn_client.get_title_stats
real_get_library = psn_client.get_library
real_get_trophy_groups = psn_client.get_trophy_groups


# ---------- cached() ----------

def test_concurrent_callers_of_one_key_fetch_once():
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.2)
        return "value"

    results = []
    threads = [threading.Thread(target=lambda: results.append(cached("same", slow))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert results == ["value"] * 8
    assert len(calls) == 1


def test_different_keys_fetch_in_parallel():
    a_started, b_started = threading.Event(), threading.Event()

    def fetch_a():
        a_started.set()
        assert b_started.wait(2), "key b was blocked by key a's lock"
        return "a"

    def fetch_b():
        b_started.set()
        assert a_started.wait(2)
        return "b"

    out = {}
    ta = threading.Thread(target=lambda: out.setdefault("a", cached("key-a", fetch_a)))
    tb = threading.Thread(target=lambda: out.setdefault("b", cached("key-b", fetch_b)))
    ta.start(); tb.start(); ta.join(5); tb.join(5)
    assert out == {"a": "a", "b": "b"}


def test_failed_fetch_releases_lock_and_is_not_cached():
    def boom():
        raise PSNAWPTooManyRequestsError("slow")

    with pytest.raises(PSNAWPTooManyRequestsError):
        cached("flaky", boom)
    assert "flaky" not in psn_client._cache
    done = threading.Event()
    threading.Thread(target=lambda: (cached("flaky", lambda: 1), done.set())).start()
    assert done.wait(2), "lock was left held after a failed fetch"
    assert cached("flaky", lambda: 2) == 1


def test_cache_expiry():
    assert cached("k", lambda: 1, seconds=60) == 1
    assert cached("k", lambda: 2, seconds=60) == 1
    psn_client._cache["k"] = (time.time() - 61, 1)
    assert cached("k", lambda: 3, seconds=60) == 3


def test_none_value_is_cached():
    calls = []
    assert cached("n", lambda: calls.append(1)) is None
    assert cached("n", lambda: calls.append(1)) is None
    assert len(calls) == 1


# ---------- fake psnawp account ----------

class FakeMe:
    def __init__(self, mapping=None, bad=(), batch_error=None):
        self.mapping = mapping or {}   # title_id -> np_communication_id
        self.bad = set(bad)            # IDs that make any lookup containing them fail
        self.batch_error = batch_error
        self.lookups = []
        self.calls = []

    def trophy_titles_for_title(self, title_ids):
        self.lookups.append(list(title_ids))
        if self.batch_error and len(title_ids) > 1:
            raise self.batch_error
        if self.bad & set(title_ids):
            raise PSNAWPBadRequestError("bad id")
        assert len(title_ids) <= TITLE_ID_BATCH
        return iter([SimpleNamespace(np_title_id=t, np_communication_id=self.mapping[t])
                     for t in title_ids if t in self.mapping])

    def title_stats(self, **kw):
        self.calls.append(("title_stats", kw))
        return iter(["s1", "s2"])

    def game_entitlements(self, **kw):
        self.calls.append(("game_entitlements", kw))
        return iter([{"productId": "x"}])

    def trophy_groups_summary(self, **kw):
        self.calls.append(("trophy_groups_summary", kw))
        return "summary"


@pytest.fixture
def me(monkeypatch):
    fake = FakeMe()
    monkeypatch.setattr(psn_client, "get_me", lambda: fake)
    return fake


# ---------- fetchers ----------

def test_get_title_stats_fetches_all_and_caches(me):
    assert real_get_title_stats() == ["s1", "s2"]
    assert real_get_title_stats() == ["s1", "s2"]
    assert me.calls == [("title_stats", {"limit": None})]
    assert "title_stats" in psn_client._cache


def test_get_library_cached_for_an_hour(me):
    assert real_get_library() == [{"productId": "x"}]
    assert me.calls[0][0] == "game_entitlements" and me.calls[0][1]["limit"] is None
    psn_client._cache["library"] = (time.time() - HOUR + 30, ["old"])
    assert real_get_library() == ["old"]
    psn_client._cache["library"] = (time.time() - HOUR - 30, ["old"])
    assert real_get_library() == [{"productId": "x"}]


def test_get_trophy_groups_per_game(me):
    ps4 = title("Old", [trophy()], platforms=("PS4",))
    multi = title("Cross", [trophy()], platforms=("PS4", "PS5"))
    assert real_get_trophy_groups(ps4) == "summary"
    assert real_get_trophy_groups(multi) == "summary"
    assert real_get_trophy_groups(ps4) == "summary"
    kws = [kw for name, kw in me.calls]
    assert len(kws) == 2
    assert kws[0] == {"np_communication_id": ps4.np_communication_id, "platform": PlatformType.PS4,
                      "include_progress": True}
    assert kws[1]["platform"] == PlatformType.PS5
    assert f"groups:{ps4.np_communication_id}" in psn_client._cache


# ---------- map_title_ids ----------

def ids(n, prefix="PPSA"):
    return [f"{prefix}{i:05d}_00" for i in range(n)]


def test_map_batches_of_five(me):
    wanted = ids(12)
    me.mapping = {t: f"NPWR{i:05d}_00" for i, t in enumerate(wanted) if i % 2 == 0}
    out = real_map_title_ids(wanted)
    assert [len(b) for b in me.lookups] == [5, 5, 2]
    assert out == {t: me.mapping.get(t) for t in wanted}
    assert out[wanted[1]] is None


def test_map_dedupes_and_caches_for_a_day(me):
    me.mapping = {"PPSA00001_00": "NPWR1"}
    assert real_map_title_ids(["PPSA00001_00", "PPSA00001_00", "CUSA00002_00"]) == {
        "PPSA00001_00": "NPWR1", "CUSA00002_00": None}
    assert me.lookups == [["PPSA00001_00", "CUSA00002_00"]]
    assert psn_client._cache["title_map:PPSA00001_00"][1] == "NPWR1"
    assert psn_client._cache["title_map:CUSA00002_00"][1] is None
    # Second call: all cached (including the None), no PSN request.
    assert real_map_title_ids(["CUSA00002_00", "PPSA00001_00"]) == {"CUSA00002_00": None, "PPSA00001_00": "NPWR1"}
    assert len(me.lookups) == 1
    # Only the expired one is looked up again.
    psn_client._cache["title_map:CUSA00002_00"] = (time.time() - DAY - 1, None)
    real_map_title_ids(["CUSA00002_00", "PPSA00001_00"])
    assert me.lookups[-1] == ["CUSA00002_00"]


def test_map_empty(me):
    assert real_map_title_ids([]) == {}
    assert me.lookups == []


def test_map_retries_one_by_one_when_batch_fails(me):
    wanted = ids(5)
    me.mapping = {t: "NP_" + t for t in wanted}
    me.bad = {wanted[2]}
    out = real_map_title_ids(wanted)
    assert me.lookups[0] == wanted
    assert me.lookups[1:] == [[t] for t in wanted]
    assert out == {t: (None if t == wanted[2] else "NP_" + t) for t in wanted}


def test_map_only_failing_batch_is_retried(me):
    wanted = ids(7)
    me.mapping = {t: "NP_" + t for t in wanted}
    me.bad = {wanted[6]}
    real_map_title_ids(wanted)
    assert [len(b) for b in me.lookups] == [5, 2, 1, 1]


def test_map_non_psn_errors_propagate(me, monkeypatch):
    def broken(title_ids):
        raise RuntimeError("NPSSO is missing")
    monkeypatch.setattr(me, "trophy_titles_for_title", broken)
    with pytest.raises(RuntimeError):
        real_map_title_ids(["PPSA00001_00"])
    assert "title_map:PPSA00001_00" not in psn_client._cache


@pytest.mark.xfail(strict=True, reason="BUG: a rate-limit (or other transient PSN) error during the title-ID lookup "
                                       "is cached as 'no trophy list' (None) for a whole day")
def test_map_transient_error_not_cached_as_no_trophy_list(me):
    me.mapping = {"PPSA00001_00": "NPWR1"}
    me.batch_error = None
    original = me.trophy_titles_for_title
    state = {"limited": True}

    def maybe_limited(title_ids):
        if state["limited"]:
            raise PSNAWPTooManyRequestsError("rate limited")
        return original(title_ids)

    me.trophy_titles_for_title = maybe_limited
    try:
        real_map_title_ids(["PPSA00001_00"])
    except PSNAWPTooManyRequestsError:
        pass  # raising is a fine way to handle it too
    state["limited"] = False  # PSN recovers a minute later
    assert real_map_title_ids(["PPSA00001_00"]) == {"PPSA00001_00": "NPWR1"}


@pytest.mark.xfail(strict=True, reason="BUG (minor): map_title_ids bypasses the per-key locks, so the startup warm-up "
                                       "and a first tool call look the same title IDs up twice")
def test_map_concurrent_callers_look_up_once(me):
    wanted = ids(3)
    me.mapping = {t: "NP_" + t for t in wanted}
    original = me.trophy_titles_for_title

    def slow(title_ids):
        time.sleep(0.2)
        return original(title_ids)

    me.trophy_titles_for_title = slow
    threads = [threading.Thread(target=real_map_title_ids, args=(wanted,)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(me.lookups) == 1
