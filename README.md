# Artemisia 🌿

A private, self-hosted menstrual cycle tracker. Named for *Artemisia* — mugwort,
the moon-herb of Artemis. Your data lives in one SQLite file on your own
computer. No accounts, no cloud, no ads, no subscription, no one monetising
your luteal phase.

Built in an afternoon by Catherine — a researcher, not a developer — with
**Claude** as gardener's assistant: the code, the moon maths, and the odds
ratios are his handiwork; the decade of data, the taste, and the stubbornness
about owning it are hers. If you'd rather grow your own than run this one, see
[PROMPT.md](PROMPT.md) for a copy-paste prompt to give any capable AI assistant.

## What she does

- **Calendar** — log flow, symptoms, mood, energy, discharge, and notes with a
  tap. Cycle day numbers in every cell. Real lunar phases marked (🌑🌓🌕🌗).
- **Honest predictions** — next period as an 80% window computed from *your*
  history ("likely between the 28th and the 2nd"), not a false-confident date.
  Fertile window and ovulation estimates, clearly labelled as estimates.
- **Year view** — twelve months at a glance; a decade becomes visible.
- **Insights** — cycle length over the years, distribution, symptom and mood
  frequencies.
- **The Almanac** — real statistics on your own data: which symptoms cluster in
  which cycle phase (odds ratios with 95% CIs), a Rayleigh test of whether your
  cycles track the moon (spoiler: population studies say they won't — but now
  you can know for *you*), rolling variability with the STRAW+10 ≥7-day marker
  so perimenopause announces itself early and calmly, seasonality, and a
  phase compass with schematic hormone curves.
- **Clue import** — swallows a Clue GDPR export whole (`measurements.json` +
  `cycle_attributes.json`). Additive and idempotent; nothing in your history
  is lost — even data types the app doesn't edit are preserved and displayed.
- **Backup & export** — download everything as CSV or the SQLite file itself.
- **Installable phone app (PWA)** — with HTTPS, installs on Android/iOS with
  its own icon, standalone window, and a long-press "Log today" shortcut.

## Getting her running — assuming nothing

Never used GitHub, Python, or a terminal? This section is for you. Ten
minutes, no prior knowledge. (Already comfortable? The short version:
Python 3.9+, `pip install -r requirements.txt`, `python3 app.py`,
visit http://localhost:5876.)

### Step 1 — Download her

At the top of this page, find the green **Code** button → **Download ZIP**.
Unzip it (right-click → Extract All on Windows; double-click on a Mac) and
move the `artemisia-main` folder somewhere permanent that gets backed up —
your Documents folder is perfect. Your cycle data will live inside this
folder, so don't leave it in Downloads where future-you might tidy it away.

### Step 2 — Install Python (the language she speaks)

- **Windows**: get the installer from [python.org/downloads](https://www.python.org/downloads/).
  When it opens, **tick the box that says "Add python.exe to PATH"** before
  clicking Install — this is the single most important click in this guide.
- **Mac**: open the Terminal app (⌘-space, type "terminal"), type
  `python3 --version` and press Enter. If macOS offers to install
  "command line developer tools", say yes — that *is* the Python install.
- **Linux / Raspberry Pi**: you almost certainly have it. `python3 --version`
  to confirm.

### Step 3 — Open a terminal *in her folder*

- **Windows**: open the `artemisia-main` folder in File Explorer, click in
  the address bar at the top, type `cmd` and press Enter. A black window
  appears, already standing in the right place.
- **Mac**: right-click the `artemisia-main` folder → Services →
  **New Terminal at Folder** (or drag the folder onto the Terminal icon).

### Step 4 — Two spells, once ever

In that terminal window, run (type it, press Enter, let it finish):

```bash
python3 -m pip install -r requirements.txt
```

(On Windows, if `python3` isn't recognised, use `py` instead: `py -m pip install -r requirements.txt`.)

### Step 5 — Wake her

```bash
python3 app.py
```

(Windows: `py app.py`.) You'll see *Artemisia unfurling at
http://127.0.0.1:5876*. Open your web browser and visit
**http://localhost:5876** — there she is. Log something. She's yours.

Notes for the road: the terminal window must stay open while she runs
(Ctrl+C stops her; running the Step 5 command again wakes her). Your data
lives in a single file, `artemisia.db`, which appears in her folder after
first run — that file *is* your history, so let your backups include it.
To start her automatically and keep her running quietly, see
"Run at startup" below.

Environment knobs for the curious: `ARTEMISIA_DB` (database path),
`ARTEMISIA_HOST` (default `127.0.0.1`; set `0.0.0.0` to allow other devices
on a trusted network), `ARTEMISIA_PORT` (default `5876`).

## Getting your data out of Clue

1. Clue app → ☰ → **Settings** → **Download my data** → **Request data**
2. Copy the password shown; Clue emails a link (expires in 72 h)
3. Download, unzip with the password
4. Import the folder: `python3 app.py import /path/to/ClueDataDownload-folder`
   (or upload `measurements.json` on the app's import page)

Safe to re-run — it never duplicates.

## Run at startup

- **macOS**: edit paths in `com.example.artemisia.plist`, copy to
  `~/Library/LaunchAgents/`, then `launchctl load` it.
- **Linux (systemd)**: edit paths in `artemisia.service`, copy to
  `/etc/systemd/system/`, then `systemctl enable --now artemisia`.

## Making it a phone app

The PWA needs HTTPS. The gentlest path is [Tailscale](https://tailscale.com)
(free for personal use): install it on the server and your phone, then on the
server run `tailscale serve --bg 5876` — you get a valid-HTTPS URL reachable
from your devices anywhere, visible to no one else. Open it in Chrome on your
phone → menu → **Add to Home screen** → **Install**.

**Hard-won gotcha:** Android identifies installed web apps by *hostname* and
ignores the port. If you self-host several apps on one machine, each
installable app needs its **own hostname** (e.g. via Tailscale's named
Services), not just its own port — otherwise Android will insist your second
app is "already installed".

## Backups

Everything is one file: `artemisia.db`. Copy it anywhere. Restoring is putting
it back.

## Honesty notes

Cycle-day counting starts on the first day of true bleeding (spotting doesn't
start a cycle). Ovulation is estimated at 14 days before the next predicted
period; fertile-window marks are calendar estimates from your own averages —
a compass, not a contraceptive. The almanac's hormone curves are
population-typical schematics, not measurements. None of this is medical
advice; it's your own data, described carefully.

## Licence

MIT — see [LICENSE](LICENSE). Take her, rename her, make her yours.
