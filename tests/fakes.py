"""Fake PSN data built from psnawp's real dataclasses, so the shapes match exactly.

Also the helpers that call tools through the server, the way Claude does.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from itertools import count

from psnawp_api.models.title_stats import PlatformCategory, TitleStats
from psnawp_api.models.trophies import PlatformType
from psnawp_api.models.trophies.trophy import TrophyWithProgress
from psnawp_api.models.trophies.trophy_constants import TrophySet, TrophyType
from psnawp_api.models.trophies.trophy_group import TrophyGroupsSummary, TrophyGroupSummaryWithProgress
from psnawp_api.models.trophies.trophy_titles import TrophyTitle

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_ids = count(1)


def at(hours: float = 0, seconds: float = 0) -> datetime:
    """A UTC time relative to BASE."""
    return BASE + timedelta(hours=hours, seconds=seconds)


def trophy(
    name="Trophy",
    detail="Do a thing",
    kind="bronze",
    earn_rate="50.0",
    earned=False,
    earned_at=None,
    hidden=False,
    group="default",
):
    """A TrophyWithProgress; earn_rate is a STRING like PSN sends it ("6.6")."""
    return TrophyWithProgress(
        trophy_set_version="01.00",
        has_trophy_groups=group not in (None, "default"),
        trophy_id=next(_ids),
        trophy_hidden=hidden,
        trophy_type=TrophyType(kind) if kind else None,
        trophy_name=name,
        trophy_detail=detail,
        trophy_icon_url=None,
        trophy_group_id=group,
        trophy_progress_target_value=None,
        trophy_reward_name=None,
        trophy_reward_img_url=None,
        earned=earned,
        progress=None,
        progress_rate=None,
        progressed_date_time=None,
        earned_date_time=earned_at,
        trophy_rarity=None,
        trophy_earn_rate=earn_rate,
    )


def _count(trophies, *, earned_only=False) -> TrophySet:
    c = {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}
    for t in trophies:
        if t.trophy_type and (t.earned or not earned_only):
            c[t.trophy_type.value] += 1
    return TrophySet(**c)


def title(
    name,
    trophies=(),
    progress=None,
    platforms=("PS5",),
    last_updated="auto",
    np_id=None,
    defined=None,
    earned=None,
):
    """A TrophyTitle. Trophy counts and last_updated are derived from `trophies` unless given."""
    trophies = list(trophies)
    if last_updated == "auto":
        times = [t.earned_date_time for t in trophies if t.earned_date_time]
        last_updated = max(times) if times else None
    defined = defined or _count(trophies)
    earned = earned or _count(trophies, earned_only=True)
    if progress is None:
        progress = round(100 * sum(1 for t in trophies if t.earned) / len(trophies)) if trophies else 0
    return TrophyTitle(
        np_service_name="trophy2",
        np_communication_id=np_id or f"NPWR{next(_ids):05d}_00",
        trophy_set_version="01.00",
        title_name=name,
        title_detail=None,
        title_icon_url=None,
        title_platform=frozenset(PlatformType(p) for p in platforms),
        has_trophy_groups=any(t.trophy_group_id not in (None, "default") for t in trophies),
        progress=progress,
        hidden_flag=False,
        earned_trophies=earned,
        defined_trophies=defined,
        last_updated_datetime=last_updated,
        np_title_id=None,
    )


class FakePSN:
    """Holds the fake library and records which games' trophies were fetched."""

    def __init__(self):
        self.titles = []
        self.trophies = {}  # np_communication_id -> list of trophies
        self.fetched = []  # title names, in fetch order
        self.profile = {}
        self.devices = []
        self.raise_on = {}  # helper name -> exception to raise
        self.stats = []  # TitleStats
        self.library = []  # raw entitlement dicts
        self.groups = {}  # np_communication_id -> TrophyGroupsSummary
        self.title_map = {}  # play-time title ID -> np_communication_id
        self.mapped = []  # title-ID lists passed to map_title_ids

    def add(self, name, trophies=(), **kw):
        t = title(name, trophies, **kw)
        self.titles.append(t)
        self.trophies[t.np_communication_id] = list(trophies)
        return t

    # Replacements for the psn_client fetch helpers.
    def get_trophy_titles(self):
        if "titles" in self.raise_on:
            raise self.raise_on["titles"]
        return list(self.titles)

    def get_title_trophies(self, t):
        if "trophies" in self.raise_on:
            raise self.raise_on["trophies"]
        self.fetched.append(t.title_name)
        return list(self.trophies[t.np_communication_id])

    def get_profile(self):
        if "profile" in self.raise_on:
            raise self.raise_on["profile"]
        return self.profile

    def get_devices(self):
        return self.devices

    def get_title_stats(self):
        if "stats" in self.raise_on:
            raise self.raise_on["stats"]
        return list(self.stats)

    def get_library(self):
        if "library" in self.raise_on:
            raise self.raise_on["library"]
        return list(self.library)

    def get_trophy_groups(self, t):
        if "groups" in self.raise_on:
            raise self.raise_on["groups"]
        return self.groups[t.np_communication_id]

    def map_title_ids(self, title_ids):
        self.mapped.append(list(title_ids))
        return {tid: self.title_map.get(tid) for tid in title_ids}

    # Builders for the step-2 data.
    def play(self, name, hours=1.0, **kw):
        s = stat(name, hours, **kw)
        self.stats.append(s)
        return s

    def own(self, name, **kw):
        item = lib_item(name, **kw)
        self.library.append(item)
        return item

    def set_groups(self, t, *groups):
        self.groups[t.np_communication_id] = groups_summary(t, groups)


