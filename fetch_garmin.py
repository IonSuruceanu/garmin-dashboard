"""Download your own data from Garmin Connect into site/data/garmin.json.

Uses the unofficial `garminconnect` library, which signs in the same way the
Garmin Connect website does. Your password is only used for the first sign-in;
after that the saved login tokens in ~/.garminconnect are reused.

    python fetch_garmin.py              # last 30 days
    python fetch_garmin.py --days 90
    python fetch_garmin.py --sample     # write fake demo data, no Garmin login
    python fetch_garmin.py --print-tokens  # show saved login for the GitHub secret
"""

import argparse
import getpass
import json
import os
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import insights

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "site" / "data"
TOKEN_DIR = os.getenv("GARMINTOKENS", "~/.garminconnect")


def dig(obj, *keys):
    """Safe nested lookup: dig(d, "a", "b") -> d["a"]["b"] or None."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def connect():
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )

    token_path = Path(TOKEN_DIR).expanduser()
    token_path.mkdir(parents=True, exist_ok=True)

    # Saved tokens are tried first; credentials are only needed when they are
    # missing or expired.
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    has_tokens = any(token_path.iterdir())
    if not has_tokens and not (email and password):
        if not sys.stdin.isatty():
            sys.exit(f"No saved Garmin login in {token_path} and no GARMIN_EMAIL/GARMIN_PASSWORD set.")
        email = email or input("Garmin email: ").strip()
        password = password or getpass.getpass("Garmin password: ")

    api = Garmin(email, password, prompt_mfa=lambda: input("Garmin 2-step code: ").strip())
    try:
        api.login(str(token_path))
    except GarminConnectAuthenticationError as e:
        sys.exit(
            f"Garmin sign-in failed: {e}\n"
            f"If you changed your password, delete {token_path} and run again."
        )
    except (GarminConnectTooManyRequestsError, GarminConnectConnectionError) as e:
        # Each run tries several sign-in methods, so retrying straight away
        # only extends Garmin's block.
        sys.exit(
            f"Couldn't sign in to Garmin: {e}\n"
            "If that mentions 429 or 'rate limited', Garmin is temporarily blocking sign-ins\n"
            "from this internet connection. Wait 1-2 hours without retrying, or connect to a\n"
            "different network (for example your phone's hotspot) and run the script once.\n"
            "Otherwise, check your internet connection and try again."
        )
    return api


def safe(label, fn, *args):
    """Call one Garmin endpoint; a failure skips that value instead of the whole run."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - one missing metric shouldn't stop the export
        print(f"  ! {label}: {e}", file=sys.stderr)
        return None


def fetch_day(api, d):
    ds = d.isoformat()
    stats = safe(f"stats {ds}", api.get_stats, ds) or {}
    sleep = dig(safe(f"sleep {ds}", api.get_sleep_data, ds), "dailySleepDTO") or {}
    hrv = dig(safe(f"hrv {ds}", api.get_hrv_data, ds), "hrvSummary") or {}

    def minutes(sec):
        return round(sec / 60) if sec else None

    return {
        "date": ds,
        "steps": stats.get("totalSteps"),
        "calories": stats.get("totalKilocalories"),
        "activeCalories": stats.get("activeKilocalories"),
        "distanceKm": round(stats["totalDistanceMeters"] / 1000, 2) if stats.get("totalDistanceMeters") else None,
        "restingHr": stats.get("restingHeartRate"),
        "stressAvg": stats.get("averageStressLevel") if (stats.get("averageStressLevel") or -1) >= 0 else None,
        "bodyBatteryHigh": stats.get("bodyBatteryHighestValue"),
        "bodyBatteryLow": stats.get("bodyBatteryLowestValue"),
        "intensityMinutes": (stats.get("moderateIntensityMinutes") or 0) + 2 * (stats.get("vigorousIntensityMinutes") or 0) or None,
        "sleepMin": minutes(sleep.get("sleepTimeSeconds")),
        "deepMin": minutes(sleep.get("deepSleepSeconds")),
        "lightMin": minutes(sleep.get("lightSleepSeconds")),
        "remMin": minutes(sleep.get("remSleepSeconds")),
        "awakeMin": minutes(sleep.get("awakeSleepSeconds")),
        "sleepScore": dig(sleep, "sleepScores", "overall", "value"),
        "hrv": hrv.get("lastNightAvg"),
        "hrvStatus": hrv.get("status"),
    }


def activity_record(a):
    """Trim a Garmin activity to the fields the dashboard and Telegram use."""
    def rnd(v, n=0):
        return round(v, n) if v is not None else None
    return {
        "id": a.get("activityId"),
        "name": a.get("activityName"),
        "type": dig(a, "activityType", "typeKey"),
        "start": a.get("startTimeLocal"),
        "distanceKm": round(a["distance"] / 1000, 2) if a.get("distance") else None,
        "durationMin": round(a["duration"] / 60, 1) if a.get("duration") else None,
        "avgHr": rnd(a.get("averageHR")),
        "maxHr": rnd(a.get("maxHR")),
        "avgSpeedKmh": round(a["averageSpeed"] * 3.6, 2) if a.get("averageSpeed") else None,
        "calories": rnd(a.get("calories")),
        "elevationM": rnd(a.get("elevationGain")),
        "aerobicTE": rnd(a.get("aerobicTrainingEffect"), 1),
        "anaerobicTE": rnd(a.get("anaerobicTrainingEffect"), 1),
        "trainingLoad": rnd(a.get("activityTrainingLoad")),
    }


