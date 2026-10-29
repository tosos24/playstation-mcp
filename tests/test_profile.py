import json

import pytest

from tests.fakes import call, raw_devices, raw_profile, trophy


@pytest.fixture
def prof(psn):
    psn.profile = raw_profile()
    psn.devices = raw_devices()
    psn.add("Done", [trophy(earned=True)], progress=100)
    psn.add("Half", [trophy(earned=True), trophy()], progress=50)
    psn.add("Unknown", [], progress=None)
    return psn


def test_profile_fields(prof):
    out = call("get_my_profile")
    assert out == {
        "online_id": "TestPlayer_99",
        "about_me": "Hello there",
        "ps_plus": True,
        "languages": ["en"],
        "trophy_level": 412,
        "level_progress_percent": 37,
        "trophies": {"platinum": 40, "gold": 250, "silver": 800, "bronze": 3000, "total": 4090},
        "games_on_trophy_list": 3,
        "games_at_100_percent": 1,
        "consoles": [
            {"type": "PS5", "primary": True, "activated": "2021-03-04"},
            {"type": "PS4", "primary": False, "activated": None},
        ],
        "profile_link": "https://profile.playstation.com/TestPlayer_99",
    }


def test_no_private_data_anywhere(prof):
    text = json.dumps(call("get_my_profile"))
    for secret in ("personalDetail", "SECRETFIRST", "SECRETLAST", "deviceId", "SECRETDEVICEID",
                   "npId", "accountDeviceVector"):
        assert secret not in text


def test_minimal_profile(psn):
    psn.profile = {"onlineId": "X"}
    out = call("get_my_profile")
    assert out["about_me"] is None
    assert out["ps_plus"] is False
    assert out["languages"] == []
    assert out["trophy_level"] is None
    assert out["trophies"] == {"platinum": 0, "gold": 0, "silver": 0, "bronze": 0, "total": 0}
    assert out["games_on_trophy_list"] == 0
    assert out["consoles"] == []
    assert out["profile_link"] == "https://profile.playstation.com/X"


def test_no_online_id_no_link(psn):
    psn.profile = {}
    out = call("get_my_profile")
    assert out["online_id"] is None and out["profile_link"] is None


def test_empty_about_me_is_none_and_plus_zero(psn):
    psn.profile = raw_profile(aboutMe="", plus=0)
    out = call("get_my_profile")
    assert out["about_me"] is None
    assert out["ps_plus"] is False


def test_partial_trophy_counts(psn):
    psn.profile = raw_profile(trophySummary={"level": 1, "progress": 0, "earnedTrophies": {"bronze": 2}})
    assert call("get_my_profile")["trophies"] == {"platinum": 0, "gold": 0, "silver": 0, "bronze": 2, "total": 2}
