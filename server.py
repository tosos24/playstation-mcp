"""PlayStation MCP server: creates the server, registers all tools, starts it.

Two modes, chosen by the MODE environment variable:
- stdio (default): for local use (Inspector, Claude Desktop).
- http: for hosting online. MCP is served only at /mcp/<MCP_SECRET>.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from starlette.responses import PlainTextResponse

from tools import trophies

load_dotenv(Path(__file__).resolve().parent / ".env")

mcp = MCPServer("playstation")

# Each feature adds its tools here. To add one: import it and call register.
trophies.register(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    """Lets the host check the server is up. Reveals nothing about my data."""
    return PlainTextResponse("ok")


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
    if os.getenv("MODE", "stdio") == "http":
        run_http()
    else:
        mcp.run()
