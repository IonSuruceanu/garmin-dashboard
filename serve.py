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
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import telegram_bot as bot
import telegram_summary as tg

SITE = Path(__file__).resolve().parent / "site"


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
        return self.reply(404, {"error": "Unknown action."})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
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