NOW = datetime.now(timezone.utc)


def ago(days: float = 0, hours: float = 0) -> datetime:
    """A UTC time relative to the real current time (play-time checks use datetime.now)."""
    return NOW - timedelta(days=days, hours=hours)


_CATEGORY = {"PS4": PlatformCategory.PS4, "PS5": PlatformCategory.PS5, None: PlatformCategory.UNKNOWN,
             "UNKNOWN": PlatformCategory.UNKNOWN}


def stat(name, hours=1.0, platform="PS5", title_id="auto", play_count=1, first=None, last=None,
         duration="auto"):
    """A TitleStats (play time). platform: "PS4", "PS5" or None/"UNKNOWN" (apps)."""
    if title_id == "auto":
        title_id = f"{'CUSA' if platform == 'PS4' else 'PPSA'}{next(_ids):05d}_00"
    if duration == "auto":
        duration = timedelta(hours=hours)
    return TitleStats(
        title_id=title_id,
        name=name,
        image_url=None,
        category=_CATEGORY[platform],
        play_count=play_count,
        first_played_date_time=first if first is not None else ago(days=365),
        last_played_date_time=last if last is not None else ago(days=1),
        play_duration=duration,
    )


def lib_item(name, platform="PS5", title_id="auto", game_meta_name="same", title_meta_name="same"):
    """A raw entitlement dict like game_entitlements() yields."""
    if title_id == "auto":
        title_id = f"{'CUSA' if platform == 'PS4' else 'PPSA'}{next(_ids):05d}_00"
    package = {"PS4": "PS4GD", "PS5": "PSGD"}.get(platform, platform)
    game_meta = {"name": name if game_meta_name == "same" else game_meta_name, "packageType": package}
    return {
        "id": f"UP0006-{title_id}-XXXX",
        "productId": f"UP0006-{title_id}-XXXX" if title_id else None,
        "gameMeta": game_meta,
        "titleMeta": {"titleId": title_id, "name": name if title_meta_name == "same" else title_meta_name},
    }


def group(gid="default", name="Base Game", earned=None, defined=None, progress=0):
    """A TrophyGroupSummaryWithProgress; earned/defined are dicts like {"bronze": 3}."""
    def ts(d):
        return TrophySet(**{"bronze": 0, "silver": 0, "gold": 0, "platinum": 0, **(d or {})})
    return TrophyGroupSummaryWithProgress(
        trophy_group_id=gid,
        trophy_group_name=name,
        trophy_group_detail=None,
        trophy_group_icon_url=None,
        defined_trophies=ts(defined),
        progress=progress,
        earned_trophies=ts(earned),
        last_updated_datetime=None,
    )


def groups_summary(t, groups):
    return TrophyGroupsSummary(
        trophy_set_version="01.00",
        trophy_title_name=t.title_name,
        trophy_title_detail=None,
        trophy_title_icon_url=None,
        trophy_title_platform=set(t.title_platform),
        defined_trophies=t.defined_trophies,
        trophy_groups=list(groups),
        hidden_flag=False,
        progress=t.progress,
        earned_trophies=t.earned_trophies,
        last_updated_datetime=t.last_updated_datetime,
    )


def raw_profile(**over):
    """A raw legacy profile shaped like PSN's, including the private bits the tool must drop."""
    p = {
        "onlineId": "TestPlayer_99",
        "npId": "dGVzdA==",
        "avatarUrls": [{"size": "l", "avatarUrl": "http://example.invalid/a.png"}],
        "plus": 1,
        "aboutMe": "Hello there",
        "languagesUsed": ["en"],
        "trophySummary": {
            "level": 412,
            "progress": 37,
            "earnedTrophies": {"bronze": 3000, "silver": 800, "gold": 250, "platinum": 40},
        },
        "isOfficiallyVerified": False,
        "personalDetail": {"firstName": "SECRETFIRST", "lastName": "SECRETLAST", "profilePictureUrls": []},
        "personalDetailSharing": "shared",
        "primaryOnlineStatus": "online",
        "presences": [],
        "friendRelation": "no",
        "blocking": False,
        "following": False,
        "consoleAvailability": {"availabilityStatus": "offline"},
    }
    p.update(over)
    return p


def raw_devices():
    return [
        {"deviceId": "SECRETDEVICEID1", "deviceType": "PS5", "activationType": "PRIMARY",
         "activationDate": "2021-03-04T10:11:12.000Z", "accountDeviceVector": "x"},
        {"deviceId": "SECRETDEVICEID2", "deviceType": "PS4", "activationType": "PSN_GAME_V3",
         "activationDate": None},
    ]


def call_raw(name, args=None):
    """Call a tool through the server the way Claude does. Returns the CallToolResult."""
    import server

    return asyncio.run(server.mcp.call_tool(name, args or {}))


def call_list(name, args=None) -> list:
    """Call a tool that returns a list: one content item per element."""
    result = call_raw(name, args)
    assert not getattr(result, "is_error", False), result
    return [json.loads(c.text) for c in result.content]


def call(name, args=None) -> dict:
    """Call a tool that returns a dict and decode it."""
    items = call_list(name, args)
    assert len(items) == 1, items
    return items[0]
