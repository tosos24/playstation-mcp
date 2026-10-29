import os

import pytest

import psn_client
import tools.common
import tools.games
import tools.friends
import tools.profile
import tools.recap
import tools.trophies
from tests.fakes import FakePSN

HELPERS = ("get_trophy_titles", "get_title_trophies", "get_profile", "get_devices",
           "get_title_stats", "get_library", "get_trophy_groups", "map_title_ids",
           # step 3
           "get_friend_ids", "get_friend_request_ids", "get_profiles", "get_presences", "get_player",
           "get_player_by_account_id", "get_psnawp",
           "get_player_summary", "get_player_trophy_titles", "get_player_title_trophies")
# Every module that imported a fetch helper by name.
MODULES = (psn_client, tools.common, tools.games, tools.trophies, tools.profile, tools.friends, tools.recap)


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live PSN test: set RUN_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def clear_cache(request):
    # Live tests share one cache on purpose, to stay well under PSN's rate limit.
    if request.node.get_closest_marker("live"):
        yield
        return
    psn_client._cache.clear()
    yield
    psn_client._cache.clear()


@pytest.fixture
def psn(monkeypatch):
    """A fake PSN library wired into every module that fetches data."""
    fake = FakePSN()
    for mod in MODULES:
        for name in HELPERS:
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, getattr(fake, name))
    # Anything that still reaches the real login is a test bug.
    def no_login():
        pytest.fail("test tried to log in to PSN")
    monkeypatch.setattr(psn_client, "get_me", no_login)
    monkeypatch.setattr(psn_client, "_me", None)
    return fake
