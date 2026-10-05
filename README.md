# garmin-dashboard

A personal dashboard for your own Garmin data: steps, sleep, resting heart rate,
HRV, stress, Body Battery and recent activities, with plain-language insights
and Telegram messages.

- **`fetch_garmin.py`** signs in to Garmin Connect with your account and saves
  your data to `site/data/garmin.json`.
- **`insights.py`** compares your recent days with your own baseline (sleep,
  HRV, resting heart rate, training load, stress, steps) and writes short
  insights into the same file.
- **`coach.py`** analyses your running (weekly distance, efficiency, effort
  balance, race predictions, training paces) and works out today's readiness
  and workout options.
- **`site/`** is a plain web page (HTML, CSS and JavaScript, no build step) that
  shows that file as charts, tables and insights.
- **`telegram_summary.py`** sends a morning summary to your Telegram.
- **`activity_watch.py`** sends a Telegram message after each new activity.
- **`telegram_bot.py`** answers `/report`, `/today` and `/refresh` sent to your bot.
- **`coach_ai.py`** lets you ask Claude questions about your training from
  Telegram, with your Garmin data as context (optional, needs an API key).
- **`mcp_server.py`** connects the dashboard to the Claude desktop app, so you
  can ask about your training in the normal Claude chat (no API key needed).
- **`serve.py`** runs the dashboard locally with buttons for refreshing and
  sending to Telegram, and keeps the bot answering within seconds.
- **`.github/workflows/garmin-telegram.yml`** runs the two Telegram scripts on
  GitHub's servers, so they work while your Mac is off.

Your data and your Garmin login stay on your computer. `site/data/garmin.json`
and the saved login tokens are listed in `.gitignore`, so they are never committed.

## Setup (once)

You need Python 3.10 or newer.

