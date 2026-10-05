# garmin-dashboard

A personal dashboard for your own Garmin data: steps, sleep, resting heart rate,
HRV, stress, Body Battery and recent activities.

It has two parts:

- **`fetch_garmin.py`** signs in to Garmin Connect with your account and saves
  your data to `site/data/garmin.json`.
- **`site/`** is a plain web page (HTML, CSS and JavaScript, no build step) that
  shows that file as charts and tables.

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

```bash
python -m http.server 8000 -d site
```

Then open <http://localhost:8000>. (Opening `index.html` directly from the file
system doesn't work, because browsers block it from loading the data file.)

If `garmin.json` doesn't exist yet, the page shows demo data from
`site/data/sample.json`, with a banner saying so. Regenerate the demo data with
`python fetch_garmin.py --sample`.

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
