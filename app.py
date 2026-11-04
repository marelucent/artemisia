#!/usr/bin/env python3
"""
Artemisia — a private cycle tracker.

Named for Artemisia (mugwort, wormwood), the moon-herb of Artemis.
All data lives in a single SQLite file beside this script. No accounts,
no cloud, no subscriptions, no harassment.

Run:        python3 app.py
Import:     python3 app.py import /path/to/ClueDataDownload-folder
            python3 app.py import /path/to/measurements.json
"""
import bisect
import calendar as callib
import csv
import io
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from flask import (Flask, Response, g, jsonify, redirect, render_template,
                   request, send_file, url_for)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ARTEMISIA_DB", os.path.join(BASE_DIR, "artemisia.db"))
HOST = os.environ.get("ARTEMISIA_HOST", "127.0.0.1")
PORT = int(os.environ.get("ARTEMISIA_PORT", "5876"))

# ---------------------------------------------------------------- vocabulary
FLOWS = ["spotting", "light", "medium", "heavy", "very heavy"]
PERIOD_FLOWS = {"light", "medium", "heavy", "very heavy"}  # spotting ≠ period
SYMPTOMS = ["cramps", "headache", "backache", "tender breasts", "bloating",
            "nausea", "acne", "fatigue", "dizziness", "ovulation pain"]
MOODS = ["happy", "calm", "sensitive", "sad", "irritable", "anxious",
         "stressed", "unmotivated", "not in control", "dreamy"]
ENERGIES = ["exhausted", "tired", "ok", "energetic"]
DISCHARGES = ["dry", "sticky", "creamy", "egg white", "watery"]
EDITABLE = ("flow", "symptom", "mood", "energy", "discharge", "note")

# stats guard-rails: cycle lengths outside this range are shown but not
# used for averages/predictions
VALID_CYCLE = (15, 60)
RECENT_N = 6          # predictions use the mean of the last N valid cycles
LUTEAL = 14           # assumed luteal phase length for ovulation estimate

app = Flask(__name__)


@app.context_processor
def inject_globals():
    return {"now_year": date.today().year}


