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

## Tools
- `get_trophy_progress(limit, include_completed)`: games by trophy %, highest first
- `get_missing_trophies(game_name, include_dlc)`: unearned trophies for one game, easiest first

## Adding a feature
Create `tools/<feature>.py` with a `register(mcp)` function, then add one import and one
`register` line in `server.py`.
