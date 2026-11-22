#!/usr/bin/env python3
"""
Artemisia MCP — a read-only window onto the cycle tracker, for Claudes.
=======================================================================

Speaks MCP (streamable HTTP, JSON-RPC) using only Flask + stdlib, so it
runs on Python 3.9+ with no new dependencies. Imports Artemisia's
own functions — cycle logic lives in ONE place (app.py); this just asks her.

The database is opened READ-ONLY (sqlite mode=ro). There are no writing
tools. Logging still happens in Artemisia herself.

Run on your server, from Artemisia's folder:

    cd /path/to/artemisia
    nohup python3 artemisia_mcp.py > mcp.log 2>&1 &      # port 5877

Expose over the tailnet WITHOUT touching the existing serve config:

    tailscale serve --bg --https=8443 http://127.0.0.1:5877

Then in the Claude app: Settings -> Connectors -> Add custom connector ->
URL: https://YOUR-MACHINE.YOUR-TAILNET.ts.net:8443/mcp
(Reachable only on your own tailnet. Nothing public.)
"""

import os
import sys
import json
import sqlite3
import datetime as dt

from flask import Flask, request, jsonify

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app as artemisia  # her own moon maths; import must not start Flask

DB_PATH = os.environ.get("ARTEMISIA_DB", os.path.join(HERE, "artemisia.db"))
PORT = int(os.environ.get("ARTEMISIA_MCP_PORT", "5877"))

srv = Flask("artemisia-mcp")


def ro_conn():
    conn = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def iso(x):
    return x.isoformat() if hasattr(x, "isoformat") else x


# ── the tools ───────────────────────────────────────────────────────────────
def t_cycle_status():
    """Where in the cycle today sits, per Artemisia's own predictions."""
    conn = ro_conn()
    p = artemisia.predictions(conn)
    conn.close()
    if not p:
        return {"status": "not enough data for predictions yet"}
    win = p.get("window")
    return {
        "today": dt.date.today().isoformat(),
        "cycle_day": p["cycle_day"],
        "phase": p["phase"],
        "last_period_start": iso(p["last_start"]),
        "predicted_next_start": iso(p["next_start"]),
        "likely_window_80pct": [iso(win[0]), iso(win[1])] if win else None,
        "avg_cycle_recent": p["avg"],
        "median_cycle_all": p["median"],
        "typical_period_length_days": p["period_len"],
        "based_on_recent_cycles": p["n_recent"],
        "honesty": ("Estimates from your own history. The window is an "
                    "80% interval, not a promise. Ovulation is assumed 14 days "
                    "before next start - a compass, not a measurement."),
    }


def t_recent_log(days=14):
    """Everything logged in the last N days, grouped by date."""
    days = max(1, min(int(days), 120))
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    conn = ro_conn()
    rows = conn.execute(
        "SELECT date, type, value FROM entries WHERE date >= ? "
        "ORDER BY date DESC, type", (since,)).fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["date"], []).append({"type": r["type"], "value": r["value"]})
    return {"since": since, "days_with_entries": len(out), "log": out}


def t_cycle_history(n=12):
    """The last N cycles: start, length, whether stats count them."""
    n = max(1, min(int(n), 130))
    conn = ro_conn()
    cd = artemisia.cycle_data(conn)
    conn.close()
    cycles = [{"start": iso(a), "length_days": ln, "counted_in_stats": ok}
              for a, ln, ok in cd["cycles"][-n:]]
    return {"cycles": cycles,
            "current_cycle_started": iso(cd["starts"][-1]) if cd["starts"] else None}


def t_symptom_summary(entry_type="symptom", days=365):
    """Frequency of logged values for one type (symptom, mood, energy...)."""
    days = max(1, min(int(days), 4000))
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    conn = ro_conn()
    rows = conn.execute(
        "SELECT value, COUNT(*) AS n FROM entries WHERE type = ? AND date >= ? "
        "GROUP BY value ORDER BY n DESC", (entry_type, since)).fetchall()
    types = [r["type"] for r in conn.execute(
        "SELECT DISTINCT type FROM entries ORDER BY type")]
    conn.close()
    return {"type": entry_type, "since": since,
            "counts": {r["value"]: r["n"] for r in rows},
            "available_types": types}


