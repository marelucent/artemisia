#!/usr/bin/env python3
"""
Artemisia's nudges: gentle, cycle-aware reminders to log a moment.

Run every 15 minutes (cron or launchd). Each run decides whether this is a
nudge time, and if so sends one short notification through Home Assistant.

  bleeding  ~11:00, 15:00, 19:30   "How's the ache?"
  run-up    ~12:30, 20:00          (about 10 days before the predicted start,
                                    or late)  "Any heat or cravings today?"
  otherwise ~20:00 every other day "How's your energy?"

It stays quiet if you've logged anything in the last 3 hours, outside
09:00-21:30, while paused ("not today") or switched off at /nudges. If three
nudges in a row go unanswered it keeps only the evening one until you log
again. It never writes to the database.

Settings live beside the database:
  ha.json      {"url": ..., "token": ...,
                "service": "notify/mobile_app_yourphone"   (or a script, e.g.
                "script/send_to_phone" with "extra": {"who": "me"})}
  nudges.json  on/off and pause (written by the /nudges page)
  nudges_state.json  what was sent when (written here)

  python3 nudges.py           normal run
  python3 nudges.py --dry     say what it would do, send nothing
  python3 nudges.py --test    send one test nudge now
"""
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app as artemisia  # noqa: E402  (cycle maths live in one place)

DB = artemisia.DB_PATH
BASE = os.path.dirname(DB)
HA_PATH = os.path.join(BASE, "ha.json")
SETTINGS = os.path.join(BASE, "nudges.json")
STATE = os.path.join(BASE, "nudges_state.json")
OPEN_URL = os.environ.get("ARTEMISIA_URL", "http://localhost:5876").rstrip("/") + "/?log=moment"

DAY_START, DAY_END = (9, 0), (21, 30)
SLOTS = {"bleed": ["11:00", "15:00", "19:30"],
         "runup": ["12:30", "20:00"],
         "quiet": ["20:00"]}
RUNUP_DAYS = 10
WINDOW_MIN = 60          # a slot can fire up to an hour late (sleeping Mac, etc.)
RECENT_HOURS = 3         # logged this recently -> no nudge
BACKOFF_AFTER = 3        # unanswered in a row -> evening only
WORDS = {
    "bleed": ["How's the ache?", "How are you holding up?",
              "Anything shifting in the body?"],
    "runup": ["Any heat or cravings today?", "Sensing the turn?",
              "How's the body feeling today?"],
    "quiet": ["How's your energy?", "A moment for the moon?"],
}


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def ro():
    conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def mode_for(conn, today):
    """('bleed'|'runup'|'quiet', cycle_day or None)."""
    pred = artemisia.predictions(conn)
    cd = pred["cycle_day"] if pred else None
    flows = tuple(artemisia.PERIOD_FLOWS)
    recent = conn.execute(
        "SELECT 1 FROM entries WHERE type='flow' AND value IN (%s) AND date IN (?, ?)"
        % ",".join("?" * len(flows)),
        (*flows, today.isoformat(), (today - timedelta(days=1)).isoformat())).fetchone()
    if recent:
        return "bleed", cd
    if pred and pred["next_start"]:
        days_to = (pred["next_start"] - today).days
        if -21 <= days_to <= RUNUP_DAYS:
            return "runup", cd
    return "quiet", cd


def log_times(conn, since):
    """Times (datetime) anything was logged since `since`."""
    out = []
    for sql in ("SELECT logged_at FROM journal WHERE logged_at >= ?",
                "SELECT logged_at FROM moments WHERE logged_at >= ?"):
        try:
            out += [datetime.fromisoformat(r[0]) for r in conn.execute(sql, (since.isoformat(),))]
        except sqlite3.OperationalError:
            pass
    return out


def send(title, message):
    ha = load(HA_PATH, None)
    if not ha or not ha.get("token"):
        raise SystemExit("no ha.json with a token beside the database")
    service = ha.get("service", "")
    if not service:
        raise SystemExit("ha.json needs a 'service', e.g. notify/mobile_app_yourphone")
    if service.startswith("notify/"):
        body = {"title": title, "message": message,
                "data": {"clickAction": OPEN_URL, "url": OPEN_URL}}
    else:
        body = dict(ha.get("extra", {}), title=title, message=message, link=OPEN_URL)
    req = urllib.request.Request(
        ha["url"].rstrip("/") + "/api/services/" + service, method="POST",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + ha["token"],
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def main():
    dry = "--dry" in sys.argv
    now = (datetime.fromisoformat(os.environ["NUDGE_NOW"]) if os.environ.get("NUDGE_NOW")  # for testing
           else datetime.now()).replace(second=0, microsecond=0)
    today = now.date()
    if "--test" in sys.argv:
        print("test nudge:", send("\U0001F319 Artemisia", "A test nudge. Tap to log a moment."))
        return

    settings = load(SETTINGS, {"enabled": True, "paused_until": ""})
    if not settings.get("enabled", True):
        return print("nudges are switched off")
    if settings.get("paused_until", "") >= today.isoformat():
        return print("paused for today")
    if not (DAY_START <= (now.hour, now.minute) <= DAY_END):
        return print("outside nudging hours")

    conn = ro()
    mode, cd = mode_for(conn, today)
    slots = SLOTS[mode]
    if mode == "quiet" and today.toordinal() % 2:
        return print("quiet phase, not a nudge day")

    state = load(STATE, {"sent": [], "done": {}})
    done = state["done"].get(today.isoformat(), [])
    due = None
    for s in slots:
        h, m = map(int, s.split(":"))
        t = datetime.combine(today, datetime.min.time()).replace(hour=h, minute=m)
        if t <= now < t + timedelta(minutes=WINDOW_MIN) and s not in done:
            due = s
            break
    if not due:
        return print("%s phase: no slot due now" % mode)

    def mark(why):
        if not dry:
            state["done"] = {k: v for k, v in state["done"].items()
                             if k >= (today - timedelta(days=7)).isoformat()}
            state["done"].setdefault(today.isoformat(), []).append(due)
            save(STATE, state)
        print("%s %s: %s" % (mode, due, why))

    logs = log_times(conn, now - timedelta(days=4))
    if any(t >= now - timedelta(hours=RECENT_HOURS) for t in logs):
        return mark("skipped, logged recently")

    recent_sent = [datetime.fromisoformat(s) for s in state["sent"][-BACKOFF_AFTER:]]
    unanswered = len(recent_sent) == BACKOFF_AFTER and all(
        not any(s <= t <= s + timedelta(hours=RECENT_HOURS) for t in logs)
        for s in recent_sent)
    if unanswered and due != slots[-1]:
        return mark("skipped, backing off (last %d unanswered)" % BACKOFF_AFTER)

    words = WORDS[mode][today.toordinal() % len(WORDS[mode])]
    title = "\U0001F319 Artemisia" + (" · day %d" % cd if mode == "bleed" and cd else "")
    if dry:
        return print("%s %s: WOULD SEND %r / %r" % (mode, due, title, words))
    status = send(title, words)
    state["sent"] = (state["sent"] + [now.isoformat()])[-20:]
    mark("sent (%s) %r" % (status, words))


if __name__ == "__main__":
    main()
