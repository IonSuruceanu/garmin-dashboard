"""Ask Claude questions about your training, with your Garmin data as context.

Used by the Telegram bot: any message that isn't a command goes to Claude.

    python coach_ai.py --setup             # one-time: save your Claude API key
    python coach_ai.py "Should I run today?"   # ask from Terminal

Needs a Claude API key from https://console.anthropic.com (pay per use, about
a few cents per question; separate from a Claude.ai subscription).
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import anthropic

import telegram_summary as tg

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "site" / "data" / "garmin.json"
KEY_FILE = ROOT / "claude.json"  # gitignored: holds your API key
HISTORY_FILE = ROOT / "chat_history.json"  # gitignored: recent questions, for follow-ups
MODEL = "claude-opus-5-5"
KEEP_TURNS = 6  # question/answer pairs remembered for follow-up questions

SYSTEM = """You are a friendly, knowledgeable running and endurance coach answering \
questions in a Telegram chat. The athlete's latest Garmin data is provided below as \
JSON, already analysed: readiness and today's workout options, running analysis \
(weekly distance, paces, VO2 max, effort balance), insights, recent days and recent \
activities.

How to answer:
- Base answers on the athlete's own numbers and say which numbers you used. If the \
data doesn't contain what's needed, say so rather than guessing.
- Be concise and practical: a few short paragraphs or a short list. This is read on a \
phone.
- Plain text only. No Markdown, no tables, no headings. Simple dashes for lists are fine.
- Missing sleep/HRV usually means the watch wasn't worn overnight; resting HR on days \
without sleep data is a daytime estimate and reads high.
- You are not a doctor. For pain, injury, illness or worrying symptoms, suggest seeing \
a professional.

Athlete data:
"""


# ---------- key ----------

KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")


def api_key():
    """Claude API key: env var / GitHub secret, or claude.json on your Mac."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    if KEY_FILE.exists():
        return json.loads(KEY_FILE.read_text()).get("api_key")
    return None


def enabled():
    return bool(api_key())


def client():
    # Same trusted certificates as the Telegram calls (works behind antivirus/VPNs).
    return anthropic.Anthropic(api_key=api_key(), max_retries=3,
                               http_client=anthropic.DefaultHttpxClient(verify=tg.SSL_CONTEXT))


def setup():
    print("1. Open https://console.anthropic.com → API Keys → Create Key, and copy the key.")
    key = KEY_RE.search(re.sub(r"\s+", "", tg.clipboard() or ""))
    key = key.group(0) if key else None
    if key and input("Found a Claude API key on your clipboard. Use it? [Y/n] ").strip().lower() not in ("", "y", "yes"):
        key = None
    while not key:
        m = KEY_RE.search(re.sub(r"\s+", "", input("Paste the API key here and press Enter: ")))
        key = m.group(0) if m else None
        if not key:
            print("That doesn't look like a Claude API key (it starts with sk-ant-).")
    try:
        anthropic.Anthropic(api_key=key, http_client=anthropic.DefaultHttpxClient(verify=tg.SSL_CONTEXT)).models.retrieve(MODEL)
    except anthropic.AuthenticationError:
        sys.exit("Anthropic rejected that key. Copy it again from the console.")
    except anthropic.APIConnectionError:
        sys.exit("Couldn't reach Anthropic. Check your internet connection and try again.")
    KEY_FILE.write_text(json.dumps({"api_key": key}))
    KEY_FILE.chmod(0o600)
    print(f"Saved to {KEY_FILE.name}. Send your bot any question in Telegram while serve.py runs.")


# ---------- context ----------

def context(data):
    """The parts of garmin.json worth sending: analysed results plus recent raw data."""
    running = dict(data.get("running") or {})
    running["efficiency"] = (running.get("efficiency") or [])[-10:]
    if running.get("vo2"):
        running["vo2"] = {k: v for k, v in running["vo2"].items() if k != "history"}
    return json.dumps({
        "athlete": data.get("athlete"),
        "dataFetchedAt": data.get("fetchedAt"),
        "today": data.get("today"),
        "insights": data.get("insights"),
        "running": running,
        "last14Days": (data.get("daily") or [])[-14:],
        "recentActivities": (data.get("activities") or [])[:15],
    }, separators=(",", ":"))


def load_history():
    try:
        return json.loads(HISTORY_FILE.read_text())
    except (OSError, ValueError):
        return []


def save_history(history):
    HISTORY_FILE.write_text(json.dumps(history[-KEEP_TURNS * 2:]))


def reset():
    HISTORY_FILE.unlink(missing_ok=True)


# ---------- asking ----------

def ask(question, data):
    """Claude's answer as plain text. Raises ValueError with a readable message on failure."""
    if not enabled():
        raise ValueError("Claude isn't set up yet. On your Mac run: python coach_ai.py --setup")
    history = load_history()
    messages = history + [{"role": "user", "content": question}]
    try:
        response = client().beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM + context(data),
            messages=messages,
            output_config={"effort": "medium"},
            cache_control={"type": "ephemeral"},
            # If a safety check declines, the API retries on a suitable model automatically.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise ValueError("Anthropic rejected the saved API key. Run: python coach_ai.py --setup") from None
    except anthropic.PermissionDeniedError:
        raise ValueError("Your Anthropic account can't use this model. Check the console.") from None
    except anthropic.RateLimitError:
        raise ValueError("Too many questions at once. Try again in a minute.") from None
    except anthropic.BadRequestError as e:
        if "credit" in str(e).lower():
            raise ValueError("Your Anthropic account is out of credit. Top it up in the console.") from None
        raise ValueError(f"Claude couldn't take that request: {e.message}") from None
    except anthropic.APIStatusError as e:
        raise ValueError(f"Claude is unavailable right now ({e.status_code}). Try again shortly.") from None
    except anthropic.APIConnectionError:
        raise ValueError("Couldn't reach Claude. Check the internet connection.") from None

    if response.stop_reason == "refusal":
        return "Sorry, I can't help with that one. Try asking it differently."
    answer = "".join(b.text for b in response.content if b.type == "text").strip()
    if not answer:
        return "I didn't get an answer that time. Please ask again."
    save_history(messages + [{"role": "assistant", "content": answer}])
    return answer


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("question", nargs="*", help="a question to ask from Terminal")
    p.add_argument("--setup", action="store_true", help="save your Claude API key (one time)")
    args = p.parse_args()
    if args.setup:
        return setup()
    if not args.question:
        p.print_help()
        return
    if not DATA_FILE.exists():
        sys.exit("No Garmin data yet. Run: python fetch_garmin.py")
    try:
        print(ask(" ".join(args.question), json.loads(DATA_FILE.read_text())))
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
