"""Weather for each run, and a heat adjustment for pace.

Temperature, humidity and dew point at the run's start place and mid-run time,
from Open-Meteo (free, no API key). Results are cached per activity, so each run
is looked up only once.
"""

from datetime import date, datetime, timedelta

import net

HOURLY = "temperature_2m,relative_humidity_2m,dew_point_2m,apparent_temperature,wind_speed_10m"
RECENT = "https://api.open-meteo.com/v1/forecast"  # roughly the last 3 months
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"  # observations; a few days' delay
HISTORICAL = "https://historical-forecast-api.open-meteo.com/v1/forecast"  # backup for older dates


def lookup(lat, lon, start_local, duration_min):
    """Weather at the middle of a run. `start_local` is the watch's local start time
    ("YYYY-MM-DD HH:MM:SS"). Returns a dict, or None if no service answered."""
    start = datetime.strptime(start_local, "%Y-%m-%d %H:%M:%S")
    mid = start + timedelta(minutes=(duration_min or 0) / 2)
    day = mid.date().isoformat()
    params = {"latitude": round(lat, 3), "longitude": round(lon, 3), "start_date": day, "end_date": day,
              "hourly": HOURLY, "timezone": "auto"}
    age = (date.today() - mid.date()).days
    for url in ([RECENT, ARCHIVE] if age <= 60 else [ARCHIVE, HISTORICAL]):
        try:
            data = net.get_json(url, params)
        except Exception:  # noqa: BLE001 - try the next service
            continue
        if data.get("error") or not data.get("hourly"):
            continue
        result = _at(data["hourly"], mid)
        if result:
            result["source"] = url.split("//")[1].split(".")[0]
            return result
    return None


def _at(hourly, when):
    """Values interpolated between the two hours around `when` (local time)."""
    times = hourly.get("time") or []
    base = when.replace(minute=0, second=0, microsecond=0)
    try:
        i = times.index(base.strftime("%Y-%m-%dT%H:%M"))
    except ValueError:
        return None
    j = min(i + 1, len(times) - 1)
    f = (when - base).total_seconds() / 3600

    def val(key):
        a, b = (hourly.get(key) or [None] * len(times))[i], (hourly.get(key) or [None] * len(times))[j]
        if a is None:
            return None
        return round(a + (b - a) * f if b is not None else a, 1)

    out = {"tempC": val("temperature_2m"), "humidity": val("relative_humidity_2m"),
           "dewPointC": val("dew_point_2m"), "feelsLikeC": val("apparent_temperature"),
           "windKmh": val("wind_speed_10m")}
    return out if out["tempC"] is not None else None


# Runner's rule of thumb: add temperature and dew point in °F; the higher the sum,
# the more heat slows you. (sum, % slower) points, interpolated in between.
_HEAT = [(100, 0.0), (110, 0.5), (120, 1.0), (130, 2.0), (140, 3.0), (150, 4.5),
         (160, 6.0), (170, 8.0), (180, 10.0), (190, 12.0)]


def heat_slowdown_pct(temp_c, dew_c):
    """Roughly how much slower heat and humidity make you, in percent (0 when cool)."""
    if temp_c is None or dew_c is None:
        return None
    s = (temp_c * 9 / 5 + 32) + (dew_c * 9 / 5 + 32)
    if s <= _HEAT[0][0]:
        return 0.0
    for (s0, p0), (s1, p1) in zip(_HEAT, _HEAT[1:]):
        if s <= s1:
            return round(p0 + (p1 - p0) * (s - s0) / (s1 - s0), 1)
    return _HEAT[-1][1]


def adjusted_pace(pace_min_km, slowdown_pct):
    """The pace the same effort would give in cool conditions."""
    if pace_min_km is None or not slowdown_pct:
        return pace_min_km
    return pace_min_km / (1 + slowdown_pct / 100)
