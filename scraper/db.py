"""SQLite snapshot store. The file lives at data/snapshots.sqlite and is committed to the repo."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Phase 1 (multi-site) defaults. The scraper is still Arlington-only, so these
# defaults keep existing single-site inserts behavior-identical and backfill
# pre-existing rows when the columns are added. Phase 2 (vendor abstraction)
# will thread site_id/season_id through upsert_section explicitly.
DEFAULT_SITE_ID = "arlington"
DEFAULT_SEASON_ID = "arlington-2026-summer"
FALL_SEASON_ID = "arlington-2026-fall"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vendor TEXT NOT NULL,
    base_url TEXT,
    timezone TEXT
);

CREATE TABLE IF NOT EXISTS seasons (
    id TEXT PRIMARY KEY,
    site_id TEXT NOT NULL REFERENCES sites(id),
    name TEXT NOT NULL,
    registration_events TEXT,
    registration_opens_at_utc TEXT
);

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
    last_seen TEXT NOT NULL,
    site_id TEXT NOT NULL DEFAULT 'arlington',
    season_id TEXT NOT NULL DEFAULT 'arlington-2026-summer'
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
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn) -> None:
    """Bring a pre-multi-site DB up to the current schema. Idempotent.

    A DB created before Phase 1 already has a `sections` table, so the
    CREATE TABLE IF NOT EXISTS in SCHEMA is a no-op for it — the new columns
    must be added via ALTER. ADD COLUMN ... NOT NULL DEFAULT also backfills
    every existing row with the default, which is exactly the Arlington
    migration we want.
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sections)")}
    if "site_id" not in cols:
        conn.execute(
            f"ALTER TABLE sections ADD COLUMN site_id TEXT NOT NULL DEFAULT '{DEFAULT_SITE_ID}'"
        )
    if "season_id" not in cols:
        conn.execute(
            f"ALTER TABLE sections ADD COLUMN season_id TEXT NOT NULL DEFAULT '{DEFAULT_SEASON_ID}'"
        )
    # Created here, not in SCHEMA: on a pre-Phase-1 DB the columns don't exist
    # until the ALTERs above run, so the index can't live in the initial script.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sections_site_season ON sections(site_id, season_id)"
    )
    _seed_arlington(conn)


def _seed_arlington(conn) -> None:
    """Seed the Arlington site + known season rows. INSERT OR IGNORE so
    re-running is harmless and never clobbers hand-edited rows."""
    conn.execute(
        "INSERT OR IGNORE INTO sites (id, name, vendor, base_url, timezone) VALUES (?,?,?,?,?)",
        (
            DEFAULT_SITE_ID,
            "Arlington County, VA",
            "webtrac",
            "https://vaarlingtonweb.myvscloud.com/webtrac/web",
            "America/New_York",
        ),
    )
    # registration_events: JSON map of event tag -> opening instant (mirrors
    # config.REG_OPENS_AT_UTC). registration_opens_at_utc holds the season's
    # first opening for convenience.
    summer_reg_events = json.dumps(
        {
            "ENJOYSUMMER1": "2026-05-12T16:00:00+00:00",
            "ENJOYSUMMER2": "2026-05-13T16:00:00+00:00",
            "ENJOYSUMMER": "2026-05-14T16:00:00+00:00",
        }
    )
    conn.execute(
        "INSERT OR IGNORE INTO seasons "
        "(id, site_id, name, registration_events, registration_opens_at_utc) "
        "VALUES (?,?,?,?,?)",
        (
            DEFAULT_SEASON_ID,
            DEFAULT_SITE_ID,
            "2026-summer",
            summer_reg_events,
            "2026-05-12T16:00:00+00:00",
        ),
    )
    fall_reg_events = json.dumps(
        {
            "ENJOYFALL1": "2026-08-04T16:00:00+00:00",
            "ENJOYFALL2": "2026-08-05T16:00:00+00:00",
            "ENJOYFALL": "2026-08-06T16:00:00+00:00",
        }
    )
    conn.execute(
        "INSERT OR IGNORE INTO seasons "
        "(id, site_id, name, registration_events, registration_opens_at_utc) "
        "VALUES (?,?,?,?,?)",
        (
            FALL_SEASON_ID,
            DEFAULT_SITE_ID,
            "2026-fall",
            fall_reg_events,
            "2026-08-04T16:00:00+00:00",
        ),
    )


def upsert_section(conn, *, fmid, activity_code, name, type_code, reg_event,
                   date_start, date_end, time_start, time_end, days,
                   location, ages, cost, ts,
                   site_id: str = DEFAULT_SITE_ID,
                   season_id: str = DEFAULT_SEASON_ID) -> None:
    # site_id/season_id are set only on first INSERT (a section's season is
    # permanent — its fmid never reappears in a later season) and deliberately
    # left out of the ON CONFLICT SET below, so a metadata-refresh call can't
    # accidentally move an existing section to a different season.
    conn.execute(
        """
        INSERT INTO sections
            (fmid, activity_code, name, type_code, reg_event, date_start, date_end,
             time_start, time_end, days, location, ages, cost, first_seen, last_seen,
             site_id, season_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(fmid) DO UPDATE SET
            activity_code = COALESCE(excluded.activity_code, sections.activity_code),
            name          = COALESCE(excluded.name,          sections.name),
            type_code     = COALESCE(excluded.type_code,     sections.type_code),
            -- reg_event tracks WebTrac's CURRENT tag. Arlington admins use
            -- staged tags (ENJOYSUMMER1/ENJOYSUMMER2) on their respective
            -- registration days, then re-tag everything under a unified
            -- ENJOYSUMMER label once all events are open. We let the field
            -- follow that change — analysis uses observed scarcity time, not
            -- this field, to determine which registration day a section
            -- actually opened on. See dump.py `_opens_at_before(scarce_ts)`.
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
         time_start, time_end, days, location, ages, cost, ts, ts,
         site_id, season_id),
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
