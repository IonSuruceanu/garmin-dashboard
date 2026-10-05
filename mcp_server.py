"""Connect this dashboard to the Claude desktop app (an MCP server).

Once installed, you can ask Claude in its normal chat things like "how ready am I
to train today?", "refresh my Garmin data", "show my VO2 max trend" or "send the
tempo workout to my Telegram"; Claude reads your data through the tools below.

    python mcp_server.py --install   # one-time: register with Claude Desktop, then restart it
    python mcp_server.py             # what Claude Desktop runs (you don't run this yourself)

Everything stays on your Mac: Claude Desktop starts this script locally.
"""

import argparse
import functools
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "site" / "data" / "garmin.json"
CLAUDE_CONFIG = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
SERVER_NAME = "garmin-dashboard"

INSTRUCTIONS = """Personal Garmin training data, already analysed by the athlete's own \
dashboard. Start with get_today for readiness and workout options. Data comes from \
Garmin Connect and may be hours old: check fetchedAt and call refresh_from_garmin if \
the athlete wants fresh numbers. Missing sleep/HRV means the watch wasn't worn \
overnight; resting HR on those days is a daytime estimate and reads high. \
Activities carry weather (temperature, dew point) and heat-adjusted pace; use \
get_training_history for blocks/monthly fitness and get_run_detail for laps and \
heart-rate drift. Heart-rate numbers (easy ceiling, zones) come from the athlete's \
Garmin settings, see get_running -> hr. Notes (run type, shoes, effort 1-10) are the \
athlete's own; ask before adding one. Give coaching guidance, not medical advice."""


def load():
    if not DATA_FILE.exists():
        raise ValueError("No Garmin data yet. Call refresh_from_garmin first.")
    return json.loads(DATA_FILE.read_text())


