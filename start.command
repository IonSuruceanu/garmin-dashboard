#!/bin/bash
# Double-click this file in Finder (or run it from Terminal) to start the
# dashboard and the Telegram bot. Keep the window open while you use them.
cd "$(dirname "$0")" || exit 1

[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate

echo "Updating…"
git pull --ff-only -q 2>/dev/null || echo "(Couldn't update from GitHub; starting the version you have.)"
pip install -q --disable-pip-version-check -r requirements.txt

(sleep 2; open "http://localhost:8000") &
python serve.py