# ---------------------------------------------------------------- database
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date  TEXT NOT NULL,
        type  TEXT NOT NULL,
        value TEXT NOT NULL,
        UNIQUE(date, type, value)
    );
    CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date);
    CREATE INDEX IF NOT EXISTS idx_entries_type ON entries(type);
    CREATE TABLE IF NOT EXISTS excluded_cycles (
        date TEXT PRIMARY KEY        -- cycle start dates excluded from stats
    );
    """)
    conn.commit()


def open_db_direct():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


# ---------------------------------------------------------------- cycle maths
def period_runs(conn):
    """Consecutive bleeding runs (gap of 1 non-bleeding day tolerated)."""
    rows = conn.execute(
        "SELECT DISTINCT date FROM entries WHERE type='flow' AND value IN (%s) "
        "ORDER BY date" % ",".join("?" * len(PERIOD_FLOWS)),
        tuple(PERIOD_FLOWS)).fetchall()
    days = [date.fromisoformat(r["date"]) for r in rows]
    runs = []
    for d in days:
        if runs and (d - runs[-1][-1]).days <= 2:
            runs[-1].append(d)
        else:
            runs.append([d])
    return runs


def cycle_data(conn):
    """Starts, lengths, exclusions, run lengths."""
    runs = period_runs(conn)
    excluded = {r["date"] for r in conn.execute("SELECT date FROM excluded_cycles")}
    starts = [r[0] for r in runs]
    run_len = {r[0]: (r[-1] - r[0]).days + 1 for r in runs}
    cycles = []   # (start, length, valid)
    for a, b in zip(starts, starts[1:]):
        length = (b - a).days
        valid = (VALID_CYCLE[0] <= length <= VALID_CYCLE[1]
                 and a.isoformat() not in excluded
                 and b.isoformat() not in excluded)
        cycles.append((a, length, valid))
    return {"runs": runs, "starts": starts, "run_len": run_len,
            "cycles": cycles, "excluded": excluded}


def _pct(vals, q):
    s = sorted(vals)
    if not s:
        return None
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def phase_of(cd, length, plen):
    """Classify a cycle day. Ovulation assumed 14 days before next start."""
    ovu = max(plen + 3, length - LUTEAL)
    if cd <= plen:
        return "menstrual"
    if cd < ovu - 1:
        return "follicular"
    if cd <= ovu + 1:
        return "ovulatory"
    return "luteal"


def predictions(conn, horizon_days=420):
    cd = cycle_data(conn)
    starts, cycles = cd["starts"], cd["cycles"]
    if len(starts) < 2:
        return None
    valid_lengths = [c[1] for c in cycles if c[2]]
    if not valid_lengths:
        return None
    recent = valid_lengths[-RECENT_N:]
    avg = round(statistics.mean(recent))
    med = round(statistics.median(valid_lengths))
    plen_all = [cd["run_len"][s] for s in starts[-RECENT_N:]]
    plen = max(1, round(statistics.mean(plen_all)))
    last = starts[-1]
    pred_starts, k = [], 1
    while True:
        s = last + timedelta(days=avg * k)
        if (s - date.today()).days > horizon_days:
            break
        pred_starts.append(s)
        k += 1
        if k > 24:
            break
    period_days, fertile_days, ovulation_days = set(), set(), set()
    for s in pred_starts:
        for i in range(plen):
            period_days.add(s + timedelta(days=i))
        ovu = s - timedelta(days=LUTEAL)
        ovulation_days.add(ovu)
        for i in range(-5, 2):
            fertile_days.add(ovu + timedelta(days=i))
    today = date.today()
    cycle_day = (today - last).days + 1 if last <= today else None
    recent24 = valid_lengths[-24:]
    win = None
    if pred_starts and recent24:
        win = (last + timedelta(days=round(_pct(recent24, 0.10))),
               last + timedelta(days=round(_pct(recent24, 0.90))))
    phase = None
    if cycle_day:
        phase = phase_of(cycle_day, avg, plen) if cycle_day <= avg + 3 else "late"
    return {"avg": avg, "median": med, "period_len": plen,
            "next_start": pred_starts[0] if pred_starts else None,
            "pred_starts": pred_starts, "period_days": period_days,
            "fertile_days": fertile_days, "ovulation_days": ovulation_days,
            "cycle_day": cycle_day, "last_start": last,
            "n_recent": len(recent), "window": win, "phase": phase}


# ---------------------------------------------------------------- moon maths
SYNODIC = 29.53058867
NEW_MOON_REF = datetime(2000, 1, 6, 18, 14)  # a known new moon, UTC


def moon_quarter(d):
    """0=new, 1=first quarter, 2=full, 3=last quarter (which eighth-ish
    of the synodic month noon of this day falls in, coarsened to quarters)."""
    days = (datetime(d.year, d.month, d.day, 12) - NEW_MOON_REF).total_seconds() / 86400
    return int((days % SYNODIC) / SYNODIC * 4)


MOON_GLYPHS = {0: "\U0001F311", 1: "\U0001F313", 2: "\U0001F315", 3: "\U0001F317"}


def moon_mark(d):
    """Glyph if this day begins a new principal phase, else None."""
    q = moon_quarter(d)
    return MOON_GLYPHS[q] if q != moon_quarter(d - timedelta(days=1)) else None


# ---------------------------------------------------------------- calendar view
FLOW_RANK = {f: i for i, f in enumerate(FLOWS)}


def month_payload(year, month):
    conn = db()
    first = date(year, month, 1)
    last_day = callib.monthrange(year, month)[1]
    last = date(year, month, last_day)
    rows = conn.execute(
        "SELECT date, type, value FROM entries WHERE date BETWEEN ? AND ?",
        (first.isoformat(), last.isoformat())).fetchall()
    per_day = defaultdict(lambda: {"flow": None, "symptoms": [], "moods": [],
                                   "energy": None, "discharge": None,
                                   "note": False, "extra": False})
    for r in rows:
        d, t, v = r["date"], r["type"], r["value"]
        info = per_day[d]
        if t == "flow":
            if info["flow"] is None or FLOW_RANK[v] > FLOW_RANK[info["flow"]]:
                info["flow"] = v
        elif t == "symptom":
            info["symptoms"].append(v)
        elif t == "mood":
            info["moods"].append(v)
        elif t == "energy":
            info["energy"] = v
        elif t == "discharge":
            info["discharge"] = v
        elif t == "note":
            info["note"] = True
        else:
            info["extra"] = True
    pred = predictions(db())
    starts = cycle_data(db())["starts"]
    all_starts = sorted(set(starts) | set(pred["pred_starts"] if pred else []))
    pred_set = set(pred["pred_starts"]) if pred else set()

    def cycle_day_of(d):
        i = bisect.bisect_right(all_starts, d) - 1
        if i < 0:
            return None, False
        cd = (d - all_starts[i]).days + 1
        if cd > 99:
            return None, False
        return cd, (all_starts[i] in pred_set or d > date.today())

    weeks = []
    cal = callib.Calendar(firstweekday=0)  # Monday first
    for wk in cal.monthdatescalendar(year, month):
        row = []
        for d in wk:
            iso = d.isoformat()
            info = per_day.get(iso, None)
            cd, cd_pred = cycle_day_of(d)
            cell = {
                "date": iso, "day": d.day,
                "in_month": d.month == month,
                "today": d == date.today(),
                "future": d > date.today(),
                "flow": info["flow"] if info else None,
                "dots": [],
                "predicted": bool(pred and d in pred["period_days"] and d > date.today()),
                "fertile": bool(pred and d in pred["fertile_days"] and d >= date.today()),
                "ovulation": bool(pred and d in pred["ovulation_days"] and d >= date.today()),
                "moon": moon_mark(d),
                "cd": cd if d.month == month else None,
                "cd_pred": cd_pred,
            }
            if info:
                if info["symptoms"]:
                    cell["dots"].append("symptom")
                if info["moods"]:
                    cell["dots"].append("mood")
                if info["note"]:
                    cell["dots"].append("note")
                if info["extra"]:
                    cell["dots"].append("extra")
            row.append(cell)
        weeks.append(row)
    return weeks, pred


@app.route("/sw.js")
def service_worker():
    # served from root so the worker's scope covers the whole app
    return app.send_static_file("sw.js")


@app.route("/")
def home():
    t = date.today()
    target = url_for("calendar_view", year=t.year, month=t.month)
    if request.args.get("log"):
        target += "?log=today"
    return redirect(target)


@app.route("/calendar/<int:year>/<int:month>")
def calendar_view(year, month):
    if not (1 <= month <= 12):
        return redirect(url_for("home"))
    weeks, pred = month_payload(year, month)
    prev_m = (date(year, month, 1) - timedelta(days=1))
    next_m = (date(year, month, callib.monthrange(year, month)[1]) + timedelta(days=1))
    return render_template("calendar.html",
                           year=year, month=month,
                           month_name=date(year, month, 1).strftime("%B"),
                           weeks=weeks, pred=pred,
                           prev=(prev_m.year, prev_m.month),
                           next=(next_m.year, next_m.month),
                           flows=FLOWS, symptoms=SYMPTOMS, moods=MOODS,
                           energies=ENERGIES, discharges=DISCHARGES,
                           today=date.today().isoformat())


@app.route("/year/<int:year>")
def year_view(year):
    conn = db()
    rows = conn.execute(
        "SELECT date, value FROM entries WHERE type='flow' AND date BETWEEN ? AND ?",
        (f"{year}-01-01", f"{year}-12-31")).fetchall()
    flow = {}
    for r in rows:
        d, v = r["date"], r["value"]
        if d not in flow or FLOW_RANK[v] > FLOW_RANK[flow[d]]:
            flow[d] = v
    pred = predictions(conn)
    months = []
    cal = callib.Calendar(firstweekday=0)
    for m in range(1, 13):
        weeks = []
        for wk in cal.monthdatescalendar(year, m):
            row = []
            for d in wk:
                if d.month != m:
                    row.append(None)
                    continue
                f = flow.get(d.isoformat())
                row.append({
                    "day": d.day,
                    "flow": f,
                    "today": d == date.today(),
                    "predicted": bool(pred and d in pred["period_days"] and d > date.today()),
                })
            weeks.append(row)
        months.append({"n": m, "name": date(year, m, 1).strftime("%B"), "weeks": weeks})
    return render_template("year.html", year=year, months=months)


@app.route("/export/csv")
def export_csv():
    rows = db().execute(
        "SELECT date, type, value FROM entries ORDER BY date, type, value").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "type", "value"])
    for r in rows:
        w.writerow([r["date"], r["type"], r["value"]])
    return Response(buf.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition":
        f"attachment; filename=artemisia-export-{date.today().isoformat()}.csv"})


@app.route("/export/db")
def export_db():
    return send_file(DB_PATH, as_attachment=True, download_name="artemisia.db")


# ---------------------------------------------------------------- day API
@app.route("/api/day/<iso>", methods=["GET"])
def get_day(iso):
    date.fromisoformat(iso)  # validate
    rows = db().execute(
        "SELECT type, value FROM entries WHERE date=? ORDER BY type, id",
        (iso,)).fetchall()
    out = {"flow": None, "symptoms": [], "moods": [], "energy": None,
           "discharge": None, "note": "", "extras": []}
    notes = []
    for r in rows:
        t, v = r["type"], r["value"]
        if t == "flow":
            if out["flow"] is None or FLOW_RANK[v] > FLOW_RANK[out["flow"]]:
                out["flow"] = v
        elif t == "symptom":
            out["symptoms"].append(v)
        elif t == "mood":
            out["moods"].append(v)
        elif t == "energy":
            out["energy"] = v
        elif t == "discharge":
            out["discharge"] = v
        elif t == "note":
            notes.append(v)
        else:
            out["extras"].append({"type": t, "value": v})
    out["note"] = " · ".join(notes)
    return jsonify(out)


@app.route("/api/day/<iso>", methods=["POST"])
def save_day(iso):
    date.fromisoformat(iso)  # validate
    data = request.get_json(force=True)
    conn = db()
    conn.execute("DELETE FROM entries WHERE date=? AND type IN (%s)"
                 % ",".join("?" * len(EDITABLE)), (iso, *EDITABLE))

    def put(t, v):
        if v:
            conn.execute("INSERT OR IGNORE INTO entries(date,type,value) VALUES(?,?,?)",
                         (iso, t, v))

    flow = data.get("flow")
    if flow in FLOWS:
        put("flow", flow)
    for s in data.get("symptoms", []):
        put("symptom", str(s)[:60])
    for m in data.get("moods", []):
        put("mood", str(m)[:60])
    if data.get("energy") in ENERGIES:
        put("energy", data["energy"])
    if data.get("discharge") in DISCHARGES:
        put("discharge", data["discharge"])
    note = (data.get("note") or "").strip()
    if note:
        put("note", note[:2000])
    conn.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- insights
def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_cycle_lengths(cycles):
    """Cycle length over time. cycles = [(start, length, valid)]"""
    if not cycles:
        return ""
    W, H, PL, PR, PT, PB = 860, 300, 46, 16, 18, 42
    xs = [c[0].toordinal() for c in cycles]
    ys = [c[1] for c in cycles]
    x0, x1 = min(xs), max(xs)
    ylo = min(min(ys), VALID_CYCLE[0]) - 2
    yhi = max(max(ys), 40) + 2

    def X(x): return PL + (x - x0) / max(1, (x1 - x0)) * (W - PL - PR)
    def Y(y): return PT + (yhi - y) / max(1, (yhi - ylo)) * (H - PT - PB)
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img">']
    step = 5 if yhi - ylo > 15 else 2
    yv = (ylo // step) * step
    while yv <= yhi:
        if yv >= ylo:
            parts.append(f'<line x1="{PL}" y1="{Y(yv):.1f}" x2="{W-PR}" y2="{Y(yv):.1f}" class="grid"/>')
            parts.append(f'<text x="{PL-8}" y="{Y(yv)+4:.1f}" class="tick" text-anchor="end">{yv}</text>')
        yv += step
    years = sorted({c[0].year for c in cycles})
    for yr in years:
        o = date(yr, 1, 1).toordinal()
        if x0 <= o <= x1:
            parts.append(f'<line x1="{X(o):.1f}" y1="{PT}" x2="{X(o):.1f}" y2="{H-PB}" class="grid v"/>')
        parts.append(f'<text x="{X(max(o, x0)):.1f}" y="{H-PB+18}" class="tick">{yr}</text>')
    pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in zip(xs, ys))
    parts.append(f'<polyline points="{pts}" class="line"/>')
    for (s, ln, valid), x, y in zip(cycles, xs, ys):
        cls = "pt" if valid else "pt outlier"
        parts.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="3.4" class="{cls}">'
                     f'<title>{s.strftime("%d %b %Y")} — {ln} days'
                     f'{"" if valid else " (not counted)"}</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def svg_histogram(values):
    if not values:
        return ""
    W, H, PL, PB, PT = 860, 240, 46, 40, 14
    lo, hi = min(values), max(values)
    counts = Counter(values)
    n = hi - lo + 1
    bw = (W - PL - 16) / n
    mx = max(counts.values())
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img">']
    for i, v in enumerate(range(lo, hi + 1)):
        c = counts.get(v, 0)
        bh = (H - PB - PT) * c / mx
        x = PL + i * bw
        parts.append(f'<rect x="{x+1.5:.1f}" y="{H-PB-bh:.1f}" width="{bw-3:.1f}" '
                     f'height="{bh:.1f}" rx="3" class="bar">'
                     f'<title>{v} days × {c}</title></rect>')
        if n <= 30 or v % 5 == 0:
            parts.append(f'<text x="{x+bw/2:.1f}" y="{H-PB+16}" class="tick" '
                         f'text-anchor="middle">{v}</text>')
        if c:
            parts.append(f'<text x="{x+bw/2:.1f}" y="{H-PB-bh-5:.1f}" class="count" '
                         f'text-anchor="middle">{c}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_freq_bars(counter, color_class="bar"):
    if not counter:
        return ""
    items = counter.most_common(12)
    W, RH, PL = 860, 30, 150
    H = len(items) * RH + 10
    mx = items[0][1]
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img">']
    for i, (name, c) in enumerate(items):
        y = 5 + i * RH
        bw = (W - PL - 60) * c / mx
        parts.append(f'<text x="{PL-10}" y="{y+RH/2+4}" class="lbl" text-anchor="end">{esc(name)}</text>')
        parts.append(f'<rect x="{PL}" y="{y+5}" width="{bw:.1f}" height="{RH-12}" rx="6" '
                     f'class="{color_class}"><title>{esc(name)}: {c}</title></rect>')
        parts.append(f'<text x="{PL+bw+8:.1f}" y="{y+RH/2+4}" class="count">{c}</text>')
    parts.append("</svg>")
    return "".join(parts)


@app.route("/insights")
def insights():
    conn = db()
    cd = cycle_data(conn)
    pred = predictions(conn)
    cycles = cd["cycles"]
    valid = [c[1] for c in cycles if c[2]]
    total = conn.execute("SELECT COUNT(*) c FROM entries").fetchone()["c"]
    span = conn.execute("SELECT MIN(date) a, MAX(date) b FROM entries").fetchone()
    sym = Counter(r["value"] for r in conn.execute(
        "SELECT value FROM entries WHERE type='symptom'"))
    mood = Counter(r["value"] for r in conn.execute(
        "SELECT value FROM entries WHERE type='mood'"))
    plens = [cd["run_len"][s] for s in cd["starts"]]
    stats = {
        "total": total,
        "span": (date.fromisoformat(span["a"]).strftime("%b %Y") + " – " +
                 date.fromisoformat(span["b"]).strftime("%b %Y")) if span["a"] else "—",
        "n_cycles": len(cycles),
        "avg": pred["avg"] if pred else None,
        "median": pred["median"] if pred else None,
        "shortest": min(valid) if valid else None,
        "longest": max(valid) if valid else None,
        "period_len": round(statistics.mean(plens), 1) if plens else None,
        "cycle_day": pred["cycle_day"] if pred else None,
        "next_start": pred["next_start"].strftime("%A %d %B %Y") if pred and pred["next_start"] else None,
        "n_recent": pred["n_recent"] if pred else 0,
    }
    return render_template("insights.html", stats=stats,
                           chart_lengths=svg_cycle_lengths(cycles),
                           chart_hist=svg_histogram(valid),
                           chart_sym=svg_freq_bars(sym, "bar"),
                           chart_mood=svg_freq_bars(mood, "bar alt"))


# ---------------------------------------------------------------- almanac
import math


def _cycle_spans(cd_data):
    """Valid cycles as (start, length, plen)."""
    out = []
    for s, ln, valid in cd_data["cycles"]:
        if valid:
            out.append((s, ln, min(cd_data["run_len"].get(s, 4), ln)))
    return out


def phase_tables(conn):
    """Phase day-denominators and per-entry phase assignment for symptoms/moods."""
    cd_data = cycle_data(conn)
    spans = _cycle_spans(cd_data)
    phase_days = Counter()
    span_ix = [(s, s + timedelta(days=ln - 1), ln, plen) for s, ln, plen in spans]
    for s, e, ln, plen in span_ix:
        for i in range(1, ln + 1):
            phase_days[phase_of(i, ln, plen)] += 1
    rows = conn.execute(
        "SELECT date, type, value FROM entries WHERE type IN ('symptom','mood')").fetchall()
    per_item = defaultdict(lambda: Counter())
    dates_by_item = defaultdict(set)
    for r in rows:
        d = date.fromisoformat(r["date"])
        for s, e, ln, plen in span_ix:
            if s <= d <= e:
                cdd = (d - s).days + 1
                per_item[(r["type"], r["value"])][phase_of(cdd, ln, plen)] += 1
                dates_by_item[(r["type"], r["value"])].add(d)
                break
    return phase_days, per_item


def odds_ratios(conn, min_n=6):
    """For each symptom/mood with >= min_n classified days: best-enriched phase
    OR with 95% CI (Woolf, +0.5 continuity correction)."""
    phase_days, per_item = phase_tables(conn)
    total = sum(phase_days.values())
    out = []
    for (typ, name), cnt in per_item.items():
        n = sum(cnt.values())
        if n < min_n or total == 0:
            continue
        best = None
        for ph in ("menstrual", "follicular", "ovulatory", "luteal"):
            a = cnt.get(ph, 0)
            b = n - a
            c = phase_days[ph] - a
            dd = (total - phase_days[ph]) - b
            a2, b2, c2, d2 = a + .5, b + .5, c + .5, dd + .5
            orr = (a2 * d2) / (b2 * c2)
            se = math.sqrt(1/a2 + 1/b2 + 1/c2 + 1/d2)
            lo, hi = orr * math.exp(-1.96 * se), orr * math.exp(1.96 * se)
            if best is None or orr > best[1]:
                best = (ph, orr, lo, hi, a)
        out.append({"type": typ, "name": name, "n": n, "phase": best[0],
                    "or": best[1], "lo": best[2], "hi": best[3], "in_phase": best[4]})
    out.sort(key=lambda r: -r["or"])
    return out


def rayleigh(conn):
    """Circular test of period starts against the lunar cycle."""
    starts = cycle_data(conn)["starts"]
    n = len(starts)
    if n < 5:
        return None
    xs = ys = 0.0
    bins = [0] * 8
    for s in starts:
        days = (datetime(s.year, s.month, s.day, 12) - NEW_MOON_REF).total_seconds()/86400
        theta = (days % SYNODIC) / SYNODIC * 2 * math.pi
        xs += math.cos(theta)
        ys += math.sin(theta)
        bins[int((days % SYNODIC) / SYNODIC * 8) % 8] += 1
    r = math.sqrt(xs*xs + ys*ys) / n
    z = n * r * r
    p = math.exp(-z) * (1 + (2*z - z*z)/(4*n) - (24*z - 132*z*z + 76*z**3 - 9*z**4)/(288*n*n))
    p = max(0.0, min(1.0, p))
    mean_angle = math.atan2(ys, xs) % (2*math.pi)
    mean_day = mean_angle / (2*math.pi) * SYNODIC
    return {"n": n, "r": r, "z": z, "p": p, "bins": bins, "mean_day": mean_day}


def rolling_variability(conn, window=6):
    """Rolling mean/SD of valid cycle lengths + consecutive differences."""
    cd_data = cycle_data(conn)
    vc = [(s, ln) for s, ln, valid in cd_data["cycles"] if valid]
    roll = []
    for i in range(window - 1, len(vc)):
        seg = [ln for _, ln in vc[i - window + 1:i + 1]]
        roll.append((vc[i][0], statistics.mean(seg), statistics.stdev(seg)))
    diffs = [(vc[i][0], abs(vc[i][1] - vc[i-1][1])) for i in range(1, len(vc))]
    recent = [d for _, d in diffs[-10:]]
    early = [d for _, d in diffs[:max(1, len(diffs)//3)]]
    summary = {
        "recent_big": sum(1 for d in recent if d >= 7),
        "early_big_rate": (sum(1 for d in early if d >= 7) / len(early)) if early else 0,
        "recent_sd": roll[-1][2] if roll else None,
        "recent_mean": roll[-1][1] if roll else None,
    }
    return roll, diffs, summary


def seasonality(conn):
    cd_data = cycle_data(conn)
    by_month = defaultdict(list)
    for s, ln, valid in cd_data["cycles"]:
        if valid:
            by_month[s.month].append(ln)
    return [(m, statistics.mean(by_month[m]), len(by_month[m]))
            for m in range(1, 13) if by_month.get(m)]


# ------- almanac SVG builders -------
def svg_hormones(length, plen, cd):
    """Schematic oestradiol / progesterone / LH curves across one cycle."""
    W, H, PL, PB, PT = 860, 260, 40, 30, 16
    ovu = max(plen + 3, length - LUTEAL)

    def X(day): return PL + (day - 1) / max(1, length - 1) * (W - PL - 16)
    def Y(v): return PT + (1 - v) * (H - PT - PB)

    def curve(fn, cls, dash=""):
        pts = " ".join(f"{X(d):.1f},{Y(fn(d)):.1f}"
                       for d in [1 + i * (length - 1) / 199 for i in range(200)])
        return f'<polyline points="{pts}" class="{cls}" {dash} fill="none"/>'

    def smooth(d, peaks):
        v = 0.08
        for centre, width, height in peaks:
            v += height * math.exp(-((d - centre) ** 2) / (2 * width ** 2))
        return min(v, 1.0)

    oest = lambda d: smooth(d, [(ovu - 1.5, 3.2, 0.85), (ovu + (length - ovu) * .55, 5.0, 0.42)])
    prog = lambda d: smooth(d, [(ovu + (length - ovu) * .5, 4.2, 0.82)])
    lh = lambda d: smooth(d, [(ovu - 1, 0.8, 0.92)])
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
    for ph, x0, x1 in [("menstrual", 1, plen), ("follicular", plen, ovu - 1),
                       ("ovulatory", ovu - 1, ovu + 1), ("luteal", ovu + 1, length)]:
        parts.append(f'<rect x="{X(x0):.1f}" y="{PT}" width="{X(x1)-X(x0):.1f}" '
                     f'height="{H-PT-PB}" class="ph-{ph}"/>')
        parts.append(f'<text x="{(X(x0)+X(x1))/2:.1f}" y="{H-10}" class="tick" '
                     f'text-anchor="middle">{ph}</text>')
    parts.append(curve(oest, "h-oest"))
    parts.append(curve(prog, "h-prog"))
    parts.append(curve(lh, "h-lh", 'stroke-dasharray="5 4"'))
    if cd and 1 <= cd <= length:
        parts.append(f'<line x1="{X(cd):.1f}" y1="{PT}" x2="{X(cd):.1f}" y2="{H-PB}" class="h-today"/>')
        parts.append(f'<text x="{X(cd)+5:.1f}" y="{PT+14}" class="lbl">today · day {cd}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_forest(rows):
    if not rows:
        return ""
    W, RH, PL = 860, 46, 210
    H = len(rows) * RH + 30
    ors = [v for r in rows for v in (r["lo"], r["hi"])]
    xmax = max(4.0, max(ors)) * 1.15
    xmin = min(0.25, min(ors)) / 1.15

    def X(v): return PL + (math.log(v) - math.log(xmin)) / (math.log(xmax) - math.log(xmin)) * (W - PL - 30)
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
    parts.append(f'<line x1="{X(1):.1f}" y1="8" x2="{X(1):.1f}" y2="{H-22}" class="grid"/>')
    for tick in (0.5, 1, 2, 4, 8, 16):
        if xmin < tick < xmax:
            parts.append(f'<text x="{X(tick):.1f}" y="{H-6}" class="tick" text-anchor="middle">{tick}×</text>')
    for i, r in enumerate(rows):
        y = 24 + i * RH
        col = "var(--rose-deep)" if r["type"] == "symptom" else "#8f7fae"
        parts.append(f'<text x="{PL-12}" y="{y+4}" class="lbl" text-anchor="end">{esc(r["name"])}'
                     f' <tspan class="tick">({r["phase"]}, n={r["n"]})</tspan></text>')
        parts.append(f'<line x1="{X(r["lo"]):.1f}" y1="{y}" x2="{X(r["hi"]):.1f}" y2="{y}" '
                     f'stroke="{col}" stroke-width="2"/>')
        parts.append(f'<circle cx="{X(r["or"]):.1f}" cy="{y}" r="5" fill="{col}">'
                     f'<title>{esc(r["name"])}: OR {r["or"]:.1f} (95% CI {r["lo"]:.1f}–{r["hi"]:.1f}) '
                     f'for {r["phase"]} phase; {r["in_phase"]}/{r["n"]} logged days in that phase</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def svg_rose(bins):
    W = H = 340
    cx, cy, R = W/2, H/2, 130
    mx = max(bins) or 1
    labels = ["new", "", "first ¼", "", "full", "", "last ¼", ""]
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
    for ring in (0.33, 0.66, 1.0):
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{R*ring:.1f}" class="grid" fill="none"/>')
    for i, cnt in enumerate(bins):
        a0 = (i / 8) * 2 * math.pi - math.pi / 2 - math.pi / 8
        a1 = a0 + 2 * math.pi / 8
        rr = R * math.sqrt(cnt / mx)
        x0, y0 = cx + rr * math.cos(a0), cy + rr * math.sin(a0)
        x1, y1 = cx + rr * math.cos(a1), cy + rr * math.sin(a1)
        parts.append(f'<path d="M{cx},{cy} L{x0:.1f},{y0:.1f} A{rr:.1f},{rr:.1f} 0 0 1 '
                     f'{x1:.1f},{y1:.1f} Z" class="petal"><title>{cnt} starts</title></path>')
        lx = cx + (R + 18) * math.cos((a0 + a1) / 2)
        ly = cy + (R + 18) * math.sin((a0 + a1) / 2)
        if labels[i]:
            parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="tick" text-anchor="middle">{labels[i]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_rolling(roll, diffs):
    if not roll:
        return ""
    W, H, PL, PB, PT = 860, 300, 46, 42, 16
    xs = [r[0].toordinal() for r in roll]
    x0, x1 = min(xs), max(xs)
    ylo = min(r[1] - r[2] for r in roll) - 2
    yhi = max(r[1] + r[2] for r in roll) + 2

    def X(x): return PL + (x - x0) / max(1, x1 - x0) * (W - PL - 16)
    def Y(y): return PT + (yhi - y) / max(1, yhi - ylo) * (H - PT - PB)
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
    for yv in range(int(ylo), int(yhi) + 1):
        if yv % 5 == 0:
            parts.append(f'<line x1="{PL}" y1="{Y(yv):.1f}" x2="{W-16}" y2="{Y(yv):.1f}" class="grid"/>')
            parts.append(f'<text x="{PL-8}" y="{Y(yv)+4:.1f}" class="tick" text-anchor="end">{yv}</text>')
    years = sorted({r[0].year for r in roll})
    for yr in years:
        o = date(yr, 1, 1).toordinal()
        if x0 <= o <= x1:
            parts.append(f'<text x="{X(o):.1f}" y="{H-PB+18}" class="tick">{yr}</text>')
    band_top = " ".join(f"{X(x):.1f},{Y(m+s):.1f}" for (x, (d, m, s)) in zip(xs, roll))
    band_bot = " ".join(f"{X(x):.1f},{Y(m-s):.1f}" for (x, (d, m, s)) in reversed(list(zip(xs, roll))))
    parts.append(f'<polygon points="{band_top} {band_bot}" class="band"/>')
    mean_line = " ".join(f"{X(x):.1f},{Y(m):.1f}" for (x, (d, m, s)) in zip(xs, roll))
    parts.append(f'<polyline points="{mean_line}" class="line" fill="none"/>')
    for d, gap in diffs:
        if gap >= 7 and x0 <= d.toordinal() <= x1:
            parts.append(f'<circle cx="{X(d.toordinal()):.1f}" cy="{PT+8}" r="4" class="pt outlier">'
                         f'<title>{d.strftime("%b %Y")}: {gap}-day jump between consecutive cycles</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def svg_seasonal(rows):
    if not rows:
        return ""
    W, H, PL, PB, PT = 860, 240, 46, 40, 16
    means = [m for _, m, _ in rows]
    ylo, yhi = min(means) - 1, max(means) + 1
    monthname = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def X(m): return PL + (m - 1) / 11 * (W - PL - 26)
    def Y(v): return PT + (yhi - v) / max(.5, yhi - ylo) * (H - PT - PB)
    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
    for yv in range(int(ylo), int(yhi) + 1):
        parts.append(f'<line x1="{PL}" y1="{Y(yv):.1f}" x2="{W-26}" y2="{Y(yv):.1f}" class="grid"/>')
        parts.append(f'<text x="{PL-8}" y="{Y(yv)+4:.1f}" class="tick" text-anchor="end">{yv}</text>')
    pts = " ".join(f"{X(m):.1f},{Y(v):.1f}" for m, v, _ in rows)
    parts.append(f'<polyline points="{pts}" class="line" fill="none"/>')
    for m, v, n in rows:
        parts.append(f'<circle cx="{X(m):.1f}" cy="{Y(v):.1f}" r="4" class="pt">'
                     f'<title>{monthname[m]}: mean {v:.1f} days (n={n})</title></circle>')
        parts.append(f'<text x="{X(m):.1f}" y="{H-PB+18}" class="tick" text-anchor="middle">{monthname[m]}</text>')
    parts.append("</svg>")
    return "".join(parts)


PHASE_POETRY = {
    "menstrual": ("the letting", "the lining releases; oestradiol and progesterone at their quietest"),
    "follicular": ("the rising", "follicles recruit, oestradiol climbs, the lining rebuilds"),
    "ovulatory": ("the crest", "the LH surge breaks; an egg sets sail"),
    "luteal": ("the keeping", "the corpus luteum holds progesterone high, then lets go"),
    "late": ("the threshold", "past the usual length — she may be gathering herself"),
}


@app.route("/almanac")
def almanac():
    conn = db()
    pred = predictions(conn)
    ors = odds_ratios(conn)
    ray = rayleigh(conn)
    roll, diffs, vsum = rolling_variability(conn)
    seas = seasonality(conn)
    hormones = ""
    compass = None
    if pred and pred["cycle_day"]:
        hormones = svg_hormones(pred["avg"], pred["period_len"], pred["cycle_day"])
        ph = pred["phase"]
        compass = {"day": pred["cycle_day"], "phase": ph,
                   "poem": PHASE_POETRY.get(ph, ("", ""))}
    window = None
    if pred and pred["window"]:
        window = (pred["window"][0].strftime("%A %d %B"),
                  pred["window"][1].strftime("%A %d %B"),
                  pred["next_start"].strftime("%A %d %B"))
    return render_template("almanac.html",
                           compass=compass, hormones=hormones, window=window,
                           ors=ors, chart_forest=svg_forest(ors),
                           ray=ray, chart_rose=svg_rose(ray["bins"]) if ray else "",
                           chart_roll=svg_rolling(roll, diffs), vsum=vsum,
                           chart_seasonal=svg_seasonal(seas))


# ---------------------------------------------------------------- Clue import
CLUE_PAIN = {"period_cramps": "cramps", "breast_tenderness": "tender breasts",
             "ovulation": "ovulation pain"}


def _clue_values(entry):
    """Yield plain string values from a Clue measurement's value field."""
    v = entry.get("value")
    items = v if isinstance(v, list) else [v]
    for item in items:
        if isinstance(item, dict):
            if "option" in item:
                yield str(item["option"])
            elif "text" in item:
                yield str(item["text"])
            elif "minutes" in item:
                yield f'{item["minutes"]} min'
            else:
                yield json.dumps(item)
        elif item is not None:
            yield str(item)


