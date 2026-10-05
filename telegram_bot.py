"""Answer commands sent to your Telegram bot.

    /report   today's full summary
    /today    readiness and workout options, as buttons to pick from
    /refresh  fetch new data from Garmin, then send the report
    /help     list the commands

    python telegram_bot.py          # keep listening (replies within seconds)
    python telegram_bot.py --once   # handle waiting messages and exit (GitHub Actions)

serve.py runs the listener for you while the dashboard is open. The bot only
answers your own chat (the one saved by `telegram_summary.py --setup`).
"""

import argparse
import html
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import telegram_summary as tg

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "site" / "data" / "garmin.json"
STALE_HOURS = 3
COMMANDS = [("report", "Today's full summary"), ("today", "Readiness and workout options"),
            ("refresh", "Fetch new data from Garmin, then report"), ("help", "List the commands")]
_refresh_lock = threading.Lock()


def esc(text):
    return html.escape(str(text), quote=False)


# ---------- data ----------

def load_data():
    return json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else None


def is_stale(data):
    if not data or not data.get("fetchedAt") or data.get("source") != "garmin":
        return True
    age = datetime.now(timezone.utc) - datetime.fromisoformat(data["fetchedAt"])
    return age.total_seconds() > STALE_HOURS * 3600


def refresh(days=28):
    """Fetch fresh data from Garmin in a separate process, using the saved login."""
    if not _refresh_lock.acquire(blocking=False):
        raise ValueError("A refresh is already running.")
    try:
        r = subprocess.run([sys.executable, str(ROOT / "fetch_garmin.py"), "--days", str(days)], cwd=ROOT,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
    finally:
        _refresh_lock.release()
    if r.returncode != 0:
        last = (r.stderr or r.stdout).strip().splitlines()[-4:]
        raise ValueError("Refresh from Garmin failed: " + " ".join(last))
    return (r.stdout.strip().splitlines() or ["Done."])[-1]


def fresh_data():
    data = load_data()
    if is_stale(data):
        refresh()
        data = load_data()
    return data


# ---------- messages ----------

def workout_message(data, key):
    today = data.get("today") or {}
    option = next((o for o in today.get("options", []) if o["key"] == key), None)
    if not option:
        raise ValueError("That workout isn't in today's options any more. Send /today again.")
    lines = [f"<b>📋 Today's plan: {esc(option['title'])}</b> · {esc(option['duration'])}",
             esc(option["summary"]), ""]
    lines += [f"{i}. {esc(step)}" for i, step in enumerate(option["steps"], 1)]
    if option["status"] == "no" and option.get("why"):
        lines += ["", f"⚠️ <i>{esc(option['why'])}</i>"]
    elif option["status"] == "recommended":
        lines += ["", "✅ <i>This is today's recommendation.</i>"]
    if today.get("score") is not None:
        lines.append(f"Readiness {today['score']}/100 · {esc(today.get('label', ''))}")
    return "\n".join(lines)


def today_message(data):
    """Readiness plus one button per workout option."""
    t = data.get("today")
    if not t:
        return "No plan for today yet. Send /refresh.", None
    mark = {"recommended": "⭐ ", "good": "", "no": "⛔ "}
    lines = [f"<b>🏃 Today: {esc(t['label'])}</b> (readiness {t['score']}/100)"]
    lines += [f"{'＋' if f['effect'] == '+' else '−' if f['effect'] == '-' else '·'} {esc(f['text'])}" for f in t.get("factors", [])]
    lines += ["", "Pick what you'd like to do. ⭐ recommended, ⛔ not ideal today:"]
    opts = t.get("options", [])
    buttons = [[(f"{mark.get(o['status'], '')}{o['title']}", f"pick:{o['key']}") for o in opts[i:i + 2]]
               for i in range(0, len(opts), 2)]
    return "\n".join(lines), buttons


HELP = "\n".join(["<b>Garmin bot</b>"] + [f"/{c} · {d}" for c, d in COMMANDS])


# ---------- handling ----------

def handle(update, token, chat_id):
    """Reply to one update. Anything not from your own chat is ignored."""
    if "callback_query" in update:
        q = update["callback_query"]
        tg.telegram(token, "answerCallbackQuery", {"callback_query_id": q["id"]})
        if str(q.get("message", {}).get("chat", {}).get("id")) != str(chat_id):
            return
        if q.get("data", "").startswith("pick:"):
            tg.send(workout_message(fresh_data(), q["data"][5:]))
        return

    msg = update.get("message") or {}
    if str(msg.get("chat", {}).get("id")) != str(chat_id):
        return
    command = (msg.get("text") or "").split()[0].split("@")[0].lower() if msg.get("text") else ""
    if command == "/report":
        tg.send(tg.build_message(fresh_data()))
    elif command == "/today":
        text, buttons = today_message(fresh_data())
        tg.send(text, buttons)
    elif command == "/refresh":
        tg.send("⏳ Fetching from Garmin…")
        refresh()
        tg.send(tg.build_message(load_data()))
    else:
        tg.send(HELP)


def safe_handle(update, token, chat_id):
    try:
        handle(update, token, chat_id)
    except (ValueError, SystemExit) as e:
        tg.send(f"⚠️ {esc(e)}")


def credentials():
    cfg = tg.config()
    if not cfg:
        sys.exit("Telegram isn't set up yet. Run: python telegram_summary.py --setup")
    tg.telegram(cfg["token"], "setMyCommands", {"commands": json.dumps(
        [{"command": c, "description": d} for c, d in COMMANDS])})
    return cfg["token"], cfg["chat_id"]


def run_once():
    """Handle messages that arrived since the last check, then exit."""
    token, chat_id = credentials()
    try:
        updates = tg.telegram(token, "getUpdates", {"timeout": 0})
    except SystemExit as e:
        if "Conflict" in str(e):  # serve.py is listening on your Mac and answers instantly
            print("Another listener (your Mac) is handling commands right now.")
            return
        raise
    try:
        for u in updates:
            safe_handle(u, token, chat_id)
    finally:
        if updates:  # tell Telegram they're done, so they're never handled twice
            tg.telegram(token, "getUpdates", {"offset": updates[-1]["update_id"] + 1, "timeout": 0})
    print(f"Handled {len(updates)} message(s).")


def listen(stop=None):
    """Keep answering until stopped. Long polling: Telegram holds each request
    open for up to 30 s and returns as soon as a message arrives."""
    token, chat_id = credentials()
    offset = None
    while not (stop and stop.is_set()):
        try:
            params = {"timeout": 30, **({"offset": offset} if offset else {})}
            for u in tg.telegram(token, "getUpdates", params, timeout=40):
                offset = u["update_id"] + 1
                safe_handle(u, token, chat_id)
        except SystemExit as e:  # e.g. GitHub's hourly job polling at the same moment
            print(f"Telegram: {e}; retrying shortly.", file=sys.stderr)
            time.sleep(10)
        except Exception as e:  # network hiccup: keep the listener alive
            print(f"Telegram listener error: {e}", file=sys.stderr)
            time.sleep(5)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--once", action="store_true", help="handle waiting messages and exit")
    args = p.parse_args()
    if args.once:
        return run_once()
    print("Listening for Telegram commands (Ctrl+C to stop)…")
    try:
        listen()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
