#!/bin/bash
# Double-click this file in Finder (or run it from Terminal) to start the
# dashboard and the Telegram bot. Keep the window open while you use them.
cd "$(dirname "$0")" || exit 1

pause_on_error() {
  echo
  echo "Something went wrong (see the message above)."
  read -n 1 -s -r -p "Press any key to close this window…"
  echo
  exit 1
}

[ -d .venv ] || python3 -m venv .venv || pause_on_error
source .venv/bin/activate

echo "Updating…"
git pull --ff-only -q 2>/dev/null || echo "(Couldn't update from GitHub; starting the version you have.)"
pip install -q --disable-pip-version-check -r requirements.txt || pause_on_error

(sleep 2; open "http://localhost:8000") &
python serve.py || pause_on_error
