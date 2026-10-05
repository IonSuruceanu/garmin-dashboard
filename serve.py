"""Run the dashboard locally, with buttons that talk to Garmin and Telegram.

    python serve.py              # then open http://localhost:8000
    python serve.py --port 8080

Same as `python -m http.server -d site`, plus three actions the page can call:
refresh data from Garmin, send the report to Telegram, and send the workout
you picked. While it runs, your Telegram bot also answers /report, /today and
/refresh within seconds. It only listens on your own computer (127.0.0.1).
"""

import argparse
import json
import socket
import sys
import traceback
import threading
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import telegram_bot as bot
import telegram_summary as tg

SITE = Path(__file__).resolve().parent / "site"


def save_note(path, body):
    """Save a run note or a weight, then recalculate garmin.json (no Garmin call)."""
    import fetch_garmin
    import store
    if not store.read("activities.json", {}):
        raise ValueError("Notes need your own Garmin data. Run `python fetch_garmin.py` first.")
    if path == "/api/note":
        if not body.get("id"):
            raise ValueError("Which run? (missing id)")
        store.save_run_note(body["id"], body.get("runType"), body.get("shoes"), body.get("effort"), body.get("comment"))
    else:
        store.save_weight(body.get("date") or date.today().isoformat(), body.get("kg"))
    fetch_garmin.rebuild()
    return {"ok": True, "message": "Saved."}


def telegram_ready():
    return tg.config() is not None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITE), **kwargs)

    def end_headers(self):
        # Always serve the latest files and data.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)

    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            return self.reply(200, {"server": True, "telegram": telegram_ready()})
        return super().do_GET()

    def do_POST(self):
        # Only accept requests from this dashboard on this computer, not from other
        # websites (checking Host as well blocks DNS-rebinding tricks).
        port = self.server.server_address[1]
        allowed = {f"localhost:{port}", f"127.0.0.1:{port}"}
        origin = self.headers.get("Origin", "")
        if self.headers.get("Host", "") not in allowed or (origin and origin.split("//", 1)[-1] not in allowed):
            return self.reply(403, {"error": "Not allowed."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.reply(400, {"error": "Bad request."})

        try:
            if self.path == "/api/refresh":
                return self.reply(200, {"ok": True, "message": bot.refresh()})
            if self.path in ("/api/note", "/api/weight"):
                return self.reply(200, save_note(self.path, body))
            if self.path in ("/api/report", "/api/workout"):
                if not telegram_ready():
                    return self.reply(400, {"error": "Telegram isn't set up. Run: python telegram_summary.py --setup"})
                data = bot.load_data()
                if not data:
                    return self.reply(400, {"error": "No Garmin data yet. Click Refresh first."})
                msg = (tg.build_message(data) if self.path == "/api/report"
                       else bot.workout_message(data, body.get("key")))
                tg.send(msg)
                return self.reply(200, {"ok": True, "message": "Sent to Telegram."})
        except ValueError as e:
            return self.reply(400, {"error": str(e)})
        except SystemExit as e:  # the scripts exit with a readable message on errors
            return self.reply(500, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - always answer the page; details go to Terminal
            traceback.print_exc()
            return self.reply(500, {"error": f"Something went wrong ({type(e).__name__}: {e}). Details are in the Terminal window."})
        return self.reply(404, {"error": "Unknown action."})


def port_in_use(port):
    """True if something already answers on this port (IPv4 or IPv6), e.g. an old
    `python -m http.server` left running in another Terminal window."""
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                if s.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def our_dashboard_running(port):
    """True if the thing on this port is already this dashboard (serve.py)."""
    import urllib.request
    for host in ("127.0.0.1", "[::1]"):
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/api/status", timeout=2) as r:
                if json.load(r).get("server"):
                    return True
        except Exception:  # noqa: BLE001 - anything else on the port isn't us
            continue
    return False


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    if port_in_use(args.port):
        if our_dashboard_running(args.port):
            print(f"The dashboard is already running at http://localhost:{args.port} (in another window).")
            print("Use that one, or close its window first if you want to restart it.")
            return
        sys.exit(f"Port {args.port} is already in use, probably by an older dashboard (e.g. `python -m http.server`)\n"
                 f"in another Terminal window. Close that window (or press Ctrl+C in it) and start again,\n"
                 f"or use another port: python serve.py --port {args.port + 1}")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Dashboard running at http://localhost:{args.port}  (press Ctrl+C to stop)")
    stop = threading.Event()
    if telegram_ready():
        # Answer /report, /today and /refresh in Telegram while the dashboard runs.
        threading.Thread(target=bot.listen, args=(stop,), daemon=True).start()
        print("Telegram bot is listening: send /today or /report to your bot.")
    else:
        print("Telegram is off until you run: python telegram_summary.py --setup")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        stop.set()
        print("\nStopped.")


if __name__ == "__main__":
    main()