```bash
git clone https://github.com/IonSuruceanu/garmin-dashboard.git
cd garmin-dashboard
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Get your data

```bash
python fetch_garmin.py            # last 30 days
python fetch_garmin.py --days 90  # or further back
```

The first time, it asks for your Garmin email, password and, if you use 2-step
verification, the code Garmin sends you. It then saves a login token in
`~/.garminconnect`, so later runs don't ask again. To skip the prompts you can
set `GARMIN_EMAIL` and `GARMIN_PASSWORD` as environment variables instead.

Run it again whenever you want fresh numbers.

## View the dashboard

The easy way: in Finder, open the `garmin-dashboard` folder and double-click
**`start.command`**. It updates the project, installs what's needed, starts the
dashboard and the Telegram bot, and opens the page. (The first time, macOS may
ask you to confirm opening it.)

Or from Terminal, inside the project folder:

```bash
python serve.py
```

Then open <http://localhost:8000>. Keep the Terminal window open while you use
it. At the top you get **↻ Refresh from Garmin** and **Send report to
Telegram**; after picking a workout, **Send to Telegram** sends you its plan.

(`python -m http.server 8000 -d site` also works, just without those buttons.
Opening `index.html` directly from the file system doesn't work, because
browsers block it from loading the data file.)

If `garmin.json` doesn't exist yet, the page shows demo data from
`site/data/sample.json`, with a banner saying so. Regenerate the demo data with
`python fetch_garmin.py --sample`.

## What the dashboard shows

- **Today:** a readiness score (Garmin's own if your watch provides it,
  otherwise calculated from HRV, resting heart rate, sleep, Body Battery and
  recent training) with the reasons behind it, and workout options: rest,
  recovery run, easy run, long run, tempo, intervals, strength. Each is marked
  *Recommended*, *Good option* or *Not today* (with why). Click one to choose
  it and see the full workout with paces from your own data. Your choice is
  remembered in this browser for the day.
- **What's going on:** insights on recovery, sleep, training load and running.
- **Running:** weekly distance (12 weeks), running efficiency (metres per
  heartbeat; rising means fitter), easy/moderate/hard balance, easy pace,
  VO2 max, cadence and race predictions.
- Daily charts, recent activities and data sources.

Paces come from Garmin's race predictions when available, otherwise from your
best recent run. All of this is calculated on your computer; no AI service or
extra API is used. It's guidance, not medical advice.

## Good to know

- **Unofficial access.** [`garminconnect`](https://github.com/cyberjunky/python-garminconnect)
  is a community library, not an official Garmin API. It's fine for reading
  your own data, but a change on Garmin's side can break it until the library
  is updated (`pip install -U garminconnect`).
- **Sign-in problems.** If you change your Garmin password or sign-in keeps
  failing, delete the `~/.garminconnect` folder and run the script again.
- **Rate limits.** Fetching many months at once makes many requests; Garmin may
  temporarily block you. Keep `--days` modest and re-run occasionally.
- **Missing metrics.** Values your watch doesn't record (for example HRV on
  older models) show as "—".

## Telegram messages

### Connect your bot (once, on your Mac)

1. In Telegram, open **@BotFather**, send `/newbot`, pick a name and a username
   ending in `bot`. BotFather replies with a **token** like `123456:ABC-xyz`.
2. Run:
   ```bash
   python telegram_summary.py --setup
   ```
   Paste the token, then open your new bot in Telegram, press **Start**, and
   press Enter in Terminal. You'll get a test message. The token is saved in
   `telegram.json`, which is gitignored.

Then, on your Mac:

```bash
python telegram_summary.py            # send this morning's summary now
python telegram_summary.py --dry-run  # just print it
python activity_watch.py              # message any new activities
```

### Ask your bot for a report

Send these to your bot in Telegram:

| Command | What you get |
|---|---|
| `/report` | today's full summary |
| `/today` | readiness and workout options as buttons; tap one for the full plan |
| `/refresh` | fetches new data from Garmin, then sends the report |

While `python serve.py` (or `python telegram_bot.py`) is running on your Mac,
the bot answers within seconds. When it isn't, the hourly GitHub job answers,
so the reply can take up to an hour. The bot only answers your own chat.

### Ask in the Claude desktop app (no API key)

Connect the dashboard to the Claude desktop app on your Mac and ask in its
normal chat: *"How ready am I to train today?"*, *"Refresh my Garmin data"*,
*"How is my VO2 max trending?"*, *"Send the tempo workout to my Telegram"*.
It uses your Claude subscription, not an API key.

```bash
cd ~/garmin-dashboard && source .venv/bin/activate
pip install -r requirements.txt
python mcp_server.py --install
```

Then quit Claude Desktop completely (⌘Q) and open it again. The first time
Claude uses a tool it asks for permission; choose **Always allow** for the
read-only ones. Claude Desktop starts the connector itself, so you don't need
`serve.py` running for this. `--install` backs up your existing Claude Desktop
settings first and keeps any other connectors.

Tools Claude gets: `get_today`, `get_insights`, `get_running`, `get_vo2max`,
`get_daily`, `get_activities`, `refresh_from_garmin`, `send_to_telegram`.
This works only in Claude Desktop on your Mac (the phone app can't reach it).

### Ask Claude questions in Telegram (optional)

Send your bot any message that isn't a command, for example *"Should I run
today?"*, *"Why is my HRV low?"* or *"Plan my week for a 10K"*, and Claude
answers using your latest Garmin data. It remembers the last few questions so
you can follow up; `/new` starts over.

1. Create an API key at <https://console.anthropic.com> (API Keys → Create Key)
   and add some credit. This is pay-per-use and separate from a Claude.ai
   subscription; a question typically costs a few cents.
2. Copy the key, then run `python coach_ai.py --setup` (it reads the key from
   your clipboard and saves it to `claude.json`, which is gitignored).
3. For answers while your Mac is off, also add an `ANTHROPIC_API_KEY`
   repository secret on GitHub.

You can also ask from Terminal: `python coach_ai.py "Should I run today?"`.
Answers are coaching guidance, not medical advice.

### Run it automatically on GitHub

The workflow sends the morning summary at about 07:15 Swiss summer time
(06:15 in winter) and checks for new activities every hour. Your Garmin data
is never committed; it only exists while the job runs.

1. On your Mac, print your saved Garmin login:
   ```bash
   python fetch_garmin.py --print-tokens
   ```
2. On GitHub, open the repo → **Settings → Secrets and variables → Actions →
   New repository secret**, and add three secrets:

   | Name | Value |
   |---|---|
   | `GARMIN_TOKENS` | everything `--print-tokens` printed |
   | `TELEGRAM_BOT_TOKEN` | the token from BotFather |
   | `TELEGRAM_CHAT_ID` | the chat id `--setup` printed |
3. Open the **Actions** tab → **Garmin → Telegram** → **Run workflow** to test it.

Notes:

- Scheduled workflows only run from the repository's default branch (`main`).
- Activity messages arrive within about an hour of the activity syncing to
  Garmin Connect. Garmin doesn't offer instant notifications to personal apps.
- Each run refreshes the Garmin login and keeps it in the repo's private
  Actions cache. If runs start failing with a sign-in error (you'll get a
  Telegram alert), run `python fetch_garmin.py` on your Mac and update the
  `GARMIN_TOKENS` secret with the new `--print-tokens` output.
- On a private repo this uses roughly 750 of the 2,000 free Actions minutes a month.
