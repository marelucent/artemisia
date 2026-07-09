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

## Quickstart

Requires Python 3.9+ and Flask. On any Mac, Linux box, Windows machine, or
Raspberry Pi:

```bash
pip install -r requirements.txt
python3 app.py
```

Visit **http://localhost:5876**. That's it — the database file
(`artemisia.db`) appears next to `app.py`.

Environment knobs: `ARTEMISIA_DB` (database path), `ARTEMISIA_HOST`
(default `127.0.0.1`; set `0.0.0.0` to allow other devices on a trusted
network), `ARTEMISIA_PORT` (default `5876`).

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
