"""Send a daily Garmin summary to Telegram.

Reads site/data/garmin.json (written by fetch_garmin.py) and sends a short
message to your own Telegram bot. Uses only the Python standard library.

    python telegram_summary.py --setup    # one-time: save bot token + chat id
    python telegram_summary.py            # send the summary
    python telegram_summary.py --dry-run  # print it instead of sending
"""

import argparse
import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "site" / "data" / "garmin.json"
CONFIG_FILE = ROOT / "telegram.json"  # gitignored: holds your bot token


def telegram(token, method, params=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = urllib.parse.urlencode(params or {}).encode()
    try:
        with urllib.request.urlopen(url, data=body, timeout=20) as r:
            return json.load(r)["result"]
    except urllib.error.HTTPError as e:
        detail = json.load(e).get("description", e.reason)
        sys.exit(f"Telegram error: {detail}")


def setup():
    print("1. In Telegram, open @BotFather, send /newbot and follow the steps.")
    print("2. BotFather replies with a token like 123456:ABC-xyz. Paste it here.")
    token = input("Bot token: ").strip()
    bot = telegram(token, "getMe")
    print(f"\n3. Now open your bot (t.me/{bot['username']}), press Start or send it any message.")
    input("   Press Enter here once you've done that… ")
    updates = telegram(token, "getUpdates")
    chats = [u["message"]["chat"] for u in updates if "message" in u]
    if not chats:
        sys.exit("No message found yet. Send your bot a message and run --setup again.")
    chat = chats[-1]
    CONFIG_FILE.write_text(json.dumps({"token": token, "chat_id": chat["id"]}, indent=1))
    CONFIG_FILE.chmod(0o600)
    telegram(token, "sendMessage", {"chat_id": chat["id"], "text": "✅ Garmin dashboard connected. Your summaries will arrive here."})
    print(f"Saved to {CONFIG_FILE.name}. Check Telegram for a test message.")
    print(f"For GitHub Actions use: TELEGRAM_BOT_TOKEN = your token, TELEGRAM_CHAT_ID = {chat['id']}")


def send(message):
    """Send an HTML-formatted message to your chat."""
    # GitHub Actions passes these as secrets; on your Mac they come from telegram.json.
    cfg = {"token": os.getenv("TELEGRAM_BOT_TOKEN"), "chat_id": os.getenv("TELEGRAM_CHAT_ID")}
    if not (cfg["token"] and cfg["chat_id"]):
        if not CONFIG_FILE.exists():
            sys.exit("Telegram isn't set up yet. Run: python telegram_summary.py --setup")
        cfg = json.loads(CONFIG_FILE.read_text())
    telegram(cfg["token"], "sendMessage", {"chat_id": cfg["chat_id"], "text": message,
                                           "parse_mode": "HTML", "disable_web_page_preview": "true"})



def esc(text):
    """Escape for Telegram HTML (apostrophes and quotes can stay as they are)."""
    return html.escape(text, quote=False)

# ---------- message ----------

def avg(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def latest(rows, key):
    for r in reversed(rows):
        if r.get(key) is not None:
            return r
    return None


def hm(minutes):
    return f"{int(minutes // 60)}h {int(round(minutes % 60)):02d}m"


def trend(value, baseline, unit="", higher_is_better=True, fmt="{:.0f}"):
    """'  ↑4 vs 7-day avg' style suffix; blank when there's no baseline."""
    if value is None or baseline is None:
        return ""
    diff = value - baseline
    if abs(diff) < 0.5:
        return "  · on your average"
    good = (diff > 0) == higher_is_better
    return f"  {'↑' if diff > 0 else '↓'}{fmt.format(abs(diff))}{unit} {'🟢' if good else '🟠'}"


def build_message(data):
    days = data.get("daily") or []
    if not days:
        return "No Garmin data found. Run fetch_garmin.py first."
    today, history = days[-1], days[-8:-1]  # baseline = the 7 days before today
    yesterday = days[-2] if len(days) > 1 else None
    lines = [f"<b>☀️ Garmin summary · {datetime.strptime(today['date'], '%Y-%m-%d'):%a %d %b}</b>", ""]

    night = today if today.get("sleepMin") else latest(days, "sleepMin")
    if night:
        score = f" · score {night['sleepScore']}" if night.get("sleepScore") else ""
        lines.append(f"😴 Sleep: <b>{hm(night['sleepMin'])}</b>{score}" +
                     trend(night["sleepMin"], avg(history, "sleepMin"), "m"))
        stages = [f"{label} {hm(night[k])}" for label, k in (("deep", "deepMin"), ("REM", "remMin")) if night.get(k)]
        if stages:
            lines.append("      " + " · ".join(stages))
    hrv = latest(days[-2:], "hrv")
    if hrv:
        status = f" ({hrv['hrvStatus'].lower().replace('_', ' ')})" if hrv.get("hrvStatus") else ""
        lines.append(f"💓 HRV: <b>{hrv['hrv']} ms</b>{status}" + trend(hrv["hrv"], avg(history, "hrv"), " ms"))
    rhr = latest(days[-2:], "restingHr")
    if rhr and not rhr.get("sleepMin"):
        rhr = None  # daytime estimate, not a real resting value
    if rhr:
        lines.append(f"❤️ Resting HR: <b>{rhr['restingHr']} bpm</b>" +
                     trend(rhr["restingHr"], avg(history, "restingHr"), " bpm", higher_is_better=False))
    if today.get("bodyBatteryHigh"):
        lines.append(f"🔋 Body Battery: <b>{today['bodyBatteryHigh']}</b>")

    if yesterday and (yesterday.get("steps") or yesterday.get("stressAvg") is not None):
        lines += ["", "<b>Yesterday</b>"]
        if yesterday.get("steps"):
            lines.append(f"👟 {yesterday['steps']:,} steps" + trend(yesterday["steps"], avg(days[-9:-2], "steps"), fmt="{:,.0f}"))
        if yesterday.get("stressAvg") is not None:
            lines.append(f"🧠 Stress avg {yesterday['stressAvg']}")

    acts = data.get("activities") or []
    recent = [a for a in acts if (a.get("start") or "")[:10] >= (yesterday or today)["date"]]
    if recent:
        lines += ["", "<b>Latest activity</b>"]
        for a in recent[:3]:
            bits = [f"{a['distanceKm']:.2f} km" if a.get("distanceKm") else None,
                    hm(a["durationMin"]) if a.get("durationMin") else None,
                    f"{a['avgHr']:.0f} bpm" if a.get("avgHr") else None]
            lines.append(f"🏃 {esc(a.get('name') or 'Activity')}: " + " · ".join(b for b in bits if b))

    t = data.get("today")
    if t and t.get("options"):
        rec = next((o for o in t["options"] if o["status"] == "recommended"), None)
        lines += ["", f"<b>🏃 Today: {esc(t['label'])}</b> (readiness {t['score']}/100)"]
        if rec:
            lines.append(f"👉 <b>{esc(rec['title'])}</b>, {esc(rec['duration'])}: {esc(rec['summary'])}")
        others = [o["title"] for o in t["options"] if o["status"] == "good"]
        avoid = [o["title"] for o in t["options"] if o["status"] == "no"]
        if others:
            lines.append("Also fine: " + esc(", ".join(others)))
        if avoid:
            lines.append("Not today: " + esc(", ".join(avoid)))

    tips = [i for i in data.get("insights") or [] if i["level"] in ("watch", "good")][:2]
    if tips:
        lines += ["", "<b>Insights</b>"]
        for i in tips:
            lines.append(f"{'⚠️' if i['level'] == 'watch' else '✅'} <b>{esc(i['title'])}</b>: {esc(i['detail'])}")

    if data.get("source") == "sample":
        lines += ["", "<i>(demo data, not your real numbers)</i>"]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--setup", action="store_true", help="connect your Telegram bot (one time)")
    p.add_argument("--dry-run", action="store_true", help="print the message instead of sending it")
    p.add_argument("--data", default=str(DATA_FILE), help="data file to summarise")
    args = p.parse_args()

    if args.setup:
        return setup()

    path = Path(args.data)
    if not path.exists():
        sys.exit(f"{path.name} not found. Run fetch_garmin.py first.")
    data = json.loads(path.read_text())
    if data.get("fetchedAt", "")[:10] < date.today().isoformat():
        print("Note: data wasn't fetched today; run fetch_garmin.py for fresh numbers.", file=sys.stderr)
    message = build_message(data)

    if args.dry_run:
        print(message)
        return
    send(message)
    print("Summary sent to Telegram.")


if __name__ == "__main__":
    main()
