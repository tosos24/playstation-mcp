"""Phase 1 check: log in to PlayStation and print my top 5 unfinished games by trophy progress."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from psnawp_api import PSNAWP

# Load .env from the project root, no matter which folder this is run from.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Windows terminals can't print some game names (e.g. "ō") without this.
sys.stdout.reconfigure(encoding="utf-8")

npsso = os.getenv("NPSSO")
if not npsso:
    sys.exit("NPSSO is missing. Put it in the .env file (see .env.example).")

# Log in. psnawp swaps the NPSSO for access tokens behind the scenes.
me = PSNAWP(npsso).me()

# Fetch every game on my trophy list (one cheap paginated call).
titles = list(me.trophy_titles(limit=None))

# Skip games I've already finished (100%), then sort highest progress first.
unfinished = [t for t in titles if (t.progress or 0) < 100]
unfinished.sort(key=lambda t: t.progress or 0, reverse=True)

print(f"Logged in as {me.online_id} - {len(titles)} games on trophy list\n")
for t in unfinished[:5]:
    print(f"{t.title_name} - {t.progress}%")
