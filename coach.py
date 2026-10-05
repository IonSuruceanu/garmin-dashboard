"""Running analysis and "what to do today" recommendations.

Everything is computed from your own data: paces come from Garmin's race
predictions (or, failing that, your recent runs), and readiness from your
HRV, resting heart rate, sleep and recent training compared with your normal.
These are rules of thumb used by most training apps, not medical advice.
"""

from datetime import date, timedelta
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


# ---------- running analysis ----------

def paces(runs, extras, today):
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

    max_hr = _max_hr(runs)
    easy_runs = [run_pace(a) for a in runs if max_hr and a.get("avgHr") and a["avgHr"] <= 0.80 * max_hr and run_pace(a)]
    easy_mid = median(easy_runs) if len(easy_runs) >= 3 else p10 * 1.27
    return {
        "source": source,
        "predictions": {k: round(v, 1) for k, v in pred.items() if v},
        "easy": [round(easy_mid - 0.15, 2), round(easy_mid + 0.35, 2)],
        "threshold": round((p10 + phm) / 2, 2),
        "interval": round(p5, 2),
        "easyHrMax": round(0.80 * max_hr) if max_hr else None,
        "maxHr": max_hr,
    }


def weekly(runs, today, weeks=12):
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


def intensity(runs, max_hr, today, days=28):
    """Share of running time that was easy / moderate / hard over the last 4 weeks."""
    since = (today - timedelta(days=days - 1)).isoformat()
    easy = moderate = hard = 0.0
    for a in runs:
        if _day(a) < since:
            continue
        z = a.get("hrZonesMin")
        if z and len(z) == 5 and sum(z):
            easy += z[0] + z[1]; moderate += z[2]; hard += z[3] + z[4]
        elif max_hr and a.get("avgHr") and a.get("durationMin"):
            r = a["avgHr"] / max_hr
            bucket = "easy" if r < 0.80 else "moderate" if r < 0.87 else "hard"
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
    max_hr = _max_hr(runs)
    weeks = weekly(runs, today)
    full = [w["km"] for w in weeks[-5:-1]]
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
        "trainingStatus": extras.get("trainingStatus"),
        "weekly": weeks,
        "efficiency": efficiency(runs),
        "intensity": intensity(runs, max_hr, today),
        "paces": paces(runs, extras, today),
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
    if r["longestRecentKm"] and wk and r["longestRecentKm"] > 0.45 * max(wk, avg4 or 0) and avg4 >= 15:
        out.append(("watch", "Your long run is a big share of your week",
                    f"Longest run in the last 2 weeks was {r['longestRecentKm']} km. Keeping your long run under about 35–40% of your weekly distance lowers injury risk."))
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
    max_hr = (r["paces"] or {}).get("maxHr")
    ready = readiness(data, today, max_hr)
    return {"running": r, "today": {**ready, "date": today.isoformat(), "options": options(r, ready, today)}}
