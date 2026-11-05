# PlayStation MCP

Lets Claude read my PlayStation account, so I can ask things like:

- "Which games am I closest to platinum on?"
- "What was the last trophy I got?"
- "Who's online right now, and what are they playing?"
- "What did I play this month?"

It only reads data. It can't change anything on the account.

## What it can do

| Tool | What it's for |
|---|---|
| `list_games` | Rank my games by trophy progress, hours played or how recently I played them. It can also show games I've abandoned or bought and never played. |
| `get_game_details` | Everything about one game: hours played, and trophy progress for the main game and each DLC |
| `get_trophies` | Find trophies: ones I'm missing, my latest, my rarest, or when I earned a specific one |
| `get_my_profile` | My PSN level, trophy counts and consoles |
| `list_friends` | My friends, who's online and what they're playing |
| `get_player` | Look up a friend or any player |
| `compare_with_player` | Compare my trophies with someone else's |
| `get_recap` | What I did in the last week, month or year |

## How it works

PlayStation has no official API for this, so it uses [psnawp](https://pypi.org/project/psnawp/), an unofficial library. It logs in with a token from my PlayStation account and saves answers for a few minutes, because PlayStation only allows about 300 requests every 15 minutes.

It runs on Render and connects to the Claude app as a custom connector. The address includes a long secret, and without it the server says the page doesn't exist.

## Run it yourself

1. Install: `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and paste in your PlayStation token (NPSSO). To get it, sign in at playstation.com, then open https://ca.account.sony.com/api/v1/ssocookie and copy the `npsso` value. It expires about every 2 months.
3. Try it: run `inspect.cmd` (needs Node.js) and test the tools in your browser.

To put it online, use the `Dockerfile` and set `NPSSO` and `MCP_SECRET` on your host.

## Tests

`pytest` runs about 570 tests on fake data. `RUN_LIVE=1 pytest -m live` runs a few checks against a real account.

While building it, I used two Claude Code agents, kept in `.claude/agents/`: one wrote tests after each step, and the other fixed the bugs it found.
