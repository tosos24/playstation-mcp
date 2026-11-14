---
name: psn-stats-unofficial
description: Answer questions about the user's own PlayStation Network account (trophies, platinum progress, hours played, games owned, friends online, comparing trophies with a friend, and weekly/monthly/yearly recaps) using the playstation-mcp server's tools. Use when the user asks about their PSN trophies, PlayStation games, play time or PSN friends. Unofficial; not affiliated with Sony. Needs the playstation-mcp MCP server connected.
---

# PSN stats (unofficial)

**Unofficial.** Not made by or connected to Sony. The playstation-mcp server uses psnawp, an
unofficial PlayStation library, with the user's own login token. Sony doesn't support this, and
heavy use could get an account restricted. All tools only read data, and the server stays under
PlayStation's rate limits.

## Before you start

This skill needs the **playstation-mcp** MCP server: https://github.com/tosos24/playstation-mcp

- If tools like `list_games` and `get_trophies` aren't available, tell the user the server isn't
  connected and point them to the setup steps in that repository's README. Don't try to install
  or run it yourself.
- Never ask for, repeat or store the user's PSN token (NPSSO) or the server's connector URL. Setup
  happens on the server, not in the chat.

## Which tool to use

| The user asks... | Call |
|---|---|
| Closest to platinum / most completed | `list_games` (defaults: sort by trophy progress, unfinished only) |
| Most played / most hours | `list_games` with `sort_by="playtime"` |
| Most hours but never finished | `list_games` with `sort_by="playtime"`, `filter="unfinished"` |
| What I've played lately | `list_games` with `sort_by="recently_played"` |
| Games I abandoned / never played | `list_games` with `filter="abandoned"` or `filter="never_played"` |
| Everything about one game, DLC progress, hours in it | `get_game_details` |
| Missing trophies in a game | `get_trophies` with `game_name` |
| Latest trophy (overall or in a game) | `get_trophies` with `status="earned"`, `sort_by="recent"`, `limit=1` |
| Rarest trophies | `get_trophies` with `status="earned"`, `sort_by="rarest"` |
| When I earned a trophy (e.g. a platinum) | `get_trophies` with `status="earned"`, `search="platinum"` (add `game_name` if known) |
| PSN level, trophy totals, PS Plus, consoles | `get_my_profile` |
| Who's online, what friends are playing, friend requests | `list_friends` |
| A friend's or another player's level and recent games | `get_player` |
| How I compare with someone (overall or in one game) | `compare_with_player` |
| What I did this week / month / year | `get_recap` with `period` |

Pick the single tool that fits. One broad call is better than many narrow ones.

## Answering well

- **Game names can be loose** ("spiderman 2", "alan wake 2"). If a tool returns `matches`, ask the
  user which game they meant, then call again with that exact name. Add the console ("... PS5")
  when two games share a name.
- **Read the caveat fields and pass them on:** `note`, `partial`, `left_out`, `play_time_warning`,
  `"private"` and `"none"`. For example, if a recap is `partial`, say the total may be incomplete.
- **Trophy % is weighted by trophy points** and includes DLC, so it isn't the share of trophies earned.
- **Play time exists only for PS4 and PS5 games**, as a total per game. PSN has no hours per week
  or month, so don't estimate them.
- **Trophy % isn't story completion.** Finishing a game and earning its trophies are different things.
- **For how to earn a trophy,** get the trophy's details with `get_trophies`, then use web search
  for a guide.
- **Keep calls few.** PlayStation limits requests. Don't loop over many games or players; use the
  tools' own lists and limits instead.

## Examples

**"Which games am I closest to platinum on?"**
Call `list_games` with `limit=5`. Answer with each game's `trophy_progress_percent`, and mention any
game where `has_platinum` is false.

**"What was the last trophy I got in Ratchet?"**
Call `get_trophies` with `game_name="ratchet"`, `status="earned"`, `sort_by="recent"`, `limit=1`.

**"What am I missing in Spider-Man 2, and how do I get them?"**
Call `get_trophies` with `game_name="spider-man 2"`. It returns missing trophies, easiest first. Then
web-search tips for the first few.

**"How do I compare with my friend Alex?"**
Call `compare_with_player` with `name="alex"`. If it returns `matches`, ask which friend.
