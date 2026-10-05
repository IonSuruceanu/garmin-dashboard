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
overnight; resting HR on those days is a daytime estimate and reads high. Give \
coaching guidance, not medical advice."""


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
    def refresh_from_garmin(days: int = 30) -> str:
        """Fetch fresh data from Garmin Connect and recalculate everything. Takes up to
        a minute. Uses the saved Garmin login on this Mac."""
        r = subprocess.run([sys.executable, str(ROOT / "fetch_garmin.py"), "--days", str(max(7, min(days, 90)))],
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
