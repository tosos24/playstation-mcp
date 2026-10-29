"""Fake PSN data built from psnawp's real dataclasses, so the shapes match exactly.

Also the helpers that call tools through the server, the way Claude does.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from itertools import count

from psnawp_api.core.psnawp_exceptions import PSNAWPForbiddenError, PSNAWPNotFoundError
from psnawp_api.models.title_stats import PlatformCategory, TitleStats
from psnawp_api.models.trophies import PlatformType
from psnawp_api.models.trophies.trophy import TrophyWithProgress
from psnawp_api.models.trophies.trophy_constants import TrophySet, TrophyType
from psnawp_api.models.trophies.trophy_group import TrophyGroupsSummary, TrophyGroupSummaryWithProgress
from psnawp_api.models.trophies.trophy_summary import TrophySummary
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
        # Step 3: friends and other players.
        self.friend_ids = []  # my friends' account IDs, in PSN's order
        self.request_ids = []  # account IDs that sent me a friend request
        self.profiles = {}  # account ID -> raw basic profile
        self.presences = {}  # account ID -> raw basic presence (missing = hidden)
        self.players = {}  # account ID -> FakeUser (friends and others)
        self.summaries = {}  # account ID -> TrophySummary
        self.player_titles = {}  # account ID -> list of TrophyTitle
        self.player_trophies = {}  # (account ID, np_communication_id) -> list of trophies
        self.player_errors = {}  # (account ID, "summary"|"titles"|"trophies") -> exception
        self.player_lookups = []  # names passed to get_player
        self.user_lookups = []  # account IDs passed to get_psnawp().user(account_id=...)

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

    # Step-3 fetch helpers.
    def get_friend_ids(self):
        return list(self.friend_ids)

    def get_friend_request_ids(self):
        return list(self.request_ids)

    def get_profiles(self, account_ids):
        return {aid: self.profiles[aid] for aid in dict.fromkeys(account_ids) if aid in self.profiles}

    def get_presences(self, account_ids):
        return {aid: self.presences[aid] for aid in dict.fromkeys(account_ids) if aid in self.presences}

    def get_player(self, online_id):
        self.player_lookups.append(online_id)
        for u in self.players.values():
            if u.online_id.lower() == online_id.lower():
                return u
        raise PSNAWPNotFoundError(f"Online ID {online_id} does not exist.")

    def get_psnawp(self):
        fake = self

        class _P:
            def user(self, account_id=None, online_id=None):
                assert account_id is not None
                fake.user_lookups.append(account_id)
                return fake.players[account_id]
        return _P()

    def _player_error(self, player, kind):
        err = self.player_errors.get((player.account_id, kind))
        if err:
            raise err

    def get_player_summary(self, player):
        self._player_error(player, "summary")
        return self.summaries[player.account_id]

    def get_player_trophy_titles(self, player):
        self._player_error(player, "titles")
        return list(self.player_titles.get(player.account_id, []))

    def get_player_title_trophies(self, player, t):
        self._player_error(player, "trophies")
        key = (player.account_id, t.np_communication_id)
        if key not in self.player_trophies:
            raise PSNAWPNotFoundError("no trophies")
        return list(self.player_trophies[key])

    def add_friend(self, online_id, *, online=None, last_online=None, platform="PS5", playing=None,
                   plus=False, about="", presence=True, aid=None):
        """A friend: profile + optional presence. online=None with presence=False means hidden."""
        aid = self.add_player(online_id, plus=plus, about=about, aid=aid)
        self.friend_ids.append(aid)
        if presence:
            self.presences[aid] = raw_presence(aid, online=bool(online), last_online=last_online,
                                               platform=platform, playing=playing)
        return aid

    def add_player(self, online_id, *, plus=False, about="", aid=None, level=100, earned=None,
                   titles=(), private=False):
        """Any PSN player (friend or not), with a trophy summary and trophy list."""
        aid = aid or f"{next(_ids) + 1000000000000000000}"
        self.players[aid] = FakeUser(online_id, aid)
        self.profiles[aid] = raw_basic_profile(online_id, plus=plus, about=about)
        self.summaries[aid] = TrophySummary(
            account_id=aid, trophy_level=level, progress=10, tier=2,
            earned_trophies=TrophySet(**{"bronze": 0, "silver": 0, "gold": 0, "platinum": 0, **(earned or {})}))
        self.player_titles[aid] = list(titles)
        if private:
            for kind in ("summary", "titles", "trophies"):
                self.player_errors[(aid, kind)] = PSNAWPForbiddenError("private")
        return aid

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


class FakeUser:
    """Shaped like psnawp's User: just the two IDs the tools read."""

    def __init__(self, online_id, account_id):
        self.online_id = online_id
        self.account_id = account_id

    def __repr__(self):
        return f"FakeUser({self.online_id!r})"


def raw_basic_profile(online_id, plus=False, about=""):
    """One entry of PSN's /profiles batch answer (no account ID in it, like the real one)."""
    return {"onlineId": online_id, "aboutMe": about, "avatars": [], "languages": ["en"],
            "isPlus": plus, "isOfficiallyVerified": False, "isMe": False,
            "personalDetail": {"firstName": "SECRETFIRST", "lastName": "SECRETLAST"}}


def iso(dt: datetime) -> str:
    """PSN's timestamp format: 2026-01-01T12:00:00.000Z."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def raw_presence(aid, online=False, last_online=None, platform="PS5", playing=None):
    """One entry of get_presences()["basicPresences"]."""
    p = {
        "accountId": aid,
        "availability": "availableToPlay" if online else "unavailable",
        "primaryPlatformInfo": {"onlineStatus": "online" if online else "offline", "platform": platform},
    }
    if last_online:
        p["lastAvailableDate"] = iso(last_online)
        p["primaryPlatformInfo"]["lastOnlineDate"] = iso(last_online)
    if playing:
        p["gameTitleInfoList"] = [{"npTitleId": "PPSA00001_00", "titleName": playing, "format": "PS5",
                                   "launchPlatform": platform}]
    return p


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
