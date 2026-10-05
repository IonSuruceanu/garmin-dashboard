"""Download your own data from Garmin Connect into site/data/garmin.json.

Uses the unofficial `garminconnect` library, which signs in the same way the
Garmin Connect website does. History, per-run details and weather are kept in
site/data/store/, so each run only fetches what's new. Your password is only used for the first sign-in;
after that the saved login tokens in ~/.garminconnect are reused.

    python fetch_garmin.py              # 90 days of daily data, activities since 1 Jan 2026
    python fetch_garmin.py --since 2025-06-01   # go further back for activities
    python fetch_garmin.py --quick      # last 12 weeks only, fast (GitHub job)
    python fetch_garmin.py --sample     # write fake demo data, no Garmin login
    python fetch_garmin.py --reanalyse  # recalculate insights from saved data, no Garmin login
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

import coach
import insights
import run_detail
import store
import weather

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "site" / "data"
DAILY_DAYS = 90  # sleep, HRV, resting HR, ... (a longer baseline for readiness trends)
HISTORY_FROM = "2026-01-01"  # activities back to here (training blocks, fitness vs earlier months)
DETAIL_MIN_KM = 4  # laps and time series for runs at least this long
MAX_DETAILS_PER_FETCH = 40  # spread the first big download over a few fetches
MAX_WEATHER_PER_FETCH = 150
ACTIVITY_DAYS = 84  # used by the demo data
TOKEN_DIR = os.getenv("GARMINTOKENS", "~/.garminconnect")
# On GitHub the logs can be public: print counts only, never names or error text.
QUIET = bool(os.getenv("GITHUB_ACTIONS"))


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
        detail = type(e).__name__ if QUIET else e
        print(f"  ! {label.split()[0]}: {detail}", file=sys.stderr)
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
        "cadence": rnd(a.get("averageRunningCadenceInStepsPerMinute")),
        "vo2max": a.get("vO2MaxValue"),
        # Running dynamics (need a compatible watch or HRM strap)
        "strideCm": rnd(a.get("avgStrideLength")),
        "gctMs": rnd(a.get("avgGroundContactTime")),
        "vertOscCm": rnd(a.get("avgVerticalOscillation"), 1),
        "vertRatio": rnd(a.get("avgVerticalRatio"), 1),
        # Start point, rounded to ~100 m: only used to look up the weather
        "lat": rnd(a.get("startLatitude"), 3),
        "lon": rnd(a.get("startLongitude"), 3),
        # Minutes in heart-rate zones 1-5, when Garmin includes them.
        "hrZonesMin": ([round((a.get(f"hrTimeInZone_{z}") or 0) / 60, 1) for z in range(1, 6)]
                       if any(a.get(f"hrTimeInZone_{z}") for z in range(1, 6)) else None),
    }


def fetch_activities(api, start, end):
    raw = safe("activities", api.get_activities_by_date, start.isoformat(), end.isoformat()) or []
    out = [activity_record(a) for a in raw]
    out.sort(key=lambda a: a["start"] or "", reverse=True)
    return out


def first(v):
    """Some Garmin endpoints return a list, others a single object."""
    return (v[0] if v else None) if isinstance(v, list) else v


def find_key(obj, key):
    """First value for `key` anywhere inside nested dicts/lists."""
    if isinstance(obj, dict):
        if key in obj and obj[key] is not None:
            return obj[key]
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            found = find_key(v, key)
            if found is not None:
                return found
    return None


def fetch_settings(api, user):
    """Your own heart-rate settings from Garmin: max HR, zones, lactate threshold."""
    settings = {}
    zones = safe("heart-rate zones", api.connectapi, "/biometric-service/heartRateZones")
    if isinstance(zones, list) and zones:
        # Prefer running-specific zones over the default set.
        z = next((x for x in zones if (x.get("sport") or "").upper() == "RUNNING"), zones[0])
        floors = [z.get(f"zone{i}Floor") for i in range(1, 6)]
        if all(floors):
            settings["zones"] = floors
            settings["zonesMethod"] = (z.get("trainingMethod") or "").replace("_", " ").lower()
        for key, ours in (("maxHeartRateUsed", "maxHr"), ("restingHeartRateUsed", "restingHr"),
                          ("lactateThresholdHeartRateUsed", "lthr")):
            if z.get(key):
                settings[ours] = round(z[key])
    if not settings.get("lthr"):
        lthr = user.get("lactateThresholdHeartRate") or find_key(
            safe("lactate threshold", lambda: api.get_lactate_threshold(latest=True)), "heartRate")
        if lthr:
            settings["lthr"] = round(lthr)
    if settings:
        settings["source"] = "Garmin"
    return settings


def fetch_extras(api, today):
    """Garmin's own running metrics and settings. Each is optional: older watches lack some."""
    ds = today.isoformat()
    extras = {}

    tr = first(safe("training readiness", api.get_training_readiness, ds))
    if isinstance(tr, dict) and tr.get("score") is not None:
        extras["readiness"] = {"score": tr.get("score"), "level": tr.get("level"),
                               "feedback": tr.get("feedbackShort")}

    ts = safe("training status", api.get_training_status, ds) or {}
    latest = dig(ts, "mostRecentTrainingStatus", "latestTrainingStatusData") or {}
    for device in latest.values():
        phrase = (device or {}).get("trainingStatusFeedbackPhrase")
        if phrase:
            extras["trainingStatus"] = phrase.rstrip("_0123456789").replace("_", " ").capitalize()
            break
    acute, chronic = find_key(ts, "dailyTrainingLoadAcute"), find_key(ts, "dailyTrainingLoadChronic")
    if acute is not None:
        extras["garminLoad"] = {"acute": round(acute), "chronic": round(chronic) if chronic else None}
    vo2 = dig(ts, "mostRecentVO2Max", "generic") or dig(first(safe("max metrics", api.get_max_metrics, ds)), "generic") or {}
    if vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue"):
        extras["vo2max"] = round(vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue"), 1)

    user = dig(safe("profile", api.get_user_profile), "userData") or {}
    profile = {}
    if user.get("gender") in ("MALE", "FEMALE"):
        profile["sex"] = user["gender"].lower()
    if user.get("birthDate"):
        born = date.fromisoformat(user["birthDate"][:10])  # only the age is kept
        profile["age"] = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    if profile:
        extras["profile"] = profile
    if not extras.get("vo2max") and user.get("vo2MaxRunning"):
        extras["vo2max"] = round(user["vo2MaxRunning"], 1)
    settings = fetch_settings(api, user)
    if settings:
        extras["settings"] = settings

    rp = first(safe("race predictions", api.get_race_predictions))
    if isinstance(rp, dict):
        keys = {"5k": "time5K", "10k": "time10K", "half": "timeHalfMarathon", "marathon": "timeMarathon"}
        pred = {k: round(rp[g] / 60, 1) for k, g in keys.items() if rp.get(g)}
        if pred.get("10k"):
            extras["racePredictions"] = pred

    body = safe("weight", api.get_body_composition, (today - timedelta(days=180)).isoformat(), ds) or {}
    weights = {}
    for w in body.get("dateWeightList") or []:
        if w.get("weight") and w.get("calendarDate"):
            weights[w["calendarDate"]] = round(w["weight"] / 1000, 1)  # grams -> kg
    if weights:
        extras["garminWeights"] = weights
    return extras


# ---------- incremental sync into the local store ----------

def sync_daily(api, days):
    """Daily metrics: fetch days we don't have yet, plus the last two (still changing)."""
    saved = store.read("daily.json", {})
    today = date.today()
    wanted = [(today - timedelta(days=i)).isoformat() for i in range(days)]
    todo = [d for d in wanted if d not in saved or d >= (today - timedelta(days=1)).isoformat()]
    print(f"Daily data: {len(todo)} day(s) to fetch ({len(saved)} saved)…")
    for n, ds in enumerate(sorted(todo), 1):
        saved[ds] = fetch_day(api, date.fromisoformat(ds))
        if not QUIET:
            print(f"  {ds}  ({n}/{len(todo)})", end="\r")
        time.sleep(0.3)  # be gentle; Garmin rate-limits aggressive clients
        if n % 20 == 0:
            store.write("daily.json", saved)  # keep progress if interrupted
    store.write("daily.json", saved)
    return [saved[d] for d in sorted(saved) if d in wanted]


def sync_activities(api, since):
    """Activities since `since`. After the first full download, only the last few weeks
    are re-fetched (to catch edits), the rest comes from the store."""
    saved = {str(k): v for k, v in store.read("activities.json", {}).items()}
    meta = store.read("meta.json", {})
    today = date.today()
    if meta.get("historyFrom", "9999") > since or not saved:
        start = date.fromisoformat(since)
    else:
        newest = max((a["start"][:10] for a in saved.values() if a.get("start")), default=since)
        start = max(date.fromisoformat(since), date.fromisoformat(newest) - timedelta(days=21))
    print(f"Activities: fetching {start} to {today}…")
    for a in fetch_activities(api, start, today):
        old = saved.get(str(a["id"]), {})
        a["decoupling"] = old.get("decoupling")  # computed from details, keep it
        saved[str(a["id"])] = a
    store.write("activities.json", saved)
    meta["historyFrom"] = min(meta.get("historyFrom", since), since)
    store.write("meta.json", meta)
    return saved


def sync_details(api, acts):
    """Laps and time series for runs over DETAIL_MIN_KM, fetched once per run."""
    todo = [a for a in sorted(acts.values(), key=lambda a: a.get("start") or "", reverse=True)
            if "running" in (a.get("type") or "") and (a.get("distanceKm") or 0) >= DETAIL_MIN_KM
            and store.detail(a["id"]) is None]
    if not todo:
        return
    batch = todo[:MAX_DETAILS_PER_FETCH]
    print(f"Run details: {len(batch)} of {len(todo)} run(s) to fetch…")
    for n, a in enumerate(batch, 1):
        splits = safe(f"laps {a['id']}", api.get_activity_splits, a["id"])
        raw = safe(f"details {a['id']}", api.get_activity_details, a["id"], 1000, 0)
        d = {"laps": run_detail.laps(splits), "series": run_detail.series(raw)}
        d["decoupling"] = run_detail.decoupling(d)
        store.save_detail(a["id"], d)
        acts[str(a["id"])]["decoupling"] = d["decoupling"]
        if not QUIET:
            print(f"  {a['start'][:10]} {a.get('name') or ''}  ({n}/{len(batch)})", end="\r")
        time.sleep(0.5)
    store.write("activities.json", acts)
    if len(todo) > len(batch):
        print(f"\n  {len(todo) - len(batch)} more run(s) will be fetched next time.")


def sync_weather(acts):
    """Weather for each activity with a start location, looked up once."""
    saved = store.read("weather.json", {})
    retry_after = (date.today() - timedelta(days=1)).isoformat()
    todo = [a for a in acts.values() if a.get("lat") is not None and a.get("start")
            and (str(a["id"]) not in saved or saved[str(a["id"])].get("failed", "9999") < retry_after)]
    if not todo:
        return
    print(f"Weather: {min(len(todo), MAX_WEATHER_PER_FETCH)} activit(ies) to look up…")
    for a in todo[:MAX_WEATHER_PER_FETCH]:
        w = weather.lookup(a["lat"], a["lon"], a["start"], a.get("durationMin"))
        saved[str(a["id"])] = w or {"failed": date.today().isoformat()}
    store.write("weather.json", saved)


# ---------- assembling garmin.json ----------

def enrich(activities, weather_by_id, notes, detail_dir="store/details"):
    """Add weather, heat-adjusted pace, your notes and a link to per-run details."""
    out = []
    for a in activities:
        a = dict(a)
        a.pop("lat", None)
        a.pop("lon", None)
        w = weather_by_id.get(str(a["id"]))
        if w and "failed" not in w:
            a["weather"] = w
            a["heatPct"] = weather.heat_slowdown_pct(w.get("tempC"), w.get("dewPointC"))
            if a.get("distanceKm") and a.get("durationMin") and "running" in (a.get("type") or ""):
                a["heatAdjPace"] = round(weather.adjusted_pace(a["durationMin"] / a["distanceKm"], a["heatPct"]), 3)
        note = notes.get("runs", {}).get(str(a["id"]))
        if note:
            a["note"] = note
        if a.get("decoupling") is not None or (detail_dir and store.detail(a["id"]) is not None):
            a["detailPath"] = f"{detail_dir}/{a['id']}.json"
        out.append(a)
    return sorted(out, key=lambda a: a.get("start") or "", reverse=True)


def finish(payload):
    """Coaching, insights and timestamps on top of the raw data."""
    payload.update(coach.build(payload))
    payload["insights"] = insights.build(payload)
    payload["fetchedAt"] = payload.get("fetchedAt") or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return payload


def rebuild(fetched_at=None):
    """Recalculate garmin.json from the store (after a note, or with --reanalyse)."""
    target = DATA_DIR / "garmin.json"
    old = json.loads(target.read_text()) if target.exists() else {}
    acts = store.read("activities.json", {})
    if not acts:
        if not old:
            sys.exit("No saved data yet. Run `python fetch_garmin.py` first.")
        payload = old  # data from before the store existed
    else:
        daily = store.read("daily.json", {})
        since = store.read("meta.json", {}).get("historyFrom", HISTORY_FROM)
        notes = store.notes()
        extras = dict(old.get("extras") or {})
        extras["weights"] = {**extras.get("garminWeights", {}), **notes["weights"]}
        payload = {
            "athlete": old.get("athlete"),
            "source": "garmin",
            "historyFrom": since,
            "daily": [daily[d] for d in sorted(daily)][-DAILY_DAYS:],
            "activities": enrich([a for a in acts.values() if (a.get("start") or "") >= since],
                                 store.read("weather.json", {}), notes),
            "extras": extras,
            "fetchedAt": fetched_at or old.get("fetchedAt"),
        }
    for k in ("today", "running", "insights"):
        payload.pop(k, None)
    target.write_text(json.dumps(finish(payload), indent=1))
    return payload


def sample_data(days):
    """Plausible fake numbers so the dashboard can be tried without a Garmin account:
    a year with build-and-stop training blocks, a hot summer and per-run details."""
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
    since = date(today.year, 1, 1)
    # Weeks off between blocks (build for a few weeks, then stop): offsets in weeks from 1 Jan.
    breaks = {6, 7, 8, 15, 16, 25, 26, 27}
    plan = {0: ("running", "Easy run", 7, 6.1), 1: ("strength_training", "Strength", None, 40),
            2: ("running", "Tempo run", 8, 5.0), 3: ("running", "Easy run", 6, 6.0),
            5: ("running", "Long run", 13, 6.2), 6: ("cycling", "Road ride", 42, 95)}
    activities, details, weather_by_id = [], {}, {}
    first_monday = since - timedelta(days=since.weekday())
    d = since
    while d <= today:
        week = (d - first_monday).days // 7
        if d.weekday() in plan and week not in breaks and rnd.random() > 0.12:
            t, name, dist, pace = plan[d.weekday()]
            block_week = week - max([b for b in breaks if b < week], default=-1)
            month = d.month
            summer = month in (6, 7, 8)
            temp = {1: 2, 2: 4, 3: 8, 4: 12, 5: 17, 6: 24, 7: 27, 8: 26, 9: 20, 10: 13, 11: 7, 12: 3}[month] + rnd.uniform(-3, 3)
            dew = temp - rnd.uniform(5, 10)
            heat = weather.heat_slowdown_pct(temp, dew)
            # Fitness improves within each block and peaks in June.
            fitness = 1 - min(block_week, 9) * 0.006 - (0.03 if month == 6 else 0) - (0.015 if month >= 9 else 0)
            start = datetime.combine(d, datetime.min.time()) + timedelta(hours=18 if summer else 7, minutes=rnd.randint(0, 60))
            a = {"id": int(d.strftime("%Y%m%d")), "name": name, "type": t, "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                 "calories": rnd.randint(250, 900)}
            if t == "running":
                km = round(dist * (1 + min(block_week, 8) * 0.06) * rnd.uniform(.9, 1.1), 2)
                p = pace * fitness * (1 + heat / 100) * rnd.uniform(.98, 1.02)
                hr = {"Easy run": 140, "Long run": 144, "Tempo run": 164}[name] + rnd.randint(-4, 4) + (3 if summer else 0)
                a.update({"distanceKm": km, "durationMin": round(km * p, 1), "avgHr": hr, "maxHr": hr + rnd.randint(8, 16),
                          "cadence": rnd.randint(164, 176), "strideCm": rnd.randint(100, 118), "gctMs": rnd.randint(235, 265),
                          "vertOscCm": round(rnd.uniform(8.2, 9.6), 1), "vertRatio": round(rnd.uniform(7.4, 8.6), 1),
                          "elevationM": rnd.randint(10, 180), "vo2max": round(47 + (6 - abs(month - 6)) * 0.4 + rnd.uniform(-.3, .3)),
                          "aerobicTE": round(rnd.uniform(2.4, 3.2) if name != "Tempo run" else rnd.uniform(3.6, 4.4), 1)})
                m = a["durationMin"]
                a["hrZonesMin"] = [round(m * x, 1) for x in ((.15, .7, .1, .05, 0) if name != "Tempo run" else (.1, .2, .2, .4, .1))]
                a["avgSpeedKmh"] = round(km / (m / 60), 2)
                weather_by_id[str(a["id"])] = {"tempC": round(temp, 1), "dewPointC": round(dew, 1),
                                               "humidity": round(rnd.uniform(45, 85)), "windKmh": round(rnd.uniform(2, 18), 1), "source": "sample"}
                if km >= DETAIL_MIN_KM and (today - d).days <= 21:
                    n = int(km)
                    laps = [{"lap": i + 1, "distanceKm": 1.0, "durationMin": round(p * (1 + (i / n) * 0.03 * (1 if summer else .5)), 2),
                             "avgHr": hr - 6 + int(i * 12 / n), "cadence": a["cadence"] + rnd.randint(-3, 3),
                             "strideCm": a["strideCm"], "gctMs": a["gctMs"], "vertOscCm": a["vertOscCm"]} for i in range(n)]
                    for lap in laps:
                        lap["pace"] = lap["durationMin"]
                    t_series = [round(x * 0.5, 2) for x in range(int(m * 2))]
                    det = {"laps": laps, "series": {
                        "t": t_series, "hr": [min(hr + 10, round(hr - 15 + 15 * min(1, x / 8) + x * 0.08)) for x in t_series],
                        "pace": [round(p * rnd.uniform(.96, 1.04), 3) for _ in t_series],
                        "cadence": [a["cadence"] + rnd.randint(-4, 4) for _ in t_series], "elev": [400 + rnd.randint(-5, 5) for _ in t_series]}}
                    det["decoupling"] = run_detail.decoupling(det)
                    a["decoupling"] = det["decoupling"]
                    details[a["id"]] = det
            else:
                a.update({"distanceKm": dist and round(dist * rnd.uniform(.8, 1.2), 2), "durationMin": round(pace * rnd.uniform(.9, 1.2), 1),
                          "avgHr": rnd.randint(118, 140)})
            activities.append(a)
        d += timedelta(days=1)
    notes = {"runs": {}, "weights": {(today - timedelta(days=i)).isoformat(): round(72.5 - i * 0.01 + rnd.uniform(-.3, .3), 1) for i in range(0, 90, 7)}}
    shoes = ["Novablast 4", "Novablast 4", "Pegasus 41"]
    for a in activities[-25:]:
        if a["type"] == "running":
            notes["runs"][str(a["id"])] = {"runType": rnd.choice(["solo", "solo", "run club", "with friends"]),
                                           "shoes": rnd.choice(shoes), "effort": rnd.randint(3, 8)}
    extras = {"vo2max": 51.4, "trainingStatus": "Productive", "profile": {"sex": "male", "age": 38},
              "racePredictions": {"5k": 22.9, "10k": 47.6, "half": 105.8, "marathon": 223.5},
              "settings": {"maxHr": 188, "restingHr": 50, "lthr": 170, "zones": [94, 113, 132, 151, 170],
                           "zonesMethod": "hr max based", "source": "sample"},
              "weights": notes["weights"]}
    payload = {"athlete": "Demo athlete", "historyFrom": since.isoformat(), "daily": daily,
               "activities": enrich(activities, weather_by_id, notes, detail_dir=None), "extras": extras}
    # Demo per-run details live next to sample.json, not in your private store.
    sample_dir = DATA_DIR / "sample-details"
    sample_dir.mkdir(parents=True, exist_ok=True)
    for old in sample_dir.glob("*.json"):
        old.unlink()
    for act_id, det in details.items():
        (sample_dir / f"{act_id}.json").write_text(json.dumps(det))
    for a in payload["activities"]:
        if a["id"] in details:
            a["detailPath"] = f"sample-details/{a['id']}.json"
    return payload


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=DAILY_DAYS, help=f"days of sleep/HRV/daily data to keep (default {DAILY_DAYS})")
    p.add_argument("--since", default=HISTORY_FROM, help=f"fetch activities back to this date (default {HISTORY_FROM})")
    p.add_argument("--quick", action="store_true",
                   help="last 12 weeks only, no per-run details or weather (used by the GitHub job)")
    p.add_argument("--sample", action="store_true", help="write fake demo data to site/data/sample.json")
    p.add_argument("--reanalyse", action="store_true",
                   help="recalculate insights and coaching from the saved data, without contacting Garmin")
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
    if args.reanalyse:
        payload = rebuild()
        print(f"Recalculated insights and coaching for {len(payload.get('activities', []))} activities. Reload the page.")
        return
    if args.sample:
        payload = finish(sample_data(DAILY_DAYS))
        payload["source"] = "sample"
        (DATA_DIR / "sample.json").write_text(json.dumps(payload, indent=1))
        print(f"Saved demo data: {len(payload['daily'])} days, {len(payload['activities'])} activities")
        return

    try:
        date.fromisoformat(args.since)
    except ValueError:
        sys.exit("--since must be a date like 2026-01-01")
    api = connect()
    today = date.today()
    if args.quick:
        args.days, args.since = min(args.days, 28), max(args.since, (today - timedelta(days=ACTIVITY_DAYS)).isoformat())
    sync_daily(api, max(7, args.days))
    acts = sync_activities(api, args.since)
    if not args.quick:
        sync_details(api, acts)
        sync_weather(acts)
    print("\nFetching settings and running metrics…")
    target = DATA_DIR / "garmin.json"
    old = json.loads(target.read_text()) if target.exists() else {}
    old.update({"athlete": safe("name", api.get_full_name) or old.get("athlete"), "extras": fetch_extras(api, today)})
    target.write_text(json.dumps(old))
    payload = rebuild(fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    runs = sum(1 for a in payload["activities"] if "running" in (a.get("type") or ""))
    print(f"Saved {len(payload['daily'])} days and {len(payload['activities'])} activities "
          f"({runs} runs since {payload['historyFrom']}) to site/data/{target.name}")


if __name__ == "__main__":
    main()