def map_clue(entry):
    """Map one Clue measurement to a list of (type, value) rows."""
    t = entry.get("type", "unknown")
    rows = []
    for raw in _clue_values(entry):
        v = raw.replace("_", " ").strip()
        if t == "period":
            rows.append(("flow", v))
        elif t == "spotting":
            rows.append(("flow", "spotting"))
        elif t == "pain":
            rows.append(("symptom", CLUE_PAIN.get(raw, v)))
        elif t in ("feelings", "emotion", "mood", "mind"):
            rows.append(("mood", v))
        elif t == "energy":
            rows.append(("energy", v))
        elif t == "discharge":
            rows.append(("discharge", v))
        elif t == "skin" and raw == "acne":
            rows.append(("symptom", "acne"))
        elif t == "digestion" and raw in ("bloated", "nauseated"):
            rows.append(("symptom", {"bloated": "bloating", "nauseated": "nausea"}[raw]))
        elif t == "notes":
            rows.append(("note", raw))
        elif t == "sleep_duration":
            rows.append(("sleep", v))
        else:
            rows.append((t.replace("_", " "), v))
    return rows


def import_clue(path, conn=None):
    """Import a measurements.json file, a folder containing one, or any
    Clue-shaped JSON list. Returns a Counter of imported rows by type."""
    own = conn is None
    if own:
        conn = open_db_direct()
    counts, skipped = Counter(), 0
    mpath, cpath = path, None
    if os.path.isdir(path):
        mpath = os.path.join(path, "measurements.json")
        c = os.path.join(path, "cycle_attributes.json")
        cpath = c if os.path.exists(c) else None
    with open(mpath, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):  # tolerate wrapped exports
        for key in ("measurements", "data", "entries"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError("Could not find a list of measurements in this file.")
    for entry in data:
        iso = str(entry.get("date") or entry.get("day") or "")[:10]
        try:
            date.fromisoformat(iso)
        except ValueError:
            skipped += 1
            continue
        for t, v in map_clue(entry):
            cur = conn.execute(
                "INSERT OR IGNORE INTO entries(date,type,value) VALUES(?,?,?)",
                (iso, t, v))
            if cur.rowcount:
                counts[t] += 1
    # a day with true bleeding shouldn't also carry imported 'spotting'
    conn.execute("""DELETE FROM entries WHERE type='flow' AND value='spotting'
                    AND date IN (SELECT date FROM entries
                                 WHERE type='flow' AND value!='spotting')""")
    if cpath:
        with open(cpath, encoding="utf-8") as f:
            attrs = json.load(f)
        for a in attrs if isinstance(attrs, list) else []:
            sd = str(a.get("startDate") or "")[:10]
            if not sd:
                continue
            if a.get("note"):
                cur = conn.execute(
                    "INSERT OR IGNORE INTO entries(date,type,value) VALUES(?,?,?)",
                    (sd, "note", str(a["note"])))
                if cur.rowcount:
                    counts["note"] += 1
            if a.get("excluded"):
                conn.execute("INSERT OR IGNORE INTO excluded_cycles(date) VALUES(?)", (sd,))
                counts["excluded cycle"] += 1
    conn.commit()
    if own:
        conn.close()
    return counts, skipped


@app.route("/import", methods=["GET", "POST"])
def import_view():
    result = error = None
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            error = "Choose a file first."
        else:
            tmp = os.path.join(BASE_DIR, "_upload.json")
            f.save(tmp)
            try:
                conn = db()
                init_db(conn)
                counts, skipped = import_clue(tmp, conn)
                result = {"counts": dict(counts.most_common()), "skipped": skipped,
                          "total": sum(counts.values())}
            except Exception as e:
                error = f"Import failed: {e}"
            finally:
                os.remove(tmp)
    return render_template("import.html", result=result, error=error)


# ---------------------------------------------------------------- entry point
def main():
    conn = open_db_direct()
    conn.close()
    if len(sys.argv) >= 3 and sys.argv[1] == "import":
        counts, skipped = import_clue(sys.argv[2])
        print("Imported:")
        for t, c in counts.most_common():
            print(f"  {t:<16} {c}")
        print(f"  (skipped {skipped} undated entries)" if skipped else "", end="")
        print(f"\nTotal rows added: {sum(counts.values())}")
        return
    print(f"Artemisia unfurling at http://{HOST}:{PORT}  (db: {DB_PATH})")
    app.run(host=HOST, port=PORT, debug=False)


if __name__ == "__main__":
    main()
