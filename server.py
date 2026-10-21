"""PlayStation MCP server: creates the server, registers all tools, starts it."""

from mcp.server.mcpserver import MCPServer

from tools import trophies

mcp = MCPServer("playstation")

# Each feature adds its tools here. To add one: import it and call register.
trophies.register(mcp)

if __name__ == "__main__":
    mcp.run()
