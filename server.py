"""PlayStation MCP server: creates the server, registers all tools, starts it.

Two modes, chosen by the MODE environment variable:
- stdio (default): for local use (Inspector, Claude Desktop).
- http: for hosting online. MCP is served only at /mcp/<MCP_SECRET>.
"""

import logging
import os
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from starlette.responses import PlainTextResponse

import psn_client
from tools import friends, games, profile, recap, trophies

load_dotenv(Path(__file__).resolve().parent / ".env")

# Read by Claude when it connects: a short map of which tool answers which kind of question.
INSTRUCTIONS = """Tools for reading my own PlayStation Network data. All tools are read-only.
- Listing or ranking my games (closest to platinum, most played, recently played, abandoned,
  never played): list_games.
- One game in depth (hours, base game vs DLC progress): get_game_details.
- Individual trophies (missing, latest earned, rarest, when I earned one): get_trophies.
- My PSN level, trophy totals, PS Plus, consoles: get_my_profile.
- My friends, who's online and what they're playing, friend requests: list_friends.
- Another player's profile, level and recent games: get_player.
- Comparing my trophies with another player (overall or for one game): compare_with_player.
- What I did in the last week / month / this year (trophies earned, games played): get_recap.
If a tool returns "matches", ask me which game I meant, then call again with that exact name.
For tips on how to earn a trophy, use web search."""

mcp = MCPServer("playstation", instructions=INSTRUCTIONS)

# Each feature adds its tools here. To add one: import it and call register.
games.register(mcp)
trophies.register(mcp)
profile.register(mcp)
friends.register(mcp)
recap.register(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    """Lets the host check the server is up. Reveals nothing about my data."""
    return PlainTextResponse("ok")


def warm_cache():
    """Fetch the big lists in the background at startup, so the first question isn't the slow one."""
    try:
        games.build_games()  # trophy list + play time + title ID lookups
        psn_client.get_profile()
        psn_client.get_devices()
        psn_client.get_library()
        psn_client.get_profiles(psn_client.get_friend_ids())  # names only; online status changes too fast
        logging.getLogger(__name__).info("Cache warmed up")
    except Exception:
        # Not fatal: tools fetch whatever they need on demand.
        logging.getLogger(__name__).exception("Cache warm-up failed")


def run_http():
    """Serve MCP over the web at a secret path, so only someone with the full URL gets in."""
    import uvicorn

    secret = os.getenv("MCP_SECRET", "")
    if len(secret) < 32:
        sys.exit("MCP_SECRET must be set to at least 32 random characters in http mode.")

    app = mcp.streamable_http_app(
        streamable_http_path=f"/mcp/{secret}",
        stateless_http=True,  # no sessions to lose when the host restarts the server
        json_response=True,
        host="0.0.0.0",
    )
    # access_log=False: request logs would print the secret path for anyone who can see the logs.
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), access_log=False)


if __name__ == "__main__":
    threading.Thread(target=warm_cache, daemon=True).start()
    if os.getenv("MODE", "stdio") == "http":
        run_http()
    else:
        mcp.run()
