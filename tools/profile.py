"""Profile tools: my PSN level, trophy totals, PS Plus and consoles."""

from psn_client import get_devices, get_profile, get_trophy_titles

from tools.common import read_only_tool


def register(mcp):
    tool = read_only_tool(mcp)

    @tool
    def get_my_profile() -> dict:
        """Get my PlayStation profile: trophy level, trophy totals, PS Plus, consoles and profile link.

        Use this for questions like "what's my PSN level?", "how many platinums do I have?",
        "do I have PS Plus?" or "which consoles are on my account?". For per-game
        progress or play time use list_games; for individual trophies use get_trophies.

        level_progress_percent is how far I am towards the next trophy level.
        """
        profile = get_profile()
        titles = get_trophy_titles()
        # The profile already carries the trophy summary, which saves a separate request.
        summary = profile.get("trophySummary", {})
        e = summary.get("earnedTrophies", {})
        online_id = profile.get("onlineId")
        # Only pick safe fields: the raw profile also has my real name, and devices have IDs.
        return {
            "online_id": online_id,
            "about_me": profile.get("aboutMe") or None,
            "ps_plus": bool(profile.get("plus")),
            "languages": profile.get("languagesUsed", []),
            "trophy_level": summary.get("level"),
            "level_progress_percent": summary.get("progress"),
            "trophies": {
                **{k: e.get(k, 0) for k in ("platinum", "gold", "silver", "bronze")},
                "total": sum(e.get(k, 0) for k in ("platinum", "gold", "silver", "bronze")),
            },
            "games_on_trophy_list": len(titles),
            "games_at_100_percent": sum(1 for t in titles if (t.progress or 0) == 100),
            "consoles": [
                {
                    "type": d.get("deviceType"),
                    "primary": d.get("activationType") == "PRIMARY",
                    "activated": (d.get("activationDate") or "")[:10] or None,
                }
                for d in get_devices()
            ],
            # Always this format, so no request needed.
            "profile_link": f"https://profile.playstation.com/{online_id}" if online_id else None,
        }
