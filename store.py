"""Your saved Garmin history, notes and per-run details, kept on this computer.

Everything lives in site/data/store/ (gitignored, never uploaded). Each fetch only
asks Garmin for what's new, so history can go back months without refetching it.
"""

import json
from pathlib import Path

STORE = Path(__file__).resolve().parent / "site" / "data" / "store"
DETAILS = STORE / "details"


def read(name, default):
    try:
        return json.loads((STORE / name).read_text())
    except (OSError, ValueError):
        return default


def write(name, obj):
    STORE.mkdir(parents=True, exist_ok=True)
    tmp = STORE / (name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    tmp.replace(STORE / name)  # atomic, so a crash never leaves half a file


def detail(activity_id):
    try:
        return json.loads((DETAILS / f"{activity_id}.json").read_text())
    except (OSError, ValueError):
        return None


def save_detail(activity_id, obj):
    DETAILS.mkdir(parents=True, exist_ok=True)
    (DETAILS / f"{activity_id}.json").write_text(json.dumps(obj))


# ---------- your own notes ----------

RUN_TYPES = ["solo", "run club", "with friends", "race", "treadmill"]


def notes():
    n = read("notes.json", {})
    n.setdefault("runs", {})
    n.setdefault("weights", {})
    return n


def save_run_note(activity_id, run_type=None, shoes=None, effort=None, comment=None):
    """Add or update your note for one activity. Empty values clear that field."""
    n = notes()
    entry = n["runs"].get(str(activity_id), {})
    for key, value in (("runType", run_type), ("shoes", shoes), ("effort", effort), ("comment", comment)):
        if value is None:
            continue
        if value == "":
            entry.pop(key, None)
        else:
            entry[key] = value
    if entry.get("effort") is not None:
        effort = int(entry["effort"])
        if not 1 <= effort <= 10:
            raise ValueError("Effort must be between 1 and 10.")
        entry["effort"] = effort
    if entry.get("runType") and entry["runType"] not in RUN_TYPES:
        raise ValueError("Run type must be one of: " + ", ".join(RUN_TYPES))
    n["runs"][str(activity_id)] = entry
    write("notes.json", n)
    return entry


def save_weight(day, kg):
    try:
        kg = float(kg)
    except (TypeError, ValueError):
        raise ValueError("Enter your weight in kilograms, e.g. 72.4") from None
    if not 30 <= kg <= 250:
        raise ValueError("Weight should be in kilograms, between 30 and 250.")
    n = notes()
    n["weights"][day] = round(kg, 1)
    write("notes.json", n)
