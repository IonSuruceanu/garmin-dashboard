"""Plain-language insights from the data fetch_garmin.py collects.

Each insight compares your recent days with your own baseline (the rest of the
fetched period), so they adapt to you rather than to population norms. They
are rules of thumb, not medical advice.
"""

from datetime import date, timedelta

GOOD, WATCH, INFO = "good", "watch", "info"


def _avg(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _col(rows, key):
    return [r.get(key) for r in rows]


def _hm(minutes):
    return f"{int(minutes // 60)}h {int(round(minutes % 60)):02d}m"


def _pct(new, base):
    return (new - base) / base * 100 if base else 0


def recovery(daily):
    """Resting HR and HRV: the two clearest signals of how recovered you are."""
    out = []
    recent, base = daily[-3:], daily[:-3]
    rhr_now, rhr_base = _avg(_col(recent, "restingHr")), _avg(_col(base, "restingHr"))
    if rhr_now is not None and rhr_base is not None and len(base) >= 7:
        diff = rhr_now - rhr_base
        if diff >= 3:
            out.append((WATCH, "Resting heart rate is up",
                        f"Last 3 days average {rhr_now:.0f} bpm, {diff:.0f} above your usual {rhr_base:.0f}. "
                        "That often means fatigue, stress, illness coming on, alcohol or poor sleep. Consider an easier day."))
        elif diff <= -2:
            out.append((GOOD, "Resting heart rate is down",
                        f"{rhr_now:.0f} bpm over the last 3 days vs your usual {rhr_base:.0f}: a sign of good recovery or improving fitness."))

    week, base = daily[-7:], daily[:-7]
    hrv_now, hrv_base = _avg(_col(week, "hrv")), _avg(_col(base, "hrv"))
    if hrv_now and hrv_base and len([v for v in _col(base, "hrv") if v]) >= 5:
        change = _pct(hrv_now, hrv_base)
        if change <= -7:
            out.append((WATCH, "HRV is trending down",
                        f"7-day average {hrv_now:.0f} ms, {abs(change):.0f}% below your baseline of {hrv_base:.0f} ms. "
                        "Your body is under more load than usual. Prioritise sleep and keep intensity moderate."))
        elif change >= 5:
            out.append((GOOD, "HRV is trending up",
                        f"7-day average {hrv_now:.0f} ms, {change:.0f}% above your baseline of {hrv_base:.0f} ms. You're absorbing training well."))
        else:
            out.append((INFO, "HRV is stable", f"7-day average {hrv_now:.0f} ms, in line with your baseline of {hrv_base:.0f} ms."))
    return out


def sleep(daily, target_min=450):
    week = [r for r in daily[-7:] if r.get("sleepMin")]
    if len(week) < 4:
        return []
    mean = _avg(_col(week, "sleepMin"))
    short = [r for r in week if r["sleepMin"] < 360]
    debt = sum(max(0, target_min - r["sleepMin"]) for r in week)
    score = _avg(_col(week, "sleepScore"))
    score_txt = f", average score {score:.0f}" if score else ""
    if mean < target_min - 30 or len(short) >= 2:
        return [(WATCH, "You're short on sleep",
                 f"Averaging {_hm(mean)} a night this week{score_txt}; {len(short)} night(s) under 6 hours. "
                 f"That's about {_hm(debt)} below a 7h30 target. An earlier night or two would help recovery.")]
    if mean >= target_min:
        return [(GOOD, "Sleep is solid", f"Averaging {_hm(mean)} a night this week{score_txt}.")]
    return [(INFO, "Sleep is OK", f"Averaging {_hm(mean)} a night this week{score_txt}, a little under a 7h30 target.")]


def training_load(daily, activities, today):
    """This week's training time vs the weekly average of the 3 weeks before."""
    def minutes_between(start, end):
        return sum(a.get("durationMin") or 0 for a in activities
                   if start.isoformat() <= (a.get("start") or "")[:10] <= end.isoformat())

    week_start = today - timedelta(days=6)
    this_week = minutes_between(week_start, today)
    earlier = [minutes_between(week_start - timedelta(days=7 * k), today - timedelta(days=7 * k)) for k in (1, 2, 3)]
    if not any(earlier):
        return []
    usual = sum(earlier) / 3
    active_days = len({(a.get("start") or "")[:10] for a in activities if (a.get("start") or "")[:10] >= week_start.isoformat()})
    summary = f"{_hm(this_week)} of training across {active_days} day(s) in the last 7 days, vs an average of {_hm(usual)} a week over the 3 weeks before."
    ratio = this_week / usual if usual else 0
    if ratio > 1.3:
        return [(WATCH, "Training load is ramping up fast",
                 summary + f" That's {(ratio - 1) * 100:.0f}% more. Big jumps raise injury risk; build up by about 10% a week.")]
    if ratio < 0.7:
        return [(INFO, "Lighter training week", summary + " Fine as a recovery week; otherwise it's a dip in consistency.")]
    return [(GOOD, "Training load is steady", summary)]


def daily_life(daily):
    out = []
    week, prev = daily[-7:], daily[-14:-7]
    stress_now, stress_prev = _avg(_col(week, "stressAvg")), _avg(_col(prev, "stressAvg"))
    if stress_now is not None and stress_prev is not None and stress_now - stress_prev >= 5:
        out.append((WATCH, "Stress is higher this week",
                    f"Average stress {stress_now:.0f} vs {stress_prev:.0f} the week before. High all-day stress eats into recovery too."))
    steps_now, steps_prev = _avg(_col(week, "steps")), _avg(_col(prev, "steps"))
    if steps_now and steps_prev:
        change = _pct(steps_now, steps_prev)
        if abs(change) >= 15:
            level = GOOD if change > 0 else INFO
            word = "up" if change > 0 else "down"
            out.append((level, f"Daily steps {word} {abs(change):.0f}%",
                        f"{steps_now:,.0f} a day this week vs {steps_prev:,.0f} the week before."))
    return out


def hard_days_and_hrv(daily, activities):
    """Does a long/hard session show up in the next night's HRV?"""
    hard = {(a.get("start") or "")[:10] for a in activities if (a.get("durationMin") or 0) >= 60}
    after_hard, after_other = [], []
    for prev, night in zip(daily, daily[1:]):
        if night.get("hrv") is None:
            continue
        (after_hard if prev["date"] in hard else after_other).append(night["hrv"])
    if len(after_hard) < 3 or len(after_other) < 5:
        return []
    diff = _avg(after_hard) - _avg(after_other)
    if diff <= -4:
        return [(INFO, "Long sessions show up in your HRV",
                 f"After sessions of an hour or more, your HRV the next night averages {abs(diff):.0f} ms lower "
                 f"({_avg(after_hard):.0f} vs {_avg(after_other):.0f} ms). Plan easier days after them.")]
    return []


ORDER = {WATCH: 0, GOOD: 1, INFO: 2}


def build(data):
    daily = data.get("daily") or []
    if len(daily) < 7:
        return [{"level": INFO, "title": "Not enough data yet",
                 "detail": "Insights appear once there are at least 7 days of data. Fetch with --days 30."}]
    acts = data.get("activities") or []
    today = date.fromisoformat(daily[-1]["date"])
    found = (recovery(daily) + sleep(daily) + training_load(daily, acts, today)
             + daily_life(daily) + hard_days_and_hrv(daily, acts))
    found.sort(key=lambda i: ORDER[i[0]])
    return [{"level": level, "title": title, "detail": detail} for level, title, detail in found]
