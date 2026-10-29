import asyncio

import server

EXPECTED = {"list_games", "get_game_details", "get_trophies", "get_my_profile",
            "list_friends", "get_player", "compare_with_player", "get_recap"}


def tools():
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def test_tool_names():
    assert set(tools()) == EXPECTED


def test_all_tools_read_only():
    for name, t in tools().items():
        assert t.annotations is not None, name
        assert t.annotations.read_only_hint is True, name
        assert t.annotations.destructive_hint in (None, False), name


def test_instructions_mention_every_tool():
    for name in tools():
        assert name in server.INSTRUCTIONS, name
    assert server.mcp.instructions == server.INSTRUCTIONS


def test_get_trophies_enums_and_defaults():
    props = tools()["get_trophies"].input_schema["properties"]
    assert props["status"]["enum"] == ["unearned", "earned", "all"]
    assert props["status"]["default"] == "unearned"
    sort_enums = [a for a in props["sort_by"]["anyOf"] if "enum" in a]
    assert sort_enums[0]["enum"] == ["easiest", "rarest", "recent"]
    assert props["sort_by"]["default"] is None
    assert props["include_dlc"]["default"] is True
    assert props["limit"]["default"] == 20
    assert props["game_name"]["default"] is None
    assert props["search"]["default"] is None


def test_get_my_profile_takes_no_args():
    assert tools()["get_my_profile"].input_schema.get("properties", {}) == {}


def test_tools_have_descriptions():
    for name, t in tools().items():
        assert t.description and len(t.description) > 40, name


# ---------- warm_cache (runs in a background thread at startup) ----------

def test_warm_cache_fills_the_cache(psn, monkeypatch):
    import psn_client

    calls = []
    psn.add("Some Game")
    psn.play("Some Game", 2)
    for name in ("get_profile", "get_devices", "get_library"):
        orig = getattr(psn_client, name)
        def wrapped(orig=orig, name=name):
            calls.append(name)
            return orig()
        monkeypatch.setattr(psn_client, name, wrapped)
    monkeypatch.setattr(psn_client, "get_friend_ids", lambda: calls.append("get_friend_ids") or ["a1"])
    monkeypatch.setattr(psn_client, "get_profiles", lambda ids: calls.append(("get_profiles", ids)) or {})
    server.warm_cache()
    assert calls == ["get_profile", "get_devices", "get_library", "get_friend_ids", ("get_profiles", ["a1"])]


def test_warm_cache_swallows_failures(psn, caplog):
    from psnawp_api.core.psnawp_exceptions import PSNAWPTooManyRequestsError

    psn.raise_on["titles"] = PSNAWPTooManyRequestsError("slow down")
    server.warm_cache()  # must not raise
    assert "warm-up failed" in caplog.text


def test_warm_cache_swallows_non_psn_errors_in_a_thread(monkeypatch):
    import threading

    import psn_client

    def missing():
        raise RuntimeError("NPSSO is missing")
    monkeypatch.setattr(psn_client, "get_me", missing)
    monkeypatch.setattr(psn_client, "_me", None)
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args.exc_value))
    t = threading.Thread(target=server.warm_cache, daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive()
    assert errors == []


def test_list_games_and_details_schemas_described():
    t = tools()
    assert "never_played" in t["list_games"].description
    assert "web search" in t["get_game_details"].description
