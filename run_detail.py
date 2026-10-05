"""Per-run detail: laps/km splits, and heart rate, pace and cadence over time.

Parsed from Garmin's activity splits and details responses into a compact form,
plus aerobic decoupling (heart-rate drift): how much your pace-per-heartbeat
dropped from the first half of the run to the second.
"""


def _cadence(v):
    # Garmin reports cadence per foot in some places (~85) and per minute in others (~170).
    if not v:
        return None
    return round(v * 2) if v < 120 else round(v)


def laps(raw):
    out = []
    for i, lap in enumerate((raw or {}).get("lapDTOs") or [], 1):
        dist = (lap.get("distance") or 0) / 1000
        dur = (lap.get("movingDuration") or lap.get("duration") or 0) / 60
        out.append({
            "lap": i,
            "distanceKm": round(dist, 2),
            "durationMin": round(dur, 2),
            "pace": round(dur / dist, 3) if dist >= 0.05 else None,
            "avgHr": round(lap["averageHR"]) if lap.get("averageHR") else None,
            "maxHr": round(lap["maxHR"]) if lap.get("maxHR") else None,
            "cadence": _cadence(lap.get("averageRunCadence")),
            "elevGainM": round(lap["elevationGain"]) if lap.get("elevationGain") else None,
            "strideCm": round(lap["strideLength"]) if lap.get("strideLength") else None,
            "gctMs": round(lap["groundContactTime"]) if lap.get("groundContactTime") else None,
            "vertOscCm": round(lap["verticalOscillation"], 1) if lap.get("verticalOscillation") else None,
        })
    return out


def series(raw, every_s=30):
    """Time series sampled every `every_s` seconds: minutes, HR, pace (min/km), cadence, elevation."""
    raw = raw or {}
    idx = {d.get("key"): d.get("metricsIndex") for d in raw.get("metricDescriptors") or []}
    t_key = next((k for k in ("sumDuration", "sumElapsedDuration", "sumMovingDuration") if k in idx), None)
    if t_key is None:
        return None

    def get(row, key):
        i = idx.get(key)
        return row[i] if i is not None and i < len(row) else None

    out = {"t": [], "hr": [], "pace": [], "cadence": [], "elev": []}
    next_t = 0.0
    for entry in raw.get("activityDetailMetrics") or []:
        row = entry.get("metrics") or []
        t = get(row, t_key)
        if t is None or t < next_t:
            continue
        next_t = t + every_s
        speed = get(row, "directSpeed")
        cad = get(row, "directDoubleCadence") or get(row, "directRunCadence")
        out["t"].append(round(t / 60, 2))
        out["hr"].append(get(row, "directHeartRate"))
        out["pace"].append(round(1000 / speed / 60, 3) if speed and speed > 0.5 else None)
        out["cadence"].append(_cadence(cad))
        elev = get(row, "directElevation")
        out["elev"].append(round(elev, 1) if elev is not None else None)
    return out if len(out["t"]) >= 4 else None


def decoupling(detail):
    """Aerobic decoupling in percent: positive means heart rate rose relative to pace in
    the second half (the usual sign of fatigue, heat or a pace too fast for the base).
    Under ~5% on a long steady run suggests good aerobic endurance."""
    s = (detail or {}).get("series")
    pairs = []
    if s:
        pairs = [(1 / p, h) for p, h in zip(s["pace"], s["hr"]) if p and h]
        # skip the first ~10% (warm-up, HR still climbing)
        pairs = pairs[len(pairs) // 10:]
    elif (detail or {}).get("laps"):
        pairs = [(1 / lap["pace"], lap["avgHr"]) for lap in detail["laps"]
                 if lap.get("pace") and lap.get("avgHr") and lap["distanceKm"] >= 0.5][1:]
    if len(pairs) < 4:
        return None
    half = len(pairs) // 2
    ef1 = sum(sp for sp, _ in pairs[:half]) / sum(h for _, h in pairs[:half])
    ef2 = sum(sp for sp, _ in pairs[half:]) / sum(h for _, h in pairs[half:])
    return round((ef1 - ef2) / ef1 * 100, 1)