def fetch_activities(api, start, end):
    raw = safe("activities", api.get_activities_by_date, start.isoformat(), end.isoformat()) or []
    out = [activity_record(a) for a in raw]
    out.sort(key=lambda a: a["start"] or "", reverse=True)
    return out


def sample_data(days):
    """Plausible fake numbers so the dashboard can be tried without a Garmin account."""
    rnd = random.Random(7)
    today = date.today()
    daily = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        deep, rem, light = rnd.randint(55, 100), rnd.randint(70, 120), rnd.randint(190, 260)
        daily.append({
            "date": d.isoformat(), "steps": rnd.randint(4000, 16000), "calories": rnd.randint(2100, 3200),
            "activeCalories": rnd.randint(300, 1100), "distanceKm": round(rnd.uniform(3, 14), 2),
            "restingHr": rnd.randint(48, 56), "stressAvg": rnd.randint(18, 42),
            "bodyBatteryHigh": rnd.randint(70, 100), "bodyBatteryLow": rnd.randint(10, 35),
            "intensityMinutes": rnd.randint(0, 90), "sleepMin": deep + rem + light, "deepMin": deep,
            "lightMin": light, "remMin": rem, "awakeMin": rnd.randint(5, 40),
            "sleepScore": rnd.randint(62, 92), "hrv": rnd.randint(52, 78), "hrvStatus": "BALANCED",
        })
    kinds = [("running", "Easy run", 8, 50), ("running", "Tempo run", 10, 55), ("cycling", "Road ride", 42, 95),
             ("strength_training", "Strength", None, 45), ("lap_swimming", "Pool swim", 2.2, 40)]
    activities = []
    for i in range(0, days, 2):
        t, name, dist, dur = rnd.choice(kinds)
        start = datetime.combine(today - timedelta(days=i), datetime.min.time()) + timedelta(hours=7, minutes=rnd.randint(0, 90))
        activities.append({
            "id": 1000 + i, "name": name, "type": t, "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "distanceKm": round(dist * rnd.uniform(.8, 1.25), 2) if dist else None,
            "durationMin": round(dur * rnd.uniform(.8, 1.3), 1), "avgHr": rnd.randint(118, 158),
            "calories": rnd.randint(250, 900), "elevationM": rnd.randint(0, 400) if dist else None,
        })
        a = activities[-1]
        a["maxHr"] = a["avgHr"] + rnd.randint(12, 30)
        a["avgSpeedKmh"] = round(a["distanceKm"] / (a["durationMin"] / 60), 2) if a["distanceKm"] else None
        a["aerobicTE"] = round(rnd.uniform(2.0, 4.2), 1)
        a["anaerobicTE"] = round(rnd.uniform(0.0, 2.5), 1)
        a["trainingLoad"] = rnd.randint(40, 220)
    return {"athlete": "Demo athlete", "daily": daily, "activities": activities}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=30, help="how many days back to fetch (default 30)")
    p.add_argument("--sample", action="store_true", help="write fake demo data to site/data/sample.json")
    p.add_argument("--print-tokens", action="store_true",
                   help="print your saved Garmin login, to paste into the GARMIN_TOKENS GitHub secret")
    args = p.parse_args()

    if args.print_tokens:
        token_file = Path(TOKEN_DIR).expanduser() / "garmin_tokens.json"
        if not token_file.exists():
            sys.exit("No saved Garmin login yet. Run `python fetch_garmin.py` once first.")
        print(token_file.read_text())
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.sample:
        payload, target = sample_data(args.days), DATA_DIR / "sample.json"
    else:
        api = connect()
        end = date.today()
        start = end - timedelta(days=args.days - 1)
        print(f"Fetching {args.days} days from Garmin Connect ({start} to {end})…")
        daily = []
        for i in range(args.days):
            d = start + timedelta(days=i)
            daily.append(fetch_day(api, d))
            print(f"  {d}", end="\r")
            time.sleep(0.3)  # be gentle; Garmin rate-limits aggressive clients
        payload = {
            "athlete": safe("name", api.get_full_name),
            "daily": daily,
            "activities": fetch_activities(api, start, end),
        }
        target = DATA_DIR / "garmin.json"

    payload["insights"] = insights.build(payload)
    payload["source"] = "sample" if args.sample else "garmin"
    payload["fetchedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    target.write_text(json.dumps(payload, indent=1))
    print(f"\nSaved {len(payload['daily'])} days and {len(payload['activities'])} activities to {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
