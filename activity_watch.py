"""Send a Telegram message for each new Garmin activity.

Garmin doesn't notify personal apps when you finish a workout, so this checks
your recent activities and messages you about any it hasn't seen before.
Run it on a schedule (the GitHub workflow runs it every hour).

    python activity_watch.py            # check and send
    python activity_watch.py --dry-run  # print messages, don't send or mark as seen

The first run only remembers your existing activities, so you don't get a
flood of messages for old workouts.
"""

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path

import fetch_garmin
import run_detail
import store
import telegram_summary
import weather

STATE_FILE = Path(fetch_garmin.TOKEN_DIR).expanduser() / "seen_activities.json"
RUN_TYPES = ("running", "walking", "hiking")
SWIM_TYPES = ("swimming",)
TE_LABELS = [(1, "no real effect"), (2, "minor"), (3, "maintaining fitness"), (4, "improving fitness"),
             (5, "highly improving"), (99, "overreaching")]



def esc(text):
    """Escape for Telegram HTML (apostrophes and quotes can stay as they are)."""
    return html.escape(text, quote=False)

def mmss(minutes):
    m, s = divmod(round(minutes * 60), 60)
    return f"{m}:{s:02d}"


def hm(minutes):
    h, m = divmod(round(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


def pace(a):
    """Human pace/speed for the activity type, plus a number to compare (lower = faster for pace)."""
    t, km, mins = a.get("type") or "", a.get("distanceKm"), a.get("durationMin")
    if not km or not mins:
        return None, None
    if any(x in t for x in SWIM_TYPES):
        per100 = mins / (km * 10)
        return f"{mmss(per100)} /100m", per100
    if any(x in t for x in RUN_TYPES):
        per_km = mins / km
        return f"{mmss(per_km)} /km", per_km
    kmh = a.get("avgSpeedKmh") or km / (mins / 60)
    return f"{kmh:.1f} km/h", -kmh  # negative so that lower = faster, like pace


def te_label(te):
    for limit, label in TE_LABELS:
        if te < limit:
            return label
    return ""


def compare(a, history):
    """One line comparing this activity with your recent ones of the same type."""
    same = [h for h in history if h.get("type") == a.get("type") and h["id"] != a["id"]][:10]
    if len(same) < 2:
        return None
    bits = []
    _, p = pace(a)
    past = [pace(h)[1] for h in same if pace(h)[1] is not None]
    if p is not None and past:
        avg = sum(past) / len(past)
        change = (p - avg) / abs(avg) * 100
        if abs(change) >= 2:
            bits.append(f"{'faster' if change < 0 else 'slower'} than usual ({abs(change):.0f}%)")
        else:
            bits.append("usual pace")
    hrs = [h["avgHr"] for h in same if h.get("avgHr")]
    if a.get("avgHr") and hrs:
        d = a["avgHr"] - sum(hrs) / len(hrs)
        if abs(d) >= 3:
            bits.append(f"heart rate {abs(d):.0f} bpm {'higher' if d > 0 else 'lower'}")
    dists = [h["distanceKm"] for h in same if h.get("distanceKm")]
    if a.get("distanceKm") and dists and a["distanceKm"] > max(dists):
        bits.append(f"your longest in the last {len(same)}")
    return f"vs your last {len(same)}: " + ", ".join(bits) if bits else None


def message(a, history):
    start = datetime.strptime(a["start"], "%Y-%m-%d %H:%M:%S") if a.get("start") else None
    kind = (a.get("type") or "activity").replace("_", " ")
    lines = [f"<b>🏁 {esc(a.get('name') or kind.title())}</b>",
             f"{kind.capitalize()}" + (f" · {start:%a %d %b, %H:%M}" if start else ""), ""]
    main = [f"{a['distanceKm']:.2f} km" if a.get("distanceKm") else None,
            hm(a["durationMin"]) if a.get("durationMin") else None,
            pace(a)[0]]
    lines.append("📏 " + " · ".join(b for b in main if b))
    if a.get("avgHr"):
        lines.append(f"❤️ Avg {a['avgHr']} bpm" + (f" · max {a['maxHr']}" if a.get("maxHr") else ""))
    extra = [f"{a['calories']} kcal" if a.get("calories") else None,
             f"{a['elevationM']} m climb" if a.get("elevationM") else None]
    if any(extra):
        lines.append("🔥 " + " · ".join(b for b in extra if b))
    if a.get("aerobicTE") is not None:
        te = f"🎯 Training effect {a['aerobicTE']} aerobic ({te_label(a['aerobicTE'])})"
        if a.get("anaerobicTE"):
            te += f" · {a['anaerobicTE']} anaerobic"
        lines.append(te)
    if a.get("trainingLoad"):
        lines.append(f"📈 Training load {a['trainingLoad']}")
    # Garmin attaches its VO2 max estimate to runs; mention it when it moves.
    if a.get("vo2max"):
        prev = next((h["vo2max"] for h in history if h.get("vo2max") and h["id"] != a["id"]
                     and (h.get("start") or "") < (a.get("start") or "")), None)
        if prev and a["vo2max"] != prev:
            lines.append(f"🫁 VO2 max {'up' if a['vo2max'] > prev else 'down'} to <b>{a['vo2max']}</b> (was {prev})")
        elif not prev:
            lines.append(f"🫁 VO2 max {a['vo2max']}")
    w = a.get("weather")
    if w:
        line = f"🌡 {w['tempC']:.0f}°C" + (f", dew point {w['dewPointC']:.0f}°C" if w.get("dewPointC") is not None else "")
        if a.get("heatPct") and a.get("heatAdjPace"):
            line += f" · heat cost ~{a['heatPct']:.1f}%, about {mmss(a['heatAdjPace'])} /km in cool air"
        lines.append(line)
    if a.get("decoupling") is not None:
        verdict = "steady" if a["decoupling"] <= 5 else "drifted"
        lines.append(f"📉 Heart-rate drift {a['decoupling']:+.1f}% ({verdict})")
    note = compare(a, history)
    if note:
        lines += ["", f"<i>{esc(note)}</i>"]
    return "\n".join(lines)


def add_weather_and_drift(api, a):
    """Weather at the run, and HR drift from its laps (runs over 4 km)."""
    if a.get("lat") is not None and a.get("start"):
        w = weather.lookup(a["lat"], a["lon"], a["start"], a.get("durationMin"))
        if w:
            a["weather"] = w
            a["heatPct"] = weather.heat_slowdown_pct(w.get("tempC"), w.get("dewPointC"))
            if a.get("distanceKm") and a.get("durationMin"):
                a["heatAdjPace"] = weather.adjusted_pace(a["durationMin"] / a["distanceKm"], a["heatPct"])
    if "running" in (a.get("type") or "") and (a.get("distanceKm") or 0) >= fetch_garmin.DETAIL_MIN_KM:
        splits = fetch_garmin.safe("laps", api.get_activity_splits, a["id"])
        raw = fetch_garmin.safe("details", api.get_activity_details, a["id"], 1000, 0)
        d = {"laps": run_detail.laps(splits), "series": run_detail.series(raw)}
        d["decoupling"] = a["decoupling"] = run_detail.decoupling(d)
        store.save_detail(a["id"], d)  # saved for the dashboard too


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="print messages instead of sending")
    args = p.parse_args()

    api = fetch_garmin.connect()
    raw = fetch_garmin.safe("activities", api.get_activities, 0, 40)
    if raw is None:
        sys.exit("Couldn't load activities from Garmin.")
    recent = [fetch_garmin.activity_record(a) for a in raw]
    recent.sort(key=lambda a: a.get("start") or "", reverse=True)

    if not STATE_FILE.exists():
        if not args.dry_run:
            STATE_FILE.write_text(json.dumps({"seen": [a["id"] for a in recent]}))
            telegram_summary.send("👀 Watching for new Garmin activities. You'll get a message after each one.")
        print(f"First run: remembered {len(recent)} existing activities; new ones will be sent from now on.")
        return

    seen = json.loads(STATE_FILE.read_text()).get("seen", [])
    new = [a for a in reversed(recent) if a["id"] not in seen]  # oldest first
    for a in new:
        add_weather_and_drift(api, a)
        msg = message(a, recent)
        if args.dry_run:
            print(msg, end="\n\n")
            continue
        telegram_summary.send(msg)
        seen.append(a["id"])
        # Save after each send, so a failure part-way doesn't resend earlier ones.
        STATE_FILE.write_text(json.dumps({"seen": seen[-500:]}))
    print(f"{len(new)} new activit{'y' if len(new) == 1 else 'ies'}.")


if __name__ == "__main__":
    main()
