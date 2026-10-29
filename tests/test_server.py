import asyncio

import server

EXPECTED = {"get_trophy_progress", "get_trophies", "get_my_profile"}


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


def test_get_trophy_progress_defaults():
    props = tools()["get_trophy_progress"].input_schema["properties"]
    assert props["limit"]["default"] == 20
    assert props["include_completed"]["default"] is False


def test_get_my_profile_takes_no_args():
    assert tools()["get_my_profile"].input_schema.get("properties", {}) == {}


def test_tools_have_descriptions():
    for name, t in tools().items():
        assert t.description and len(t.description) > 40, name
