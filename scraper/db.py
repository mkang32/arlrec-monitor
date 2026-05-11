"""SQLite snapshot store. The file lives at data/snapshots.sqlite and is committed to the repo."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sections (
    fmid TEXT PRIMARY KEY,
    activity_code TEXT,
    name TEXT,
    type_code TEXT,
    reg_event TEXT,
    date_start TEXT,
    date_end TEXT,
    time_start TEXT,
    time_end TEXT,
    days TEXT,
    location TEXT,
    ages TEXT,
    cost TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    fmid TEXT NOT NULL,
    ts TEXT NOT NULL,
    status TEXT NOT NULL,
    enrolled INTEGER,
    waitlist INTEGER,
    PRIMARY KEY (fmid, ts)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_fmid ON snapshots(fmid);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);

CREATE TABLE IF NOT EXISTS alerts_sent (
    fmid TEXT NOT NULL,
    prev_status TEXT,
    new_status TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (fmid, new_status, ts)
);
"""


@contextmanager
def connect(path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_section(conn, *, fmid, activity_code, name, type_code, reg_event,
                   date_start, date_end, time_start, time_end, days,
                   location, ages, cost, ts) -> None:
    conn.execute(
        """
        INSERT INTO sections
            (fmid, activity_code, name, type_code, reg_event, date_start, date_end,
             time_start, time_end, days, location, ages, cost, first_seen, last_seen)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(fmid) DO UPDATE SET
            activity_code = COALESCE(excluded.activity_code, sections.activity_code),
            name          = COALESCE(excluded.name,          sections.name),
            type_code     = COALESCE(excluded.type_code,     sections.type_code),
            reg_event     = COALESCE(excluded.reg_event,     sections.reg_event),
            date_start    = COALESCE(excluded.date_start,    sections.date_start),
            date_end      = COALESCE(excluded.date_end,      sections.date_end),
            time_start    = COALESCE(excluded.time_start,    sections.time_start),
            time_end      = COALESCE(excluded.time_end,      sections.time_end),
            days          = COALESCE(excluded.days,          sections.days),
            location      = COALESCE(excluded.location,      sections.location),
            ages          = COALESCE(excluded.ages,          sections.ages),
            cost          = COALESCE(excluded.cost,          sections.cost),
            last_seen     = excluded.last_seen
        """,
        (fmid, activity_code, name, type_code, reg_event, date_start, date_end,
         time_start, time_end, days, location, ages, cost, ts, ts),
    )


def insert_snapshot(conn, *, fmid, ts, status, enrolled, waitlist) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO snapshots (fmid, ts, status, enrolled, waitlist) VALUES (?,?,?,?,?)",
        (fmid, ts, status, enrolled, waitlist),
    )


def latest_status(conn, fmid: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM snapshots WHERE fmid=? ORDER BY ts DESC LIMIT 1", (fmid,)
    ).fetchone()
    return row["status"] if row else None


def previous_status(conn, fmid: str, before_ts: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM snapshots WHERE fmid=? AND ts<? ORDER BY ts DESC LIMIT 1",
        (fmid, before_ts),
    ).fetchone()
    return row["status"] if row else None


def record_alert(conn, *, fmid: str, prev_status: str | None, new_status: str, ts: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO alerts_sent (fmid, prev_status, new_status, ts) VALUES (?,?,?,?)",
        (fmid, prev_status, new_status, ts),
    )
