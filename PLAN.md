# PlayStation MCP Server: Build Plan

## Goal
Build an MCP server that lets Claude read my PlayStation data (trophies, play time, friends) so I can ask questions about it in the Claude app. It must be easy to add new features over time.

## Tech stack
- Python 3.11+
- `mcp[cli]` (official MCP Python SDK v2, using `MCPServer`, which was called `FastMCP` in v1; the `[cli]` extra is needed for `mcp dev`)
- `psnawp` (unofficial PlayStation library; check its docs for current method names)
- `python-dotenv` for local secrets
- Node.js (needed by the MCP Inspector in Phase 3)
- Hosting: Render or Railway (Phase 4). Render's free tier sleeps, and cold starts can time out the connector.

## Rules for Claude Code
- Build one phase at a time. Stop after each phase so I can test it.
- Keep code simple and commented in plain language.
- Never hard-code or print the NPSSO token. Read it from an environment variable.
- Tools must return clean, small JSON (lists/dicts), not raw library objects.
- Every tool needs a clear docstring, because Claude uses it to decide when to call the tool.
- Load `.env` relative to the file (`Path(__file__)`), not the current folder, so it works when Claude Desktop launches the server.
- Cache PSN responses in memory for 5-10 minutes. PSN allows about 300 requests per 15 minutes.

---

## Phase 1: Get the data working (no MCP yet)
1. `git init`, a virtual environment and `requirements.txt` (`mcp[cli]`, `psnawp`, `python-dotenv`).
2. Create `.env` with `NPSSO=...` and `.env.example` with a blank value.
3. Create `.gitignore` that includes `.env` and `venv/`.
4. Write `scripts/check_psn.py` that logs in and prints my top 5 **unfinished** games by trophy progress (skip 100%).

**Done when:** running the script prints something like:
```
Elden Ring - 92%
God of War - 85%
Spider-Man 2 - 60%
```

How to get the NPSSO: log in at playstation.com, then open `https://ca.account.sony.com/api/v1/ssocookie` and copy the 64-character value.

---

## Phase 2: Build the MCP server (extensible layout)

### Folder structure
```
playstation-mcp/
  server.py          # creates the server, registers all tools, starts it
  psn_client.py      # logs in to PlayStation once, shared by all tools
  tools/
    __init__.py
    trophies.py      # first feature
  scripts/
    check_psn.py
  .env / .env.example / .gitignore / requirements.txt / README.md
```

### The pattern every feature follows
Each file in `tools/` has one `register(mcp)` function that adds its tools:

```python
# tools/trophies.py
from psn_client import get_me

def register(mcp):
    @mcp.tool()
    def get_trophy_progress(limit: int = 20) -> list[dict]:
        """List my unfinished games with trophy completion %, closest to 100% first."""
        ...
```

`server.py` calls `register` for every tools module:

```python
from tools import trophies
trophies.register(mcp)
```

**To add a feature later:** create `tools/<feature>.py` with a `register(mcp)` function, then add one import and one `register` line in `server.py`. Nothing else changes.

### First tools (trophies.py)
- `get_trophy_progress(limit)`: unfinished games with completion %. Leave out 100% games. Note in the docstring that PSN's % is weighted by trophy points and that some games have no platinum.
- `get_missing_trophies(game_name)`: trophies not yet earned for one game (name, description, rarity). Match the name fuzzily, ignoring case and ™/®. If several games match (e.g. PS4 and PS5 versions), return the matches instead of guessing. Hidden trophies have no description, so mark them as hidden.

**Done when:** both tools exist and the server starts without errors.

---

## Phase 3: Test locally
1. Run `inspect.cmd` (the MCP Inspector using the project venv; `mcp dev` needs `uv`) and test each tool in the Inspector page.
2. Optional: add the server to Claude Desktop's config file and chat with it.

**Done when:** in the Inspector, `get_trophy_progress` with `limit=3` returns:
```json
[
  {"game": "Elden Ring", "progress_percent": 92},
  {"game": "God of War", "progress_percent": 85},
  {"game": "Spider-Man 2", "progress_percent": 60}
]
```

---

## Phase 4: Put it online and connect to the Claude app
The Claude app can't reach my laptop, so the server must be hosted.

1. Add a setting so the server can run in web mode ("streamable HTTP") as well as local mode. Use an environment variable to choose, for example `MODE=http`.
2. Add simple protection: serve MCP only at a secret path, e.g. `/mcp/<long-random-secret>`, and reject everything else. The Claude app's custom connectors can't send custom headers (only no-auth or OAuth), so the secret has to be in the URL. Treat the full URL like a password.
3. Add a `Dockerfile` or start command for Render/Railway.
4. Deploy. Set `NPSSO` and the secret key as environment variables on the host, not in code.
5. In the Claude app, add the server as a custom connector using its URL.
6. Turn on web search in the app (for "how do I get this trophy" questions).

**Done when:** in the Claude app I ask "Which games am I closest to platinum on?" and get a real answer from my data.

---

## Feature backlog (add one at a time)
- [ ] `playtime.py`: hours per game, games played this year (PSN gives total play time and last-played date only, with no per-year hours; PS4/PS5 only)
- [ ] Trophy rarity: my rarest trophies
- [ ] Trophy level: PSN level and progress
- [ ] `friends.py`: friends list and who's online (one presence call per friend, so cache it)
- [ ] Compare trophies with a friend for the same game
- [ ] Combined question: "most hours but never finished"
- [ ] `get_story_completion`: games where I earned the "beat the story" trophy but not the platinum (the PS5 "Game Progress" bar isn't reachable through PSN's web API)

## Known risks
- PSNAWP is unofficial, so it may break if Sony changes things.
- The NPSSO token expires every so often. When tools start failing, get a new one first.
- Keep the token out of GitHub at all times.
