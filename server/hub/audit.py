"""Append-only record of what was done through the hub, and by whom.

Phase 0 only has chat turns and refusals to record; from phase 2 on every
action that changes something (restarting a service, writing a file, sending a
message) goes through here, so "what happened on the server last night" always
has an answer.
"""
import sqlite3
import threading
import time

from . import config

_lock = threading.Lock()
_ready = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY,
    ts     REAL    NOT NULL,
    login  TEXT    NOT NULL,
    action TEXT    NOT NULL,
    detail TEXT    NOT NULL DEFAULT '',
    ok     INTEGER NOT NULL DEFAULT 1
)"""


def _connect():
    conn = sqlite3.connect(config.HUB_DB)
    if config.HUB_DB not in _ready:
        conn.execute(_SCHEMA)
        _ready.add(config.HUB_DB)
    return conn


def record(login, action, detail="", ok=True):
    # Never let bookkeeping fail the request it is recording.
    try:
        with _lock, _connect() as conn:
            conn.execute(
                "INSERT INTO audit (ts, login, action, detail, ok) VALUES (?, ?, ?, ?, ?)",
                (time.time(), login or "-", action, detail[:500], 1 if ok else 0),
            )
    except sqlite3.Error as e:
        print(f"[AUDIT] {e}")


def recent(limit=50):
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT ts, login, action, detail, ok FROM audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(zip(("ts", "login", "action", "detail", "ok"), r)) for r in rows]
