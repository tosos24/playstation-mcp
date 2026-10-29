# PlayStation MCP

An MCP server that lets Claude read my PlayStation trophy data.

## Setup
```
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env    # then paste your NPSSO into .env
```
Get the NPSSO: sign in at playstation.com, then open
https://ca.account.sony.com/api/v1/ssocookie in the same browser and copy the `npsso` value.
It expires after about 2 months. When the tools start failing, get a new one.

## Run
- Quick check (no MCP): `venv\Scripts\python scripts\check_psn.py`
- Server: `venv\Scripts\python server.py`
- Inspector (needs Node.js): double-click `inspect.cmd`, or run `inspect.cmd` in the project folder.
  Don't use `mcp dev server.py`: it needs `uv` and runs the server without this project's packages.
- Web mode (for hosting): set `MODE=http` and `MCP_SECRET` (see `.env.example`), then run `server.py`.
  MCP is served only at `/mcp/<MCP_SECRET>`; `/health` returns `ok`. The `Dockerfile` runs this mode.

## Tests
```
venv\Scripts\python -m pip install -r requirements-dev.txt
venv\Scripts\python -m pytest                 # unit tests, fake PSN data
set RUN_LIVE=1 && venv\Scripts\python -m pytest -m live   # a few checks against the real account
```
`.claude/agents/` holds the two Claude Code agents used while building: `ps-tester` writes and runs
tests after each step, and `ps-bug-fixer` fixes what it finds.

## Tools
All tools are read-only.
- `list_games(sort_by, filter, limit)`: rank games by trophy %, play time, recency or play count;
  filter unfinished, completed, platinum earned, abandoned (90+ days) or never played
- `get_game_details(game_name)`: one game's hours, play count and base-game vs DLC trophy progress
- `get_trophies(game_name?, status, sort_by, search?, include_dlc, limit)`: individual trophies:
  missing, latest earned, rarest, or one by name ("when did I get the platinum in X?")
- `get_my_profile()`: PSN level, trophy totals, PS Plus, consoles
- `list_friends(online_only)`: friends, who's online and what they're playing, friend requests
- `get_player(name)`: a friend's (or any player's) profile, level and recent games
- `compare_with_player(name, game_name?)`: compare trophies overall or for one game
- `get_recap(period)`: trophies earned, games played and started in the last week, month or this year

Game names can be loose ("spiderman 2", "alan wake 2"); add the console ("... PS5") to pick between
same-named PS4 and PS5 trophy lists.

## Adding a feature
Create `tools/<feature>.py` with a `register(mcp)` function, then add one import and one
`register` line in `server.py`. Decorate tools with `read_only_tool(mcp)` from `tools/common.py`
(marks them read-only and turns PSN errors into clear messages), and add a line to the server
`INSTRUCTIONS` saying which questions the new tool answers.