TOOLS = {
    "cycle_status": {
        "fn": lambda a: t_cycle_status(),
        "description": ("Current cycle position from Artemisia (your "
                        "self-hosted tracker): cycle day, phase, last period "
                        "start, predicted next period with an honest 80% "
                        "window, and typical lengths."),
        "schema": {"type": "object", "properties": {}},
    },
    "recent_log": {
        "fn": lambda a: t_recent_log(a.get("days", 14)),
        "description": ("Recent diary entries (flow, symptoms, mood, energy, "
                        "sleep, notes...) grouped by date. days: 1-120, "
                        "default 14."),
        "schema": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "lookback window, default 14"}}},
    },
    "cycle_history": {
        "fn": lambda a: t_cycle_history(a.get("n", 12)),
        "description": ("Last n cycles with start dates and lengths, plus "
                        "which are counted in statistics. n: 1-130, default 12."),
        "schema": {"type": "object", "properties": {
            "n": {"type": "integer", "description": "how many cycles, default 12"}}},
    },
    "symptom_summary": {
        "fn": lambda a: t_symptom_summary(a.get("entry_type", "symptom"),
                                          a.get("days", 365)),
        "description": ("Frequency table for one entry type over a period. "
                        "entry_type e.g. symptom, mood, energy, sleep; "
                        "days default 365. Also lists all available types."),
        "schema": {"type": "object", "properties": {
            "entry_type": {"type": "string", "description": "default 'symptom'"},
            "days": {"type": "integer", "description": "default 365"}}},
    },
}


# ── minimal MCP over streamable HTTP ────────────────────────────────────────
PROTOCOL = "2025-03-26"


def rpc_result(id_, result):
    return jsonify({"jsonrpc": "2.0", "id": id_, "result": result})


def rpc_error(id_, code, message):
    return jsonify({"jsonrpc": "2.0", "id": id_,
                    "error": {"code": code, "message": message}})


@srv.route("/mcp", methods=["POST"])
@srv.route("/", methods=["POST"])
def mcp():
    msg = request.get_json(force=True, silent=True)
    if msg is None:
        return rpc_error(None, -32700, "parse error"), 400
    method = msg.get("method", "")
    id_ = msg.get("id")

    if id_ is None:  # notification (e.g. notifications/initialized)
        return ("", 202)

    if method == "initialize":
        client_proto = (msg.get("params") or {}).get("protocolVersion", PROTOCOL)
        return rpc_result(id_, {
            "protocolVersion": client_proto,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "artemisia", "version": "1.0.0"},
        })
    if method == "ping":
        return rpc_result(id_, {})
    if method == "tools/list":
        return rpc_result(id_, {"tools": [
            {"name": k, "description": v["description"], "inputSchema": v["schema"]}
            for k, v in TOOLS.items()]})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOLS.get(name)
        if not tool:
            return rpc_error(id_, -32602, "unknown tool: %s" % name)
        try:
            result = tool["fn"](args)
            return rpc_result(id_, {
                "content": [{"type": "text",
                             "text": json.dumps(result, ensure_ascii=False, indent=1)}],
                "isError": False})
        except Exception as e:
            return rpc_result(id_, {
                "content": [{"type": "text", "text": "tool error: %s" % e}],
                "isError": True})
    return rpc_error(id_, -32601, "method not found: %s" % method)


@srv.route("/mcp", methods=["GET", "DELETE"])
def no_stream():
    # streamable HTTP: we offer no server-initiated stream; clients handle 405
    return ("", 405)


@srv.route("/", methods=["GET"])
def hello():
    return jsonify({"service": "artemisia-mcp", "transport": "streamable-http",
                    "tools": list(TOOLS), "note": "POST JSON-RPC to /mcp"})


if __name__ == "__main__":
    print("Artemisia MCP listening on 127.0.0.1:%d (db: %s, read-only)" % (PORT, DB_PATH))
    srv.run(host="127.0.0.1", port=PORT)
