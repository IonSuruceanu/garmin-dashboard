"""Running analysis and "what to do today" recommendations.

Everything is computed from your own data: paces come from Garmin's race
predictions (or, failing that, your recent runs), and readiness from your
HRV, resting heart rate, sleep and recent training compared with your normal.
These are rules of thumb used by most training apps, not medical advice.
"""

from datetime import date, datetime, timedelta
from statistics import median

RIEGEL = 1.06  # standard race-time scaling exponent


# ---------- helpers ----------

def _avg(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _day(a):
    return (a.get("start") or "")[:10]


def is_run(a):
    return "running" in (a.get("type") or "")


def run_pace(a):
    """Minutes per km, or None."""
    if a.get("distanceKm") and a.get("durationMin") and a["distanceKm"] >= 0.5:
        return a["durationMin"] / a["distanceKm"]
    return None


def fmt_pace(p):
    if p is None:
        return "—"
    m, s = divmod(round(p * 60), 60)
    return f"{m}:{s:02d}"


def fmt_time(minutes):
    total = round(minutes * 60)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def night_rhr(d):
    """Resting HR only counts when the watch was worn overnight; otherwise
    Garmin estimates it from daytime readings and it reads far too high."""
    return d.get("restingHr") if d.get("sleepMin") else None


def _max_hr(runs):
    vals = [a.get("maxHr") for a in runs if a.get("maxHr")]
    return max(vals) if vals else None


def is_hard(a, max_hr):
    if (a.get("aerobicTE") or 0) >= 3.5 or (a.get("anaerobicTE") or 0) >= 2.0:
        return True
    return bool(max_hr and a.get("avgHr") and a["avgHr"] >= 0.86 * max_hr)


# ---------- VO2 max ----------

# Fitness classification by age and sex (Cooper Institute norms, the table
# Garmin uses): minimum VO2 max for Superior, Excellent, Good, Fair.
VO2_NORMS = {
    "male": [(29, 55.4, 51.1, 45.4, 41.7), (39, 54.0, 48.3, 44.0, 40.5), (49, 52.5, 46.4, 42.4, 38.5),
             (59, 48.9, 43.4, 39.2, 35.6), (69, 45.7, 39.5, 35.5, 32.3), (200, 42.1, 36.7, 32.3, 29.4)],
    "female": [(29, 49.6, 43.9, 39.5, 36.1), (39, 47.4, 42.4, 37.8, 34.4), (49, 45.3, 39.7, 36.3, 33.0),
               (59, 41.1, 36.7, 33.0, 30.1), (69, 37.8, 33.0, 30.0, 27.5), (200, 36.7, 30.9, 28.1, 25.9)],
}
VO2_LEVELS = ["Superior", "Excellent", "Good", "Fair", "Poor"]


def vo2_cost(speed_m_min):
    """Oxygen cost (ml/kg/min) of running at a speed (Daniels & Gilbert)."""
    return -4.60 + 0.182258 * speed_m_min + 0.000104 * speed_m_min ** 2


def run_vo2_estimate(a, max_hr):
    """Estimate VO2 max from one steady run using heart rate: the speed's oxygen
    cost divided by the share of VO2 max that heart rate implies (Swain:
    %HRmax = 0.64 x %VO2max + 37). Rough: hills, heat and fatigue all skew it."""
    p = run_pace(a)
    if not (p and max_hr and a.get("avgHr") and (a.get("distanceKm") or 0) >= 3):
        return None
    hr_frac = a["avgHr"] / max_hr
    if not 0.65 <= hr_frac <= 0.95:
        return None
    vo2_frac = (hr_frac * 100 - 37) / 64
    return vo2_cost(1000 / p) / vo2_frac


def vo2_level(value, profile):
    sex, age = (profile or {}).get("sex"), (profile or {}).get("age")
    if not value or sex not in VO2_NORMS or not age:
        return None, None
    row = next(r for r in VO2_NORMS[sex] if age <= r[0])
    idx = next((i for i, cut in enumerate(row[1:]) if value >= cut), 4)
    nxt = None if idx == 0 else {"level": VO2_LEVELS[idx - 1], "at": row[idx]}
    return VO2_LEVELS[idx], nxt


def vo2max(runs, extras, today, max_hr):
    """Current VO2 max, its 12-week trend and fitness level for your age and sex."""
    garmin_hist = sorted(({"date": _day(a), "value": a["vo2max"]} for a in runs if a.get("vo2max")),
                         key=lambda x: x["date"])
    current, source = extras.get("vo2max"), "Garmin"
    history = garmin_hist
    if not history:
        # No Garmin values: weekly median of heart-rate based estimates.
        monday = today - timedelta(days=today.weekday())
        for k in range(11, -1, -1):
            start = monday - timedelta(weeks=k)
            ests = [e for e in (run_vo2_estimate(a, max_hr) for a in runs
                                if start.isoformat() <= _day(a) <= (start + timedelta(days=6)).isoformat()) if e]
            if ests:
                history.append({"date": start.isoformat(), "value": round(median(ests), 1)})
        if not current and history:
            current = round(median([h["value"] for h in history[-3:]]), 1)
            source = "estimated from your runs' pace and heart rate"
    if not current and history:
        current = history[-1]["value"]
    if not current:
        return None
    # Single readings are noisy, so compare the first three with the last three.
    change = (round(_avg([h["value"] for h in history[-3:]]) - _avg([h["value"] for h in history[:3]]), 1)
              if len(history) >= 6 else None)
    level, nxt = vo2_level(current, extras.get("profile"))
    return {
        "value": round(current, 1),
        "source": source,
        "history": history,
        "change": change,
        "since": history[0]["date"] if history else None,
        "level": level,
        "next": nxt,
        "profile": extras.get("profile"),
    }


def vo2_insights(v):
    if not v:
        return []
    out = []
    if v.get("change") is not None and v["since"]:
        if v["change"] >= 0.5:
            out.append(("good", f"VO2 max up {v['change']:.1f}",
                        f"Now {v['value']}, up about {v['change']:.1f} since {v['since']}. Your aerobic engine is getting bigger."))
        elif v["change"] <= -0.5:
            out.append(("watch", f"VO2 max down {abs(v['change']):.1f}",
                        f"Now {v['value']}, down about {abs(v['change']):.1f} since {v['since']}. Consistent easy running plus one quality session a week usually turns this around."))
    if v.get("next"):
        out.append(("info", f"{v['next']['at'] - v['value']:.1f} to reach '{v['next']['level']}'",
                    f"Your VO2 max of {v['value']} rates '{v['level']}' for your age and sex; '{v['next']['level']}' starts at {v['next']['at']}."))
    return out


# ---------- your heart-rate settings ----------

def hr_settings(runs, extras):
    """Max HR, easy ceiling and zones: Garmin's settings when available, so the
    dashboard, Telegram and Claude all work from the same numbers."""
    s = extras.get("settings") or {}
    observed = _max_hr(runs)
    max_hr = s.get("maxHr") or observed
    zones = s.get("zones") if s.get("zones") and len(s["zones"]) == 5 else None
    if zones:
        easy_cap, easy_source = zones[2] - 1, "top of your Garmin zone 2"
    elif max_hr:
        easy_cap = round(0.80 * max_hr)
        easy_source = "80% of your Garmin max HR" if s.get("maxHr") else "80% of your highest recorded HR"
    else:
        easy_cap, easy_source = None, None
    return {"maxHr": max_hr, "maxHrSource": "Garmin settings" if s.get("maxHr") else "highest recorded",
            "observedMaxHr": observed, "restingHr": s.get("restingHr"), "lthr": s.get("lthr"),
            "zones": zones, "zonesMethod": s.get("zonesMethod"), "easyCap": easy_cap, "easySource": easy_source}


def zone_of(hr, settings):
    """Heart-rate zone 1-5 for a heart rate, from your zones (or % of max HR)."""
    zones = settings.get("zones")
    if zones:
        return max([i + 1 for i, floor in enumerate(zones) if hr >= floor], default=1)
    m = settings.get("maxHr")
    if not m:
        return None
    return 1 + sum(hr >= m * f for f in (0.60, 0.70, 0.80, 0.90))


def load_estimate(a, settings):
    """Training load: Garmin's own when present, otherwise zone-weighted minutes
    (Edwards TRIMP: minutes in zone 1 x1, zone 2 x2 ... zone 5 x5)."""
    if a.get("trainingLoad"):
        return a["trainingLoad"], "garmin"
    if a.get("hrZonesMin"):
        return round(sum((i + 1) * m for i, m in enumerate(a["hrZonesMin"]))), "estimate"
    if a.get("avgHr") and a.get("durationMin"):
        z = zone_of(a["avgHr"], settings)
        if z:
            return round(z * a["durationMin"]), "estimate"
    return None, None


# ---------- longer history ----------

def _active_threshold(weeks):
    km = sorted(w["km"] for w in weeks if w["km"] > 0)
    return max(5.0, 0.3 * km[len(km) // 2]) if km else 5.0


def blocks(weeks):
    """Training blocks: runs of active weeks, ended by 2+ quiet weeks in a row. One
    quiet week inside a block (holiday, illness) doesn't end it."""
    thr = _active_threshold(weeks)
    out, cur, gap = [], None, []
    for i, w in enumerate(weeks):
        partial = w.get("partial")
        if w["km"] >= thr or (partial and cur and w["runs"]):
            if cur is None:
                cur = [w]
            else:
                cur += gap + [w]
            gap = []
        elif cur is not None and not partial:
            gap.append(w)
            if len(gap) >= 2:
                out.append(cur)
                cur, gap = None, []
    if cur:
        out.append(cur)
    result = []
    for b in out:
        active = [w for w in b if w["km"] >= thr or w.get("partial")]
        kms = [w["km"] for w in b]
        # Biggest week-on-week jump in the last 3 weeks of the block
        tail = b[-4:]
        jumps = [(c["km"] - p["km"]) / p["km"] * 100 for p, c in zip(tail, tail[1:]) if p["km"] >= thr and not c.get("partial")]
        result.append({
            "start": b[0]["week"], "end": b[-1]["week"], "weeks": len(b), "activeWeeks": len(active),
            "km": round(sum(kms), 1), "peakKm": max(kms), "avgKm": round(sum(kms) / len(b), 1),
            "lateJumpPct": round(max(jumps)) if jumps else None,
            "current": b[-1] is weeks[-1] or (len(weeks) > 1 and b[-1] is weeks[-2]),
        })
    return {"thresholdKm": round(thr, 1), "blocks": result}


def _riegel_5k(minutes, km):
    return minutes * (5 / km) ** RIEGEL


SOCIAL = ("run club", "with friends")


def period_stats(label, rs):
    """Best effort (as a 5K-equivalent time, raw and heat-adjusted) and running
    efficiency (distance per heartbeat, raw and heat-adjusted) for a set of runs."""
    rs = [a for a in rs if a.get("distanceKm") and a.get("durationMin") and a["distanceKm"] >= 3]
    if not rs:
        return None

    def best(adjusted):
        cands = []
        for a in rs:
            pace = a.get("heatAdjPace") if adjusted else run_pace(a)
            if pace:
                cands.append((_riegel_5k(pace * a["distanceKm"], a["distanceKm"]), a))
        return min(cands, key=lambda c: c[0]) if cands else (None, None)

    b_raw, a_raw = best(False)
    b_adj, _ = best(True)
    # Social runs are slower by choice; leave them out of efficiency.
    steady = [a for a in rs if a.get("avgHr") and (a.get("note") or {}).get("runType") not in SOCIAL]
    eff = [1000 / run_pace(a) / a["avgHr"] for a in steady]
    eff_adj = [1000 / a["heatAdjPace"] / a["avgHr"] for a in steady if a.get("heatAdjPace")]
    temps = [a["weather"]["tempC"] for a in rs if (a.get("weather") or {}).get("tempC") is not None]
    return {
        "month": label, "runs": len(rs), "km": round(sum(a["distanceKm"] for a in rs), 1),
        "best5k": round(b_raw, 2) if b_raw else None,
        "best5kAdj": round(b_adj, 2) if b_adj else None,
        "bestRun": {"date": _day(a_raw), "name": a_raw.get("name"), "distanceKm": a_raw["distanceKm"],
                    "pace": round(run_pace(a_raw), 3)} if a_raw else None,
        "efficiency": round(median(eff), 3) if eff else None,
        "efficiencyAdj": round(median(eff_adj), 3) if eff_adj else None,
        "avgTempC": round(sum(temps) / len(temps), 1) if temps else None,
    }


def monthly_fitness(runs):
    by_month = {}
    for a in runs:
        by_month.setdefault(_day(a)[:7], []).append(a)
    return [m for m in (period_stats(month, by_month[month]) for month in sorted(by_month)) if m]


def fitness_compare(runs, months, today):
    """Now (last 6 weeks) against your best month so far, heat-adjusted where possible."""
    since = (today - timedelta(days=41)).isoformat()
    now = period_stats("last 6 weeks", [a for a in runs if _day(a) >= since])
    if not now or not months:
        return None
    past = [m for m in months if m["month"] < since[:7] and (m.get("best5kAdj") or m.get("best5k"))]
    if not past:
        return None
    key = "best5kAdj" if now.get("best5kAdj") and all(m.get("best5kAdj") for m in past) else "best5k"
    peak = min(past, key=lambda m: m[key])
    eff_key = "efficiencyAdj" if now.get("efficiencyAdj") and peak.get("efficiencyAdj") else "efficiency"
    eff_change = ((now[eff_key] - peak[eff_key]) / peak[eff_key] * 100) if now.get(eff_key) and peak.get(eff_key) else None
    return {"peakMonth": peak["month"], "peak5k": peak[key], "now5k": now[key], "heatAdjusted": key == "best5kAdj",
            "changePct": round((now[key] - peak[key]) / peak[key] * 100, 1),
            "efficiencyChangePct": round(eff_change, 1) if eff_change is not None else None,
            "peakBestRun": peak.get("bestRun"), "nowBestRun": now.get("bestRun"), "nowRuns": now["runs"]}


def drift_summary(runs, today):
    """Heart-rate drift on long, steady runs over the last 8 weeks."""
    since = (today - timedelta(days=55)).isoformat()
    longs = [a for a in runs if _day(a) >= since and a.get("decoupling") is not None
             and ((a.get("durationMin") or 0) >= 60 or (a.get("distanceKm") or 0) >= 12)]
    if not longs:
        return None
    vals = [a["decoupling"] for a in longs]
    return {"runs": [{"date": _day(a), "name": a.get("name"), "distanceKm": a.get("distanceKm"),
                      "decoupling": a["decoupling"], "tempC": (a.get("weather") or {}).get("tempC")} for a in longs],
            "avg": round(sum(vals) / len(vals), 1)}


def dynamics(runs, today):
    """Running form averages for the last 4 weeks and the 4 before."""
    def window(lo, hi):
        rs = [a for a in runs if lo <= _day(a) < hi]
        out = {k: _avg([a.get(k) for a in rs]) for k in ("cadence", "strideCm", "gctMs", "vertOscCm", "vertRatio")}
        return {k: round(v, 1) for k, v in out.items() if v is not None}
    d0, d1, d2 = ((today - timedelta(days=n)).isoformat() for n in (0, 27, 55))
    now, before = window(d1, "9999"), window(d2, d1)
    return {"now": now, "before": before} if now else None


def shoes(activities):
    """Kilometres per shoe, from the shoes you've noted on runs."""
    totals = {}
    for a in activities:
        name = (a.get("note") or {}).get("shoes")
        if name and a.get("distanceKm"):
            t = totals.setdefault(name, {"name": name, "km": 0.0, "runs": 0, "lastUsed": ""})
            t["km"] += a["distanceKm"]
            t["runs"] += 1
            t["lastUsed"] = max(t["lastUsed"], _day(a))
    return sorted(({**t, "km": round(t["km"], 1)} for t in totals.values()), key=lambda t: -t["km"])


def weight_trend(weights):
    if not weights:
        return None
    days = sorted(weights)
    latest = days[-1]
    month_ago = [d for d in days if d <= (date.fromisoformat(latest) - timedelta(days=28)).isoformat()]
    return {"latest": weights[latest], "date": latest,
            "change28d": round(weights[latest] - weights[month_ago[-1]], 1) if month_ago else None}


# ---------- running analysis ----------

def paces(runs, extras, today, hr=None):
    """Race predictions (min) and training paces (min/km)."""
    pred = dict(extras.get("racePredictions") or {})
    source = "Garmin race predictor"
    if not pred.get("10k"):
        # Estimate from the best recent effort with Riegel's formula.
        recent = [a for a in runs if _day(a) >= (today - timedelta(days=42)).isoformat()
                  and (a.get("distanceKm") or 0) >= 3 and a.get("durationMin")]
        if not recent:
            return None
        best = min(recent, key=lambda a: a["durationMin"] * (10 / a["distanceKm"]) ** RIEGEL)
        t10 = best["durationMin"] * (10 / best["distanceKm"]) ** RIEGEL
        pred = {"5k": t10 * 0.5 ** RIEGEL, "10k": t10, "half": t10 * 2.10975 ** RIEGEL, "marathon": t10 * 4.2195 ** RIEGEL}
        source = "estimated from your best recent run"
    p5 = pred["5k"] / 5 if pred.get("5k") else pred["10k"] / 10 * 0.96
    p10 = pred["10k"] / 10
    phm = pred["half"] / 21.0975 if pred.get("half") else p10 * 1.045

    hr = hr or hr_settings(runs, extras)
    cap = hr["easyCap"]
    easy_runs = [run_pace(a) for a in runs if cap and a.get("avgHr") and a["avgHr"] <= cap and run_pace(a)
                 and _day(a) >= (today - timedelta(days=83)).isoformat()]
    easy_mid = median(easy_runs) if len(easy_runs) >= 3 else p10 * 1.27
    return {
        "source": source,
        "predictions": {k: round(v, 1) for k, v in pred.items() if v},
        "easy": [round(easy_mid - 0.15, 2), round(easy_mid + 0.35, 2)],
        "threshold": round((p10 + phm) / 2, 2),
        "interval": round(p5, 2),
        "easyHrMax": cap,
        "easyHrSource": hr["easySource"],
        "maxHr": hr["maxHr"],
    }


def weekly(runs, today, weeks=12, settings=None):
    monday = today - timedelta(days=today.weekday())
    out = []
    for k in range(weeks - 1, -1, -1):
        start = monday - timedelta(weeks=k)
        end = start + timedelta(days=6)
        wk = [a for a in runs if start.isoformat() <= _day(a) <= end.isoformat()]
        out.append({
            "week": start.isoformat(),
            "km": round(sum(a.get("distanceKm") or 0 for a in wk), 1),
            "runs": len(wk),
            "longestKm": round(max((a.get("distanceKm") or 0 for a in wk), default=0), 1),
            "load": sum(load_estimate(a, settings or {})[0] or 0 for a in wk) or None,
            "partial": k == 0,
        })
    return out


def efficiency(runs):
    """Metres per heartbeat for steady runs: rises as aerobic fitness improves."""
    pts = []
    for a in runs:
        p = run_pace(a)
        if p and a.get("avgHr") and (a.get("distanceKm") or 0) >= 3:
            pts.append({"date": _day(a), "value": round(1000 / p / a["avgHr"], 3)})
    return sorted(pts, key=lambda x: x["date"])


def intensity(runs, settings, today, days=28):
    """Share of running time that was easy / moderate / hard over the last 4 weeks."""
    since = (today - timedelta(days=days - 1)).isoformat()
    easy = moderate = hard = 0.0
    for a in runs:
        if _day(a) < since:
            continue
        z = a.get("hrZonesMin")
        if z and len(z) == 5 and sum(z):
            easy += z[0] + z[1]; moderate += z[2]; hard += z[3] + z[4]
        elif a.get("avgHr") and a.get("durationMin") and zone_of(a["avgHr"], settings):
            z = zone_of(a["avgHr"], settings)
            bucket = "easy" if z <= 2 else "moderate" if z == 3 else "hard"
            if bucket == "easy": easy += a["durationMin"]
            elif bucket == "moderate": moderate += a["durationMin"]
            else: hard += a["durationMin"]
    total = easy + moderate + hard
    if not total:
        return None
    return {"easy": round(easy / total * 100), "moderate": round(moderate / total * 100),
            "hard": round(hard / total * 100), "minutes": round(total)}


def running(data, today):
    acts = data.get("activities") or []
    runs = [a for a in acts if is_run(a)]
    extras = data.get("extras") or {}
    hr = hr_settings(runs, extras)
    max_hr = hr["maxHr"]
    since = date.fromisoformat(data.get("historyFrom") or (today - timedelta(days=83)).isoformat())
    n_weeks = max(12, min(60, (today - since).days // 7 + 1))
    all_weeks = weekly(runs, today, n_weeks, hr)
    weeks = all_weeks[-12:]
    full = [w["km"] for w in weeks[-5:-1]]
    months = monthly_fitness(runs)
    last14 = [a for a in runs if _day(a) >= (today - timedelta(days=13)).isoformat()]
    cad = _avg([a.get("cadence") for a in runs if _day(a) >= (today - timedelta(days=27)).isoformat()])
    # A "long run" is one close to your longest of the last 4 weeks (and at least 10 km).
    month = [a for a in runs if _day(a) >= (today - timedelta(days=27)).isoformat()]
    long_cut = max(10, 0.8 * max((a.get("distanceKm") or 0 for a in month), default=0))
    long_days = [_day(a) for a in month if (a.get("distanceKm") or 0) >= long_cut]
    days_since_long = (today - date.fromisoformat(max(long_days))).days if long_days else 99
    return {
        "thisWeekKm": weeks[-1]["km"],
        "avgWeekKm": round(sum(full) / len(full), 1) if full else 0,
        "runsThisWeek": weeks[-1]["runs"],
        "longestRecentKm": round(max((a.get("distanceKm") or 0 for a in last14), default=0), 1),
        "cadence": round(cad) if cad else None,
        "daysSinceLong": days_since_long,
        "vo2max": extras.get("vo2max"),
        "vo2": vo2max(runs, extras, today, max_hr),
        "trainingStatus": extras.get("trainingStatus"),
        "weekly": weeks,
        "history": {"from": since.isoformat(), "weekly": all_weeks, **blocks(all_weeks)},
        "monthly": months,
        "fitnessCompare": fitness_compare(runs, months, today),
        "drift": drift_summary(runs, today),
        "dynamics": dynamics(runs, today),
        "hr": hr,
        "garminLoad": extras.get("garminLoad"),
        "shoes": shoes(acts),
        "weight": weight_trend(extras.get("weights")),
        "efficiency": [p for p in efficiency(runs) if p["date"] >= (today - timedelta(days=83)).isoformat()],
        "intensity": intensity(runs, hr, today),
        "paces": paces(runs, extras, today, hr),
    }


def running_insights(r):
    out = []
    if not r["weekly"] or not any(w["runs"] for w in r["weekly"]):
        return out
    wk, avg4 = r["thisWeekKm"], r["avgWeekKm"]
    if avg4:
        out.append(("info", f"{wk:.1f} km run this week",
                    f"Your last 4 weeks averaged {avg4:.1f} km. A sensible target for a full week is about "
                    f"{avg4 * 1.1:.0f} km (no more than ~10% above your average)."))
    it = r["intensity"]
    if it:
        if it["easy"] >= 75:
            out.append(("good", f"{it['easy']}% of your running is easy",
                        "That's the 80/20 balance most coaches recommend: lots of easy running builds the base, the hard 20% builds speed."))
        elif it["moderate"] >= 30:
            out.append(("watch", "Too much running at medium effort",
                        f"{it['moderate']}% of the last 4 weeks was moderate (the 'grey zone'): too hard to recover from, too easy to build speed. "
                        f"Slow your easy runs down" + (f" (heart rate under {r['paces']['easyHrMax']} bpm)" if r["paces"] and r["paces"].get("easyHrMax") else "") +
                        " and make hard days properly hard."))
        else:
            out.append(("watch", f"Only {it['easy']}% of your running is easy",
                        "Aim for about 80% easy. More easy running lets you recover and makes the hard sessions count."))
    eff = r["efficiency"]
    if len(eff) >= 8:
        recent = _avg([p["value"] for p in eff[-4:]])
        before = _avg([p["value"] for p in eff[:-4]])
        change = (recent - before) / before * 100 if before else 0
        if change >= 2:
            out.append(("good", "Aerobic fitness is improving",
                        f"Your recent runs cover {change:.0f}% more distance per heartbeat than earlier ones: you're running faster at the same effort."))
        elif change <= -3:
            out.append(("watch", "Running feels harder than usual",
                        f"Your recent runs cover {abs(change):.0f}% less distance per heartbeat. Heat, fatigue, illness or hills can all cause this. Watch your recovery."))
    out += vo2_insights(r.get("vo2"))
    out += history_insights(r)
    if r["longestRecentKm"] and wk and r["longestRecentKm"] > 0.45 * max(wk, avg4 or 0) and avg4 >= 15:
        out.append(("watch", "Your long run is a big share of your week",
                    f"Longest run in the last 2 weeks was {r['longestRecentKm']} km. Keeping your long run under about 35–40% of your weekly distance lowers injury risk."))
    return out


def history_insights(r):
    out = []
    h = r.get("history") or {}
    bl = h.get("blocks") or []
    ended = [b for b in bl if not b["current"]]
    cur = next((b for b in bl if b["current"]), None)
    if cur and ended:
        lengths = [b["weeks"] for b in ended]
        avg_len = sum(lengths) / len(lengths)
        detail = (f"This block started the week of {cur['start']} and is in week {cur['weeks']}. "
                  f"Since {h.get('from')}, your earlier blocks lasted " + ", ".join(str(n) for n in lengths) + " weeks "
                  f"(average {avg_len:.0f}), each followed by 2+ quiet weeks.")
        jumps = [b for b in ended if (b.get("lateJumpPct") or 0) >= 30]
        if jumps:
            detail += (f" {len(jumps)} of {len(ended)} ended within a few weeks of a jump of 30%+ in weekly distance; "
                       "keeping increases around 10% a week may help this one last.")
        level = "good" if cur["weeks"] > max(lengths) else "watch" if cur["weeks"] >= avg_len - 1 else "info"
        title = ("Your longest training block this year" if level == "good"
                 else "You're near the point where past blocks stopped" if level == "watch"
                 else f"Week {cur['weeks']} of this training block")
        out.append((level, title, detail))
    elif not cur and ended:
        last = ended[-1]
        out.append(("info", "Between training blocks",
                    f"Your last block ran {last['weeks']} weeks, ending the week of {last['end']}. "
                    "Restarting with 2-3 easy weeks before adding volume tends to stick better."))

    fc = r.get("fitnessCompare")
    if fc:
        month = datetime.strptime(fc["peakMonth"], "%Y-%m").strftime("%B")
        heat = "heat-adjusted " if fc["heatAdjusted"] else ""
        pct = fc["changePct"]
        eff = ""
        if fc.get("efficiencyChangePct") is not None:
            e = fc["efficiencyChangePct"]
            eff = f" At the same heart rate you cover {abs(e):.0f}% {'more' if e >= 0 else 'less'} distance than in {month}."
        cmp = "faster than" if pct < -1 else "slower than" if pct > 1 else "about level with"
        out.append(("good" if pct <= 1 else "info", f"Fitness vs {month}: {cmp}",
                    f"Best {heat}5K-equivalent effort in the last 6 weeks: {fmt_time(fc['now5k'])}, vs {fmt_time(fc['peak5k'])} "
                    f"in {month} ({pct:+.1f}%).{eff} This compares your best efforts, so it's only fair if you've run "
                    "something hard recently."))

    d = r.get("drift")
    if d and len(d["runs"]) >= 2:
        if d["avg"] <= 5:
            out.append(("good", "Little heart-rate drift on long runs",
                        f"Average drift {d['avg']}% over {len(d['runs'])} long runs: your aerobic base holds up for the distance."))
        else:
            out.append(("watch", "Heart rate drifts on your long runs",
                        f"Average drift {d['avg']}% over {len(d['runs'])} long runs (under 5% is the usual target). "
                        "Start slower, fuel and drink on runs over an hour, and allow for heat."))

    hot = [m for m in r.get("monthly") or [] if (m.get("avgTempC") or 0) >= 20]
    if hot and r.get("fitnessCompare") and r["fitnessCompare"]["heatAdjusted"]:
        out.append(("info", "Paces are heat-adjusted",
                    "Warm months (" + ", ".join(datetime.strptime(m["month"], "%Y-%m").strftime("%b") for m in hot) +
                    ") are corrected using temperature and dew point at the time of each run, so they compare fairly with cool ones."))

    for shoe in r.get("shoes") or []:
        if shoe["km"] >= 600:
            out.append(("watch", f"{shoe['name']}: {shoe['km']:.0f} km",
                        "Most running shoes lose cushioning around 600-800 km. Consider a replacement pair soon."))
    w = r.get("weight")
    if w and w.get("change28d") is not None and abs(w["change28d"]) >= 1.5:
        out.append(("info", f"Weight {'up' if w['change28d'] > 0 else 'down'} {abs(w['change28d']):.1f} kg in 4 weeks",
                    f"Latest {w['latest']} kg. Weight changes also shift pace and heart rate a little."))
    return out


# ---------- readiness & today's options ----------

def readiness(data, today, max_hr):
    daily = data.get("daily") or []
    acts = data.get("activities") or []
    extras = data.get("extras") or {}
    last = daily[-1] if daily else {}
    base = daily[:-1]
    factors = []
    score = 70.0

    hrv_base = _avg([d.get("hrv") for d in base[-21:]])
    if last.get("hrv") and hrv_base:
        pct = (last["hrv"] - hrv_base) / hrv_base * 100
        score += _clamp(pct * 1.2, -15, 10)
        factors.append(("+" if pct >= 3 else "-" if pct <= -5 else "=",
                        f"HRV {last['hrv']} ms, {abs(pct):.0f}% {'above' if pct >= 0 else 'below'} your normal"))
    rhr_base = _avg([night_rhr(d) for d in base[-21:]])
    if night_rhr(last) and rhr_base:
        diff = last["restingHr"] - rhr_base
        score -= _clamp(diff * 3, -6, 15)
        factors.append(("-" if diff >= 2 else "+" if diff <= -1 else "=",
                        f"Resting HR {last['restingHr']} bpm ({diff:+.0f} vs normal)"))
    if last.get("sleepMin"):
        h = last["sleepMin"] / 60
        score -= _clamp((7 - h) * 7, -5, 18)
        factors.append(("+" if h >= 7.5 else "-" if h < 6.5 else "=", f"Slept {int(h)}h {round(last['sleepMin'] % 60):02d}m"))
    if last.get("bodyBatteryHigh"):
        bb = last["bodyBatteryHigh"]
        score += 5 if bb >= 80 else -10 if bb < 50 else 0
        factors.append(("+" if bb >= 80 else "-" if bb < 50 else "=", f"Body Battery {bb}"))

    yesterday = (today - timedelta(days=1)).isoformat()
    two_ago = (today - timedelta(days=2)).isoformat()
    hard_days = sorted({_day(a) for a in acts if is_hard(a, max_hr)}, reverse=True)
    if yesterday in hard_days:
        score -= 10
        factors.append(("-", "Hard session yesterday"))
    elif two_ago in hard_days:
        score -= 5
        factors.append(("=", "Hard session 2 days ago"))

    def load(start, end):
        return sum(a.get("durationMin") or 0 for a in acts if start <= _day(a) <= end)
    acute = load((today - timedelta(days=6)).isoformat(), today.isoformat())
    chronic = load((today - timedelta(days=27)).isoformat(), today.isoformat()) / 4
    if chronic:
        ratio = acute / chronic
        if ratio > 1.3:
            score -= 10
            factors.append(("-", f"Training this week is {(ratio - 1) * 100:.0f}% above your usual"))
        elif ratio < 0.8:
            score += 5
            factors.append(("+", "Lighter week than usual, so you're fresh"))

    if not last.get("sleepMin") and not last.get("hrv"):
        factors.append(("=", "No overnight data from your watch: wear it to bed for a fuller picture"))

    garmin = extras.get("readiness") or {}
    source = "your HRV, resting HR, sleep and recent training"
    if garmin.get("score") is not None:
        score = garmin["score"]
        source = "Garmin training readiness"
    score = round(_clamp(score, 5, 100))
    level = "push" if score >= 75 else "steady" if score >= 50 else "recover"
    label = {"push": "Ready to push", "steady": "Good for steady training", "recover": "Take it easy"}[level]
    days_since_hard = (today - date.fromisoformat(hard_days[0])).days if hard_days else 99
    return {"score": score, "level": level, "label": label, "source": source,
            "factors": [{"effect": e, "text": t} for e, t in factors], "daysSinceHard": days_since_hard}


def _range(p, lo, hi):
    return f"{fmt_pace(p * lo)}–{fmt_pace(p * hi)} /km"


def options(r, ready, today):
    """All workouts you could do today, each marked recommended / good / not today."""
    p = r["paces"]
    runs_known = p is not None
    easy = f"{fmt_pace(p['easy'][0])}–{fmt_pace(p['easy'][1])} /km" if runs_known else "conversational effort"
    hr_cap = f", heart rate under {p['easyHrMax']} bpm" if runs_known and p.get("easyHrMax") else ""
    thr = _range(p["threshold"], 0.99, 1.01) if runs_known else "comfortably hard, 7/10 effort"
    intv = _range(p["interval"], 0.98, 1.01) if runs_known else "hard, 8–9/10 effort"
    avg_wk = r["avgWeekKm"] or 20
    # About a third of a normal week; don't jump past your recent longest run.
    longest = r["longestRecentKm"]
    long_km = round(_clamp(min(longest * 1.05, max(avg_wk * 0.33, longest * 0.9)) if longest else avg_wk * 0.33, 8, 32))

    # Long run is due after ~a week without one; prefer weekends unless it's overdue.
    long_due = (r["daysSinceLong"] >= 6 and (today.weekday() >= 5 or r["daysSinceLong"] >= 9)
                and ready["daysSinceHard"] >= 2 and runs_known)

    o = {
        "rest": {"title": "Rest or mobility", "duration": "20–30 min",
                 "summary": "No running. A walk, stretching or foam rolling.",
                 "steps": ["10 min easy walk", "10–15 min mobility: hips, calves, hamstrings", "Optional: foam roll legs"]},
        "recovery": {"title": "Recovery run", "duration": "25–35 min",
                     "summary": f"Very easy, slower than your easy pace{hr_cap}." if runs_known else "Very easy jog, slower than feels natural.",
                     "steps": [f"25–35 min very easy (around {fmt_pace(p['easy'][1])} /km or slower)" if runs_known else "25–35 min very easy",
                               "Walk breaks are fine"]},
        "easy": {"title": "Easy run", "duration": "40–60 min",
                 "summary": f"Relaxed, conversational pace: {easy}{hr_cap}." if runs_known else "Relaxed pace where you could hold a conversation.",
                 "steps": [f"40–60 min at {easy}{hr_cap}", "Optional: 4–6 × 20 s relaxed strides at the end"]},
        "long": {"title": "Long run", "duration": f"about {long_km} km",
                 "summary": f"Builds endurance. Steady and easy: {easy}." if runs_known else "Builds endurance. Steady and easy the whole way.",
                 "steps": [f"{long_km} km at {easy}{hr_cap}", "Take water / fuel if over 75 min", "Keep the last 2 km relaxed, not a race"]},
        "tempo": {"title": "Tempo run", "duration": "45–55 min",
                  "summary": f"Threshold pace, comfortably hard: {thr}." if runs_known else "Comfortably hard: you could say a few words, not sentences.",
                  "steps": [f"15 min warm-up at {easy}", f"3 × 8 min at {thr}, 2 min easy jog between", "10 min cool-down easy"]},
        "intervals": {"title": "Intervals", "duration": "45–50 min",
                      "summary": f"Short, fast repeats at 5K pace: {intv}." if runs_known else "Short, fast repeats at about your 5K race effort.",
                      "steps": [f"15 min warm-up at {easy} + 4 strides", f"6 × 800 m at {intv}, 2 min easy jog between", "10 min cool-down easy"]},
        "strength": {"title": "Strength for runners", "duration": "30–40 min",
                     "summary": "Legs and core. Makes you more injury-resistant.",
                     "steps": ["3 rounds: 10 squats, 10 lunges per leg, 12 calf raises per leg",
                               "3 rounds: 30 s plank, 30 s side plank each side, 10 glute bridges", "Finish with 5 min mobility"]},
    }

    level, since_hard = ready["level"], ready["daysSinceHard"]
    if since_hard < 2 and r["daysSinceLong"] >= 6:
        o["long"]["summary"] += " Better tomorrow or the day after, once you've recovered from your last hard session."
    status = {k: ("good", "") for k in o}
    if level == "recover":
        rec = "rest" if ready["score"] < 25 else "recovery"
        for k in ("tempo", "intervals", "long"):
            status[k] = ("no", "Your body is still recovering; hard or long efforts now add fatigue without much benefit.")
    elif level == "steady":
        rec = "long" if long_due else "easy"
        status["intervals"] = ("no", "Save very hard efforts for a day when you're fully recovered.")
        if since_hard < 2:
            status["tempo"] = ("no", "You had a hard session recently; give it at least 48 hours.")
    else:
        if since_hard < 2:
            rec = "easy"
            for k in ("tempo", "intervals"):
                status[k] = ("no", "You had a hard session recently; give it at least 48 hours.")
        elif long_due:
            rec = "long"
        else:
            rec = "intervals" if since_hard >= 3 else "tempo"
    status[rec] = ("recommended", "")

    out = []
    for key in ("rest", "recovery", "easy", "long", "tempo", "intervals", "strength"):
        s, why = status[key]
        out.append({"key": key, **o[key], "status": s, "why": why})
    out.sort(key=lambda x: {"recommended": 0, "good": 1, "no": 2}[x["status"]])
    return out


def build(data):
    daily = data.get("daily") or []
    today = date.fromisoformat(daily[-1]["date"]) if daily else date.today()
    r = running(data, today)
    max_hr = r["hr"]["maxHr"]
    ready = readiness(data, today, max_hr)
    return {"running": r, "today": {**ready, "date": today.isoformat(), "options": options(r, ready, today)}}