def build_server():
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    def readable(fn):
        """Show our own error messages to Claude (other exceptions are masked)."""
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (ValueError, OSError) as e:
                raise ToolError(str(e)) from None
            except SystemExit as e:  # helper scripts exit with a readable message
                raise ToolError(str(e)) from None
        return wrapper

    server = MCPServer(name=SERVER_NAME, title="Garmin dashboard", instructions=INSTRUCTIONS)

    @server.tool()
    @readable
    def get_today() -> dict:
        """Today's readiness score (with the reasons behind it) and the workout options,
        each marked recommended / good / no (not today, with why), with steps and paces.
        Also when the data was last fetched from Garmin."""
        d = load()
        return {"fetchedAt": d.get("fetchedAt"), "athlete": d.get("athlete"), "today": d.get("today")}

    @server.tool()
    @readable
    def get_insights() -> list:
        """Plain-language insights comparing recent days with the athlete's own baseline:
        recovery, sleep, training load, stress, steps and running."""
        return load().get("insights") or []

    @server.tool()
    @readable
    def get_running() -> dict:
        """Running analysis: this week's and average weekly distance, longest run,
        12-week weekly distances, easy/moderate/hard balance, training paces, race
        predictions, cadence, running efficiency and Garmin training status."""
        r = dict(load().get("running") or {})
        r.pop("vo2", None)  # see get_vo2max
        r["efficiency"] = (r.get("efficiency") or [])[-20:]
        return r

    @server.tool()
    @readable
    def get_vo2max() -> dict:
        """VO2 max: current value, source (Garmin or estimate), fitness level for the
        athlete's age and sex, 12-week change and history."""
        return (load().get("running") or {}).get("vo2") or {"note": "No VO2 max available."}

    @server.tool()
    @readable
    def get_daily(days: int = 14) -> list:
        """Daily metrics for the last `days` days (max 30): steps, sleep and stages,
        sleep score, resting HR, HRV, stress, Body Battery, calories, intensity minutes."""
        return (load().get("daily") or [])[-max(1, min(days, 30)):]

    @server.tool()
    @readable
    def get_activities(limit: int = 15, activity_type: str = "") -> list:
        """Recent activities, newest first (up to 12 weeks): distance, duration, heart
        rate, pace/speed, training effect, training load, cadence, HR zones.
        activity_type filters by Garmin type key, e.g. "running" or "cycling"."""
        acts = load().get("activities") or []
        if activity_type:
            acts = [a for a in acts if activity_type.lower() in (a.get("type") or "")]
        return acts[:max(1, min(limit, 100))]

    @server.tool()
    @readable
    def get_training_history() -> dict:
        """Training since the start of the saved history: weekly distance and load,
        training blocks (runs of active weeks ended by 2+ quiet weeks, with the late
        week-on-week jump before each ended), fitness by month (best effort as a
        5K-equivalent, raw and heat-adjusted; distance per heartbeat, raw and
        heat-adjusted; average temperature), and the last 6 weeks vs the best month."""
        r = load().get("running") or {}
        return {"history": r.get("history"), "monthly": r.get("monthly"), "fitnessCompare": r.get("fitnessCompare"),
                "drift": r.get("drift"), "shoes": r.get("shoes"), "weight": r.get("weight")}

    @server.tool()
    @readable
    def get_run_detail(activity_id: str = "latest") -> dict:
        """One activity in full: summary, weather and heat adjustment, your note, laps
        (pace, HR, cadence, stride per lap), and HR/pace/cadence every 30 s, plus
        aerobic decoupling (HR drift, %). activity_id from get_activities, or "latest"
        for the most recent run. Details exist for runs over 4 km."""
        import store
        acts = load().get("activities") or []
        if activity_id == "latest":
            a = next((x for x in acts if "running" in (x.get("type") or "")), None)
        else:
            a = next((x for x in acts if str(x.get("id")) == str(activity_id)), None)
        if not a:
            raise ValueError("No such activity. Use get_activities to find the id.")
        d = store.detail(a["id"]) if (a.get("detailPath") or "").startswith("store/") else None
        if d is None and a.get("detailPath"):
            p = ROOT / "site" / "data" / a["detailPath"]
            d = json.loads(p.read_text()) if p.exists() else None
        return {"activity": a, "detail": d or "No laps or time series saved for this activity (runs over 4 km only)."}

    @server.tool()
    @readable
    def add_run_note(activity_id: str = "latest", run_type: str = "", shoes: str = "",
                     effort: int = 0, comment: str = "") -> str:
        """Save the athlete's own note on a run (only what they told you). run_type:
        solo, run club, with friends, race or treadmill. shoes: the shoe name, used for
        mileage. effort: how hard it felt, 1-10 (0 = leave unchanged). activity_id from
        get_activities, or "latest" for the most recent run. Leave a field empty to
        keep it as is."""
        import fetch_garmin
        import store
        acts = load().get("activities") or []
        a = (next((x for x in acts if "running" in (x.get("type") or "")), None) if activity_id == "latest"
             else next((x for x in acts if str(x.get("id")) == str(activity_id)), None))
        if not a:
            raise ValueError("No such activity. Use get_activities to find the id.")
        if not store.read("activities.json", {}):
            raise ValueError("Notes need your own Garmin data. Call refresh_from_garmin first.")
        entry = store.save_run_note(a["id"], run_type or None, shoes or None, effort or None, comment or None)
        fetch_garmin.rebuild()
        return f"Saved for {a.get('name')} on {(a.get('start') or '')[:10]}: {entry}"

    @server.tool()
    @readable
    def log_weight(kg: float, day: str = "") -> str:
        """Log the athlete's body weight in kg for a day (YYYY-MM-DD, default today)."""
        import fetch_garmin
        import store
        from datetime import date
        store.save_weight(day or date.today().isoformat(), kg)
        if store.read("activities.json", {}):
            fetch_garmin.rebuild()
        return f"Logged {kg} kg."

    @server.tool()
    @readable
    def refresh_from_garmin() -> str:
        """Fetch new data from Garmin Connect (only what changed since last time) and
        recalculate everything. Usually under a minute; the first full download of
        history, run details and weather takes longer."""
        r = subprocess.run([sys.executable, str(ROOT / "fetch_garmin.py")],
                           cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise ValueError("Refresh failed: " + " ".join((r.stderr or r.stdout).strip().splitlines()[-4:]))
        return (r.stdout.strip().splitlines() or ["Done."])[-1]

    @server.tool()
    @readable
    def send_to_telegram(what: str = "report", workout: str = "") -> str:
        """Send something to the athlete's Telegram bot. what="report" sends today's
        summary; what="workout" sends the plan for `workout`, one of the option keys
        from get_today (rest, recovery, easy, long, tempo, intervals, strength)."""
        import telegram_bot
        import telegram_summary as tg
        if not tg.config():
            raise ValueError("Telegram isn't set up. Run: python telegram_summary.py --setup")
        d = load()
        msg = tg.build_message(d) if what == "report" else telegram_bot.workout_message(d, workout)
        tg.send(msg)
        return "Sent to Telegram."

    return server


# ---------- install into Claude Desktop ----------

def install():
    if sys.platform != "darwin":
        sys.exit("--install sets up Claude Desktop on a Mac. On other systems, add this server by hand (see README).")
    if importlib.util.find_spec("mcp") is None:
        sys.exit("Install the requirements first: pip install -r requirements.txt")
    config = {}
    if CLAUDE_CONFIG.exists():
        try:
            config = json.loads(CLAUDE_CONFIG.read_text() or "{}")
        except ValueError:
            sys.exit(f"{CLAUDE_CONFIG} isn't valid JSON, so I won't touch it. Fix or delete it and run again.")
        backup = CLAUDE_CONFIG.with_name(f"claude_desktop_config.backup-{datetime.now():%Y%m%d-%H%M%S}.json")
        shutil.copy2(CLAUDE_CONFIG, backup)
        print(f"Backed up your current settings to {backup.name}")
    CLAUDE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    # Use this exact Python (the project's .venv) so the Garmin and MCP libraries are found.
    config.setdefault("mcpServers", {})[SERVER_NAME] = {"command": sys.executable, "args": [str(ROOT / "mcp_server.py")]}
    CLAUDE_CONFIG.write_text(json.dumps(config, indent=2))
    print(f"Added '{SERVER_NAME}' to Claude Desktop.")
    print("Now quit Claude Desktop completely (⌘Q) and open it again.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--install", action="store_true", help="register this server with Claude Desktop (Mac)")
    args = p.parse_args()
    if args.install:
        return install()
    build_server().run()  # stdio: Claude Desktop talks to it over stdin/stdout


if __name__ == "__main__":
    main()
