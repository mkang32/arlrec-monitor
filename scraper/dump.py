"""Render the SQLite snapshots into committed-to-the-repo artifacts:
    data/latest.csv      — one row per section: current status + most recent counts
    data/snapshots.csv   — every snapshot row (history)
    index.html           — readable HTML page (lives at repo root for GH Pages)

Generates everything from data/snapshots.sqlite. Idempotent — safe to re-run.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from . import config

ET = ZoneInfo("America/New_York")

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "snapshots.sqlite"
LATEST_CSV = DATA_DIR / "latest.csv"
SNAPSHOTS_CSV = DATA_DIR / "snapshots.csv"
# Analysis-friendly export: one row per currently-open section, with sell-out
# duration. Materialized as a table named `currently_open` inside snapshots.sqlite
# and mirrored to data/currently_open.csv.
CURRENTLY_OPEN_CSV = DATA_DIR / "currently_open.csv"
# Dashboard (live filterable tables) lives under its county/season directory.
# Future seasons get sibling directories (arlington-va/2026-fall/dashboard/ etc.)
INDEX_HTML = Path("arlington-va/2026-summer/dashboard/index.html")


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        print(f"no DB at {DB_PATH} — nothing to dump")
        return 0
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        _dump_snapshots_csv(conn)
        _dump_latest_csv(conn)
        _dump_analysis(conn)
        _dump_index_html(conn)
    finally:
        conn.close()
    return 0


# ----- CSV dumps -----------------------------------------------------------

def _dump_snapshots_csv(conn) -> None:
    rows = conn.execute(
        "SELECT fmid, ts, status, enrolled, waitlist FROM snapshots ORDER BY ts, fmid"
    ).fetchall()
    with SNAPSHOTS_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fmid", "ts", "status", "enrolled", "waitlist"])
        for r in rows:
            w.writerow([r["fmid"], r["ts"], r["status"], r["enrolled"], r["waitlist"]])


def _dump_latest_csv(conn) -> None:
    # status/ts from the most recent snapshot;
    # enrolled/waitlist from the most recent snapshot with non-null counts.
    rows = conn.execute(
        """
        WITH latest_status AS (
            SELECT s.fmid, s.ts, s.status
            FROM snapshots s
            JOIN (SELECT fmid, MAX(ts) AS max_ts FROM snapshots GROUP BY fmid) m
              ON m.fmid = s.fmid AND m.max_ts = s.ts
        ),
        latest_counts AS (
            SELECT s.fmid, s.enrolled, s.waitlist
            FROM snapshots s
            JOIN (
              SELECT fmid, MAX(ts) AS max_ts FROM snapshots
              WHERE enrolled IS NOT NULL GROUP BY fmid
            ) m ON m.fmid = s.fmid AND m.max_ts = s.ts
        )
        SELECT sec.fmid, sec.activity_code, sec.name, sec.reg_event, sec.type_code,
               sec.date_start, sec.date_end, sec.time_start, sec.time_end, sec.days,
               sec.location, sec.ages, sec.cost,
               ls.ts, ls.status, lc.enrolled, lc.waitlist
        FROM sections sec
        LEFT JOIN latest_status ls ON ls.fmid = sec.fmid
        LEFT JOIN latest_counts lc ON lc.fmid = sec.fmid
        ORDER BY sec.name, sec.activity_code
        """
    ).fetchall()
    with LATEST_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "fmid", "activity_code", "name", "reg_event", "type_code", "type_label",
            "date_start", "date_end", "time_start", "time_end", "days",
            "location", "ages", "cost",
            "last_seen_ts", "status", "enrolled", "waitlist",
        ])
        for r in rows:
            d = dict(r)
            d["type_label"] = config.TYPE_LABELS.get(d.get("type_code") or "", "")
            w.writerow([
                d["fmid"], d["activity_code"], d["name"], d["reg_event"],
                d["type_code"], d["type_label"],
                d["date_start"], d["date_end"], d["time_start"], d["time_end"], d["days"],
                d["location"], d["ages"], d["cost"],
                d["ts"], d["status"], d["enrolled"], d["waitlist"],
            ])


# ----- Analysis export (CSV + dedicated SQLite) ----------------------------

# Order here = order in CSV and SQLite. Add new fields at the end to keep
# downstream readers happy.
_ANALYSIS_COLS: tuple[tuple[str, str], ...] = (
    ("fmid",                       "TEXT PRIMARY KEY"),
    ("activity_code",              "TEXT"),
    ("name",                       "TEXT"),
    ("type_code",                  "TEXT"),
    ("type_label",                 "TEXT"),
    ("reg_event",                  "TEXT"),
    ("reg_opens_at",               "TEXT"),
    ("date_start",                 "TEXT"),
    ("date_end",                   "TEXT"),
    ("days",                       "TEXT"),
    ("time_start",                 "TEXT"),
    ("time_end",                   "TEXT"),
    ("location",                   "TEXT"),
    ("ages",                       "TEXT"),
    ("cost",                       "TEXT"),
    ("status",                     "TEXT"),
    ("last_seen_ts",               "TEXT"),
    ("enrolled",                   "INTEGER"),
    ("waitlist",                   "INTEGER"),
    ("counts_ts",                  "TEXT"),
    ("first_scarce_ts",            "TEXT"),
    ("last_open_ts",               "TEXT"),
    ("sold_out_within_seconds",    "INTEGER"),
    ("sold_out_within_human",      "TEXT"),
    ("sold_out_start_source",      "TEXT"),  # 'reg-open' (preferred) or 'last-poll'
)


def _opens_at_before(scarce_dt: dt.datetime) -> tuple[dt.datetime, str] | tuple[None, None]:
    """Return (opens_at, event_code) for the latest known registration window
    that opened at or before `scarce_dt`. Robust to WebTrac re-tagging:
    Arlington admins use ENJOYSUMMER1/ENJOYSUMMER2 on their respective opening
    days then later collapse everything under ENJOYSUMMER — but the section's
    actual registration moment is determined by *when* it was first observed
    scarce, not by what the row currently says.
    """
    best: tuple[dt.datetime, str] | None = None
    for event_code, iso in config.REG_OPENS_AT_UTC.items():
        try:
            opens_dt = dt.datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            continue
        if opens_dt <= scarce_dt and (best is None or opens_dt > best[0]):
            best = (opens_dt, event_code)
    return best if best is not None else (None, None)


def _dump_analysis(conn) -> None:
    """Build the 'currently_open' analysis table — same rows as the
    Currently-open UI table, plus sell-out duration when applicable. The table
    is materialized inside the main snapshots.sqlite (DROP + CREATE on each
    run, so it's always in sync with the latest snapshots) and mirrored to
    data/currently_open.csv for non-SQL consumers.
    """
    rows = conn.execute(
        """
        WITH latest_status AS (
            SELECT s.fmid, s.ts, s.status
            FROM snapshots s
            JOIN (SELECT fmid, MAX(ts) AS max_ts FROM snapshots GROUP BY fmid) m
              ON m.fmid = s.fmid AND m.max_ts = s.ts
        ),
        latest_counts AS (
            SELECT s.fmid, s.ts AS counts_ts, s.enrolled, s.waitlist
            FROM snapshots s
            JOIN (
              SELECT fmid, MAX(ts) AS max_ts FROM snapshots
              WHERE enrolled IS NOT NULL GROUP BY fmid
            ) m ON m.fmid = s.fmid AND m.max_ts = s.ts
        ),
        first_scarce AS (
            SELECT fmid, MIN(ts) AS scarce_ts
            FROM snapshots
            WHERE status IN ('Waitlist','Full')
            GROUP BY fmid
        ),
        last_open AS (
            SELECT s.fmid, MAX(s.ts) AS open_ts
            FROM snapshots s
            JOIN first_scarce fs ON fs.fmid = s.fmid
            WHERE s.status IN ('Available','Unavailable')
              AND s.ts < fs.scarce_ts
            GROUP BY s.fmid
        )
        SELECT sec.fmid, sec.activity_code, sec.name, sec.type_code, sec.reg_event,
               sec.date_start, sec.date_end, sec.days, sec.time_start, sec.time_end,
               sec.location, sec.ages, sec.cost,
               ls.status, ls.ts AS last_seen_ts,
               lc.enrolled, lc.waitlist, lc.counts_ts,
               fs.scarce_ts AS first_scarce_ts,
               lo.open_ts AS last_open_ts
        FROM sections sec
        JOIN latest_status ls ON ls.fmid = sec.fmid
        LEFT JOIN latest_counts lc ON lc.fmid = sec.fmid
        LEFT JOIN first_scarce fs ON fs.fmid = sec.fmid
        LEFT JOIN last_open lo ON lo.fmid = sec.fmid
        WHERE ls.status != 'Unavailable'
        ORDER BY sec.type_code, sec.name, sec.activity_code
        """
    ).fetchall()

    augmented: list[dict] = []
    for r in rows:
        d = dict(r)
        d["type_label"] = config.TYPE_LABELS.get(d.get("type_code") or "", "")
        secs = None
        src = None
        reg_opens_at_iso = None
        if d.get("first_scarce_ts"):
            scarce_dt = dt.datetime.fromisoformat(d["first_scarce_ts"])
            # Find the latest registration window that opened at or before
            # the section was observed scarce. Self-correcting against
            # WebTrac admins re-tagging `reg_event` later in the cycle.
            opens_dt, _event = _opens_at_before(scarce_dt)
            start = None
            if opens_dt is not None:
                start = opens_dt
                src = "reg-open"
                reg_opens_at_iso = opens_dt.isoformat()
            elif d.get("last_open_ts"):
                last_dt = dt.datetime.fromisoformat(d["last_open_ts"])
                if last_dt <= scarce_dt:
                    start = last_dt
                    src = "last-poll"
            if start is not None:
                secs = int((scarce_dt - start).total_seconds())
        d["reg_opens_at"] = reg_opens_at_iso
        d["sold_out_within_seconds"] = secs
        d["sold_out_within_human"] = _fmt_duration(secs) if secs is not None else None
        d["sold_out_start_source"] = src
        augmented.append(d)

    # CSV
    with CURRENTLY_OPEN_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in _ANALYSIS_COLS])
        for d in augmented:
            w.writerow([d.get(c) for c, _ in _ANALYSIS_COLS])

    # Materialized table inside snapshots.sqlite — drop & recreate each run to
    # keep schema migrations trivial and the table fresh against the latest
    # snapshot data.
    conn.execute("DROP TABLE IF EXISTS currently_open")
    cols_sql = ", ".join(f"{name} {sqltype}" for name, sqltype in _ANALYSIS_COLS)
    conn.execute(f"CREATE TABLE currently_open ({cols_sql})")
    placeholders = ", ".join("?" * len(_ANALYSIS_COLS))
    conn.executemany(
        f"INSERT INTO currently_open VALUES ({placeholders})",
        [[d.get(c) for c, _ in _ANALYSIS_COLS] for d in augmented],
    )
    conn.execute(
        "CREATE INDEX idx_currently_open_duration "
        "ON currently_open(sold_out_within_seconds)"
    )
    conn.execute(
        "CREATE INDEX idx_currently_open_type "
        "ON currently_open(type_code, status)"
    )
    conn.commit()


# ----- HTML page -----------------------------------------------------------

def _dump_index_html(conn) -> None:
    # Show when data was last polled (most recent snapshot row), not when this
    # HTML was generated. The page time is just a deploy artifact; users care
    # about data freshness.
    row = conn.execute("SELECT MAX(ts) FROM snapshots").fetchone()
    last_data_iso = row[0] if row and row[0] else None
    last_data_str = _fmt_et(last_data_iso) if last_data_iso else "(no snapshots yet)"

    # Watchlist
    watch_rows = [_latest_row_for(conn, fmid) for fmid in config.WATCH_FMIDS]
    watch_rows = [dict(r) for r in watch_rows if r is not None]

    # Sections that are currently non-Unavailable.
    # Latest status comes from the most recent snapshot; counts come from the
    # most recent snapshot with non-null enrolled (status polls run with
    # --skip-counts so the very latest row often lacks counts).
    active = [dict(r) for r in conn.execute(
        """
        WITH latest_status AS (
            SELECT s.fmid, s.ts, s.status
            FROM snapshots s
            JOIN (SELECT fmid, MAX(ts) AS max_ts FROM snapshots GROUP BY fmid) m
              ON m.fmid = s.fmid AND m.max_ts = s.ts
        ),
        latest_counts AS (
            SELECT s.fmid, s.enrolled, s.waitlist
            FROM snapshots s
            JOIN (
              SELECT fmid, MAX(ts) AS max_ts
              FROM snapshots
              WHERE enrolled IS NOT NULL
              GROUP BY fmid
            ) m ON m.fmid = s.fmid AND m.max_ts = s.ts
        )
        SELECT sec.fmid, sec.activity_code, sec.name, sec.type_code, sec.location, sec.days,
               sec.time_start, sec.time_end, sec.ages, sec.cost,
               ls.status, ls.ts,
               lc.enrolled, lc.waitlist
        FROM sections sec
        JOIN latest_status ls ON ls.fmid = sec.fmid
        LEFT JOIN latest_counts lc ON lc.fmid = sec.fmid
        WHERE ls.status != 'Unavailable'
        ORDER BY
          CASE ls.status WHEN 'Full' THEN 0 WHEN 'Waitlist' THEN 1
                          WHEN 'Available' THEN 2 ELSE 3 END,
          sec.name, sec.activity_code
        """
    ).fetchall()]

    sellout = _sellout_rows(conn)

    parts: list[str] = []
    parts.append(_HTML_HEAD.format(last_data=html.escape(last_data_str)))

    parts.append("<h2>Watchlist</h2>")
    if not watch_rows:
        parts.append("<p class='dim'>No watchlist sections seen yet.</p>")
    else:
        parts.append(_render_table_html(watch_rows, _COLUMNS_WATCHLIST, table_id="watch"))

    parts.append("<h2>Sell-out speed</h2>")
    parts.append("<p class='dim'>How fast each section transitioned from open (Unavailable/Available) "
                 "to scarce (Waitlist/Full). Shortest first. "
                 "Each duration is an upper bound — the actual sell-out moment is at or before the value shown.</p>")
    if not sellout:
        parts.append("<p class='dim'>No sections have sold out yet.</p>")
    else:
        any_uncertain = any(r["uncertain"] for r in sellout)
        footnote = ""
        if any_uncertain:
            footnote = (
                "<p class='footnote'><b>*</b> = section was already scarce on our first poll "
                "after registration opened, so the actual sell-out happened anywhere between "
                "registration opening and that first poll — exact moment unknown.</p>"
            )
        parts.append(_render_table_html(sellout, _COLUMNS_SELLOUT, table_id="sellout"))
        if footnote:
            parts.append(footnote)

    parts.append("<h2>Currently open (Available, Waitlist, or Full)</h2>")
    if not active:
        parts.append("<p class='dim'>Nothing is open yet. Registration hasn't started.</p>")
    else:
        parts.append(_render_table_html(active, _COLUMNS_OPEN, table_id="open"))

    parts.append("<h2>Downloads</h2>")
    parts.append(
        "<ul>"
        # Dashboard lives at /arlington-va/2026-summer/dashboard/, data files at /data/.
        # `../../../` walks up 3 dirs to repo root, then into data/.
        "<li><a href='../../../data/latest.csv'>latest.csv</a> — one row per section, current state</li>"
        "<li><a href='../../../data/snapshots.csv'>snapshots.csv</a> — full status history</li>"
        "<li><a href='../../../data/snapshots.sqlite'>snapshots.sqlite</a> — raw SQLite database</li>"
        "</ul>"
    )
    parts.append(_HTML_FOOT)
    INDEX_HTML.write_text("".join(parts))


# ----- Data helpers --------------------------------------------------------

def _latest_row_for(conn, fmid: str):
    """status + ts from the most recent snapshot.
       enrolled + waitlist from the most recent snapshot with non-null counts
       (hot-window polls run `--skip-counts` and leave those columns null)."""
    return conn.execute(
        """
        SELECT sec.fmid, sec.activity_code, sec.name, sec.type_code, sec.location, sec.days,
               sec.time_start, sec.time_end, sec.ages, sec.cost,
               (SELECT status FROM snapshots WHERE fmid = sec.fmid ORDER BY ts DESC LIMIT 1) AS status,
               (SELECT ts     FROM snapshots WHERE fmid = sec.fmid ORDER BY ts DESC LIMIT 1) AS ts,
               (SELECT enrolled FROM snapshots WHERE fmid = sec.fmid AND enrolled IS NOT NULL ORDER BY ts DESC LIMIT 1) AS enrolled,
               (SELECT waitlist FROM snapshots WHERE fmid = sec.fmid AND waitlist IS NOT NULL ORDER BY ts DESC LIMIT 1) AS waitlist
        FROM sections sec
        WHERE sec.fmid = ?
        """,
        (fmid,),
    ).fetchone()


def _sellout_rows(conn) -> list[dict]:
    """For each section that has ever been observed as Waitlist or Full, compute:
       - duration_s = upper-bound sell-out duration (max(0, scarce_ts - start_ts))
                       where start_ts = REG_OPENS_AT_UTC[reg_event] if known,
                                         else the last Available/Unavailable poll before scarce
       - uncertain = True if first observation after reg-open was already scarce
                     (so the actual fill could've been anywhere between reg-open
                      and that first poll).
    """
    raw = conn.execute(
        """
        WITH first_scarce AS (
            SELECT fmid, MIN(ts) AS scarce_ts
            FROM snapshots
            WHERE status IN ('Waitlist','Full')
            GROUP BY fmid
        ),
        last_open AS (
            SELECT s.fmid, MAX(s.ts) AS open_ts
            FROM snapshots s
            JOIN first_scarce fs ON fs.fmid = s.fmid
            WHERE s.status IN ('Available','Unavailable')
              AND s.ts < fs.scarce_ts
            GROUP BY s.fmid
        ),
        latest_status AS (
            SELECT s.fmid, s.ts, s.status
            FROM snapshots s
            JOIN (SELECT fmid, MAX(ts) AS max_ts FROM snapshots GROUP BY fmid) m
              ON m.fmid = s.fmid AND m.max_ts = s.ts
        ),
        latest_counts AS (
            SELECT s.fmid, s.enrolled, s.waitlist
            FROM snapshots s
            JOIN (
              SELECT fmid, MAX(ts) AS max_ts
              FROM snapshots
              WHERE enrolled IS NOT NULL
              GROUP BY fmid
            ) m ON m.fmid = s.fmid AND m.max_ts = s.ts
        )
        SELECT
            fs.fmid,
            sec.name, sec.activity_code, sec.type_code, sec.reg_event,
            sec.days, sec.time_start, sec.time_end, sec.location, sec.ages, sec.cost,
            lo.open_ts,
            fs.scarce_ts,
            (SELECT status FROM snapshots WHERE fmid=fs.fmid AND ts=fs.scarce_ts
             ORDER BY ts LIMIT 1) AS scarce_status,
            ls.status   AS current_status,
            lc.enrolled AS current_enrolled,
            lc.waitlist AS current_waitlist
        FROM first_scarce fs
        LEFT JOIN last_open lo ON lo.fmid = fs.fmid
        JOIN sections sec ON sec.fmid = fs.fmid
        LEFT JOIN latest_status ls ON ls.fmid = fs.fmid
        LEFT JOIN latest_counts lc ON lc.fmid = fs.fmid
        """
    ).fetchall()

    out: list[dict] = []
    for r in raw:
        d = dict(r)
        scarce_dt = dt.datetime.fromisoformat(d["scarce_ts"])
        # Find the latest registration window that opened at or before
        # the section was observed scarce. Robust to WebTrac re-tagging.
        opens_dt, _event = _opens_at_before(scarce_dt)
        start_dt = None
        start_source = None
        if opens_dt is not None:
            start_dt = opens_dt
            start_source = "reg-open"
        elif d["open_ts"]:
            od = dt.datetime.fromisoformat(d["open_ts"])
            if od <= scarce_dt:
                start_dt = od
                start_source = "last-poll"
        if start_dt is None:
            continue
        duration_s = int((scarce_dt - start_dt).total_seconds())
        d["start_ts_utc"] = start_dt.isoformat()
        d["start_source"] = start_source
        d["duration_s"] = duration_s

        # Uncertainty: was the first post-open observation already scarce?
        d["uncertain"] = False
        if start_source == "reg-open" and opens_dt is not None:
            first_after = conn.execute(
                "SELECT status FROM snapshots WHERE fmid=? AND ts>=? "
                "ORDER BY ts ASC LIMIT 1",
                (d["fmid"], opens_dt.isoformat()),
            ).fetchone()
            if first_after and first_after["status"] in ("Waitlist", "Full"):
                d["uncertain"] = True
        out.append(d)
    out.sort(key=lambda x: x["duration_s"])
    return out


# ----- Formatting helpers --------------------------------------------------

def _fmt_et(ts_utc_iso: str | None) -> str:
    if not ts_utc_iso:
        return ""
    try:
        u = dt.datetime.fromisoformat(ts_utc_iso)
    except ValueError:
        return ts_utc_iso
    e = u.astimezone(ET)
    return e.strftime("%b %d, %-I:%M:%S %p ET")


def _fmt_et_short(ts_utc_iso: str | None) -> str:
    if not ts_utc_iso:
        return ""
    try:
        u = dt.datetime.fromisoformat(ts_utc_iso)
    except ValueError:
        return ts_utc_iso
    e = u.astimezone(ET)
    return e.strftime("%b %d, %-I:%M %p ET")


def _fmt_duration(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m {s}s" if s else f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m" if m else f"{h}h"
    d, h = divmod(h, 24)
    return f"{d}d {h}h" if h else f"{d}d"


def _pill_class(status: str | None) -> str:
    return {
        "Available": "open",
        "Waitlist":  "wait",
        "Full":      "full",
        "Unavailable": "off",
    }.get(status or "", "off")


def _section_link(fmid: str) -> str:
    return (
        f"<a href='https://vaarlingtonweb.myvscloud.com/webtrac/web/iteminfo.html?"
        f"Module=AR&FMID={html.escape(fmid)}' target='_blank'>open</a>"
    )


def _time_label(row) -> str:
    return f"{row['days'] or ''} {row['time_start'] or ''}-{row['time_end'] or ''}".strip()


# ----- Column descriptors --------------------------------------------------

@dataclass(frozen=True)
class Column:
    label: str
    render: Callable[[dict], str]   # cell innerHTML
    filter_kind: str | None         # "select" | "text" | "preset" | "contains" | "tokens" | None
    filter_value: Callable[[dict], str]  # plain-text used for substring/select match
    # For numeric "preset" filters: extractor returns a number (or None if N/A),
    # and presets is a list of (label, "lo,hi") where either bound may be blank.
    numeric_value: Callable[[dict], float | None] | None = None
    presets: tuple[tuple[str, str], ...] | None = None
    # For "contains" filters: extractor returns the row's (lo, hi) numeric range;
    # the user types a number and the row passes iff lo <= user_value <= hi.
    range_value: Callable[[dict], tuple[float, float] | None] | None = None
    contains_placeholder: str = "value"
    # For "tokens" filters: list of (label, token_value). Row passes iff its
    # filter_value (split on ',') contains the selected token.
    tokens: tuple[tuple[str, str], ...] | None = None


def _status_pill(status_key="status"):
    def f(r):
        s = r.get(status_key) or "?"
        return f"<span class='pill pill--{_pill_class(s)}'>{html.escape(s)}</span>"
    return f


def _type_label_render(r):
    return html.escape(config.TYPE_LABELS.get(r.get("type_code") or "", r.get("type_code") or ""))


def _type_label_value(r):
    return config.TYPE_LABELS.get(r.get("type_code") or "", r.get("type_code") or "")


_ENROLLED_PRESETS = (
    ("0",       "0,0"),
    ("1-4",     "1,4"),
    ("5-9",     "5,9"),
    ("10-19",   "10,19"),
    ("20+",     "20,"),
)
_WAITLIST_PRESETS = (
    ("0",       "0,0"),
    ("1-4",     "1,4"),
    ("5-9",     "5,9"),
    ("10+",     "10,"),
)
_DURATION_PRESETS = (
    ("≤ 1 min",   ",60"),
    ("≤ 5 min",   ",300"),
    ("≤ 30 min",  ",1800"),
    ("≤ 1 hr",    ",3600"),
    ("≤ 6 hr",    ",21600"),
    ("≤ 1 day",   ",86400"),
    ("> 1 day",         "86400,"),
)


def _opt_int(r, key):
    v = r.get(key)
    return None if v is None else float(v)


_AGE_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*")
_TIME_RE = re.compile(r"\s*(\d{1,2}):(\d{2})\s*(am|pm)\s*", re.I)
_DOLLAR_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)")


def _parse_first_dollar(s: str | None) -> float | None:
    """'$70.00/$90.00' → 70.0 (resident price, the lower of the two)."""
    if not s:
        return None
    m = _DOLLAR_RE.search(s)
    return float(m.group(1)) if m else None


def _parse_age_range(s: str | None) -> tuple[float, float] | None:
    """'1.5-5.99' → (1.5, 5.99). Returns None if format isn't 'min-max'."""
    if not s:
        return None
    m = _AGE_RE.match(s)
    if not m:
        return None
    return (float(m.group(1)), float(m.group(2)))


def _parse_time_to_minutes(s: str | None) -> float | None:
    """'10:30 am' → 630.0 (minutes from midnight). '12:00 pm' → 720.0."""
    if not s:
        return None
    m = _TIME_RE.match(s)
    if not m:
        return None
    h, mn, ampm = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if ampm == "pm" and h != 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    return float(h * 60 + mn)


def _day_tokens_text(r) -> str:
    """'M, W, F' → 'm,w,f' (lowercased, comma-joined, used for row filter data attrs)."""
    d = (r.get("days") or "").strip()
    if not d:
        return ""
    return ",".join(p.strip().lower() for p in d.split(",") if p.strip())


_COLUMN_STATUS  = Column("Status",  _status_pill("status"),        "select", lambda r: r.get("status") or "")
_COLUMN_TYPE    = Column("Type",    _type_label_render,            "select", _type_label_value)
_COLUMN_CLASS   = Column("Class",   lambda r: html.escape(r.get("name") or ""),         "text",   lambda r: r.get("name") or "")
_COLUMN_ACT     = Column("Activity",lambda r: html.escape(r.get("activity_code") or ""),"text",   lambda r: r.get("activity_code") or "")
_COLUMN_WHEN    = Column("When",    lambda r: html.escape(_time_label(r)),              "text",   _time_label)

_DAY_TOKENS = (
    ("Sun", "su"), ("Mon", "m"), ("Tue", "tu"), ("Wed", "w"),
    ("Thu", "th"), ("Fri", "f"), ("Sat", "sa"),
)

_COLUMN_DAY = Column(
    "Day",
    lambda r: html.escape(r.get("days") or ""),
    "tokens",
    _day_tokens_text,
    tokens=_DAY_TOKENS,
)

_TIME_PRESETS = (
    ("Morning (before 12pm)",    ",719"),
    ("Afternoon (12pm-5pm)",     "720,1019"),
    ("Evening (5pm+)",           "1020,"),
)

def _time_only_label(r) -> str:
    ts, te = r.get("time_start") or "", r.get("time_end") or ""
    return f"{ts}-{te}".strip("-")

_COLUMN_TIME = Column(
    "Time",
    lambda r: html.escape(_time_only_label(r)),
    "preset",
    _time_only_label,
    numeric_value=lambda r: _parse_time_to_minutes(r.get("time_start")),
    presets=_TIME_PRESETS,
)
_COLUMN_LOC     = Column("Location",lambda r: html.escape(r.get("location") or ""),     "select", lambda r: r.get("location") or "")
_COLUMN_AGES    = Column(
    "Ages",
    lambda r: html.escape(r.get("ages") or ""),
    "contains",
    lambda r: r.get("ages") or "",
    range_value=lambda r: _parse_age_range(r.get("ages")),
    contains_placeholder="age (e.g. 4)",
)

_COST_PRESETS = (
    ("Free",      "0,0"),
    ("≤ $50",     ",50"),
    ("≤ $100",    ",100"),
    ("≤ $200",    ",200"),
    ("≤ $500",    ",500"),
    ("> $500",          "500.01,"),
)

_COLUMN_COST = Column(
    "Cost",
    lambda r: html.escape(r.get("cost") or ""),
    "preset",
    lambda r: r.get("cost") or "",
    numeric_value=lambda r: _parse_first_dollar(r.get("cost")),
    presets=_COST_PRESETS,
)
_COLUMN_ENROLL  = Column(
    "Enrolled",
    lambda r: "" if r.get("enrolled") is None else str(r["enrolled"]),
    "preset",
    lambda r: "" if r.get("enrolled") is None else str(r["enrolled"]),
    numeric_value=lambda r: _opt_int(r, "enrolled"),
    presets=_ENROLLED_PRESETS,
)
_COLUMN_WAIT_N  = Column(
    "Waitlist",
    lambda r: "" if r.get("waitlist") is None else str(r["waitlist"]),
    "preset",
    lambda r: "" if r.get("waitlist") is None else str(r["waitlist"]),
    numeric_value=lambda r: _opt_int(r, "waitlist"),
    presets=_WAITLIST_PRESETS,
)
_COLUMN_LASTSEEN = Column("Last seen (ET)",
                          lambda r: f"<time datetime='{html.escape(r.get('ts') or '')}'>{html.escape(_fmt_et_short(r.get('ts')))}</time>",
                          None, lambda r: _fmt_et_short(r.get("ts")) or "")
_COLUMN_LINK    = Column("Link",    lambda r: _section_link(r["fmid"]),                 None,     lambda r: "")


def _sellout_duration_render(r):
    star = "*" if r.get("uncertain") else ""
    return f"<b>≤ {_fmt_duration(r['duration_s'])}</b>{star}"


def _sellout_duration_value(r):
    # used for filtering — just the human string
    return f"≤ {_fmt_duration(r['duration_s'])}"


def _sellout_current_status_render(r):
    s = r.get("current_status") or r.get("scarce_status") or "?"
    return f"<span class='pill pill--{_pill_class(s)}'>{html.escape(s)}</span>"


def _sellout_current_status_value(r):
    return r.get("current_status") or r.get("scarce_status") or ""


def _enrolled_now(r):
    return "" if r.get("current_enrolled") is None else str(r["current_enrolled"])


def _waitlist_now(r):
    return "" if r.get("current_waitlist") is None else str(r["current_waitlist"])


# Watchlist: small table, no per-column filters.
_COLUMNS_WATCHLIST: list[Column] = [
    Column(c.label, c.render, None, c.filter_value)  # strip filter_kind
    for c in [_COLUMN_STATUS, _COLUMN_TYPE, _COLUMN_CLASS, _COLUMN_ACT,
              _COLUMN_DAY, _COLUMN_TIME,
              _COLUMN_LOC, _COLUMN_AGES, _COLUMN_COST,
              _COLUMN_ENROLL, _COLUMN_WAIT_N, _COLUMN_LASTSEEN, _COLUMN_LINK]
]

_COLUMNS_OPEN: list[Column] = [
    _COLUMN_STATUS, _COLUMN_TYPE, _COLUMN_CLASS, _COLUMN_ACT,
    _COLUMN_DAY, _COLUMN_TIME,
    _COLUMN_LOC, _COLUMN_AGES, _COLUMN_COST,
    _COLUMN_ENROLL, _COLUMN_WAIT_N, _COLUMN_LINK,
]

_COLUMNS_SELLOUT: list[Column] = [
    Column(
        "Sold out within", _sellout_duration_render, "preset", _sellout_duration_value,
        numeric_value=lambda r: float(r["duration_s"]),
        presets=_DURATION_PRESETS,
    ),
    Column("Final status",    _sellout_current_status_render, "select", _sellout_current_status_value),
    _COLUMN_TYPE,
    _COLUMN_CLASS,
    _COLUMN_ACT,
    _COLUMN_DAY,
    _COLUMN_TIME,
    _COLUMN_LOC,
    _COLUMN_AGES,
    _COLUMN_COST,
    Column(
        "Enrolled", _enrolled_now, "preset", _enrolled_now,
        numeric_value=lambda r: _opt_int(r, "current_enrolled"),
        presets=_ENROLLED_PRESETS,
    ),
    Column(
        "Waitlist", _waitlist_now, "preset", _waitlist_now,
        numeric_value=lambda r: _opt_int(r, "current_waitlist"),
        presets=_WAITLIST_PRESETS,
    ),
    _COLUMN_LINK,
]


# ----- Table renderer ------------------------------------------------------

_FILTER_JS_TEMPLATE = """<script>(function(){
var id=%(id)r;
var tbl=document.getElementById(id);
if(!tbl) return;
var fs=tbl.querySelectorAll('thead .filter');
function apply(){
  var spec=[];
  fs.forEach(function(f){
    var v=(f.value||'').trim();
    if(!v) return;
    var i=parseInt(f.dataset.col);
    if(f.dataset.kind==='preset'){
      var parts=v.split(',');
      var lo=parts[0]!==''?parseFloat(parts[0]):null;
      var hi=parts[1]!==''?parseFloat(parts[1]):null;
      spec.push({i:i,op:'num',lo:lo,hi:hi});
    } else if(f.dataset.kind==='contains'){
      var x=parseFloat(v);
      if(!isNaN(x)) spec.push({i:i,op:'contains',x:x});
    } else if(f.dataset.kind==='tokens'){
      spec.push({i:i,op:'has_token',v:v.toLowerCase()});
    } else if(f.tagName==='SELECT'){
      spec.push({i:i,op:'eq',v:v.toLowerCase()});
    } else {
      spec.push({i:i,op:'in',v:v.toLowerCase()});
    }
  });
  var n=0,total=0;
  tbl.querySelectorAll('tbody tr').forEach(function(tr){
    total++;
    var ok=spec.every(function(s){
      if(s.op==='num'){
        var raw=tr.dataset['n'+s.i];
        if(raw===undefined||raw==='') return false;
        var x=parseFloat(raw);
        if(isNaN(x)) return false;
        if(s.lo!==null && x<s.lo) return false;
        if(s.hi!==null && x>s.hi) return false;
        return true;
      }
      if(s.op==='contains'){
        var lo=parseFloat(tr.dataset['l'+s.i]);
        var hi=parseFloat(tr.dataset['h'+s.i]);
        if(isNaN(lo)||isNaN(hi)) return false;
        return s.x>=lo && s.x<=hi;
      }
      if(s.op==='has_token'){
        var toks=(tr.dataset['c'+s.i]||'').toLowerCase().split(',');
        return toks.indexOf(s.v)>=0;
      }
      var t=(tr.dataset['c'+s.i]||'').toLowerCase();
      return s.op==='eq'?t===s.v:t.indexOf(s.v)>=0;
    });
    tr.style.display=ok?'':'none';
    if(ok) n++;
  });
  var c=document.getElementById(id+'-count');
  if(c) c.textContent=n+' / '+total;
}
fs.forEach(function(f){f.addEventListener('input',apply);f.addEventListener('change',apply);});
})();</script>"""


def _render_table_html(rows: list[dict], columns: list[Column], *, table_id: str) -> str:
    if not rows:
        return ""

    # Header row 1: labels
    label_cells = "".join(f"<th>{html.escape(c.label)}</th>" for c in columns)

    # Header row 2: per-column filter widgets (only for columns with filter_kind set)
    has_any_filter = any(c.filter_kind for c in columns)
    filter_cells_parts = []
    for i, c in enumerate(columns):
        if c.filter_kind == "select":
            values = sorted({c.filter_value(r) for r in rows if c.filter_value(r)})
            opts = "".join(
                f"<option value='{html.escape(v.lower())}'>{html.escape(v)}</option>"
                for v in values
            )
            widget = (
                f"<select class='filter' data-col='{i}'>"
                f"<option value=''>all</option>{opts}</select>"
            )
        elif c.filter_kind == "preset" and c.presets:
            opts = "".join(
                f"<option value='{html.escape(rng)}'>{html.escape(label)}</option>"
                for label, rng in c.presets
            )
            widget = (
                f"<select class='filter' data-col='{i}' data-kind='preset'>"
                f"<option value=''>any</option>{opts}</select>"
            )
        elif c.filter_kind == "contains":
            widget = (
                f"<input type='search' inputmode='decimal' class='filter' "
                f"data-col='{i}' data-kind='contains' "
                f"placeholder='{html.escape(c.contains_placeholder)}'>"
            )
        elif c.filter_kind == "tokens" and c.tokens:
            opts = "".join(
                f"<option value='{html.escape(tok)}'>{html.escape(label)}</option>"
                for label, tok in c.tokens
            )
            widget = (
                f"<select class='filter' data-col='{i}' data-kind='tokens'>"
                f"<option value=''>any</option>{opts}</select>"
            )
        elif c.filter_kind == "text":
            widget = f"<input type='search' class='filter' data-col='{i}' placeholder='filter'>"
        else:
            widget = ""
        filter_cells_parts.append(f"<th>{widget}</th>")
    filter_row = (
        "<tr class='filters-row'>" + "".join(filter_cells_parts) + "</tr>"
    ) if has_any_filter else ""

    thead = f"<thead><tr>{label_cells}</tr>{filter_row}</thead>"

    # Body
    body_parts = ["<tbody>"]
    for r in rows:
        cell_htmls: list[str] = []
        data_attrs: list[str] = []
        for i, c in enumerate(columns):
            cell_htmls.append(f"<td>{c.render(r)}</td>")
            fv = (c.filter_value(r) or "").lower()
            if fv:
                data_attrs.append(f"data-c{i}='{html.escape(fv)}'")
            # Numeric value for preset filtering (if applicable).
            if c.numeric_value is not None:
                nv = c.numeric_value(r)
                if nv is not None:
                    data_attrs.append(f"data-n{i}='{nv}'")
            # Range for "contains" filtering (if applicable).
            if c.range_value is not None:
                rv = c.range_value(r)
                if rv is not None:
                    data_attrs.append(f"data-l{i}='{rv[0]}'")
                    data_attrs.append(f"data-h{i}='{rv[1]}'")
        body_parts.append(
            f"<tr {' '.join(data_attrs)}>{''.join(cell_htmls)}</tr>"
        )
    body_parts.append("</tbody>")

    table = f"<table id='{table_id}'>{thead}{''.join(body_parts)}</table>"
    count_bar = (
        f"<div class='table-count'>Showing <b id='{table_id}-count'>{len(rows)} / {len(rows)}</b></div>"
    ) if has_any_filter else ""
    script = (_FILTER_JS_TEMPLATE % {"id": table_id}) if has_any_filter else ""
    return f"<div class='scroll-box'>{table}</div>{count_bar}{script}"


_HTML_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Arlington Park & Rec — registration monitor</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --bg: #fafafa; --fg: #111; --dim: #777; --line: #e3e3e3;
    --open: #2e7d32; --wait: #c77c00; --full: #c62828; --off: #555;
  }}
  body {{ font: 14px/1.45 -apple-system, system-ui, sans-serif; margin: 2em auto; max-width: 1200px;
         padding: 0 1em; color: var(--fg); background: var(--bg); }}
  h1 {{ margin-bottom: .2em; }}
  h2 {{ margin-top: 2em; border-bottom: 1px solid var(--line); padding-bottom: .2em; }}
  .meta, .dim, .footnote {{ color: var(--dim); }}
  .footnote {{ font-size: 12px; margin: 4px 0 0; }}
  .lede {{ color: var(--fg); max-width: 70ch; }}
  .lede p {{ margin: .4em 0; }}
  .schedule {{ background: #fff8e6; border-left: 3px solid #d4a017; padding: 8px 12px;
               margin: 16px 0; border-radius: 0 4px 4px 0; line-height: 1.55; }}
  .schedule b {{ color: #8a6d10; }}
  .schedule--polling {{ background: #eaf2fb; border-left-color: #1a64c8; }}
  .schedule--polling b {{ color: #134578; }}
  .scroll-box {{ max-height: 592px; overflow: auto;
                 border: 1px solid var(--line); border-radius: 4px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line);
           vertical-align: top; white-space: nowrap; }}
  th {{ font-weight: 600; background: #f0f0f0; position: sticky; top: 0; z-index: 2; }}
  thead .filters-row th {{ background: white; top: 28px; z-index: 1;
                            padding: 2px 4px; font-weight: 400; }}
  thead .filters-row .filter {{ width: 100%; box-sizing: border-box;
       padding: 2px 4px; font-size: 11px; border: 1px solid var(--line);
       border-radius: 3px; background: white; }}
  .pill {{ display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 11px;
          font-weight: 600; color: white; }}
  .pill--open {{ background: var(--open); }}
  .pill--wait {{ background: var(--wait); }}
  .pill--full {{ background: var(--full); }}
  .pill--off  {{ background: var(--off); }}
  .table-count {{ color: var(--dim); font-size: 12px; margin: 4px 0 0; }}
  a {{ color: #1a64c8; }}
</style>
</head><body>
<h1>Arlington Park &amp; Rec — registration monitor</h1>
<div class='lede'>
  <p><b>Why this exists:</b> Arlington's most popular kids' classes can sell out
     within seconds of registration opening. This page surfaces each section's
     historical sell-out speed so parents can plan registration day —
     which classes to grab first, which need a backup choice, and which are
     safe to register for at leisure.</p>
  <p>A scheduled GitHub Actions job polls the public
     <a href='https://vaarlingtonweb.myvscloud.com/webtrac/web/search.html?interfaceparameter=WebTrac'>WebTrac</a>
     catalog and records each section's status (Available / Waitlist / Full /
     Unavailable) over time into the SQLite database below.
     The <b>Sell-out speed</b> table shows the upper-bound time it took for each
     section to transition from open to scarce — shortest first. Use the
     per-column filters in any table header to narrow results.</p>
  <p class='meta'>Last data poll: {last_data}</p>
</div>
<div class='schedule'>
  <b>Registration windows (all noon EDT = 16:00 UTC):</b><br>
  Mon 2026-05-12 — Gymnastics (Summer ENJOY)<br>
  Tue 2026-05-13 — Aquatics (Summer ENJOY)<br>
  Wed 2026-05-14 — Other classes (Summer ENJOY / Nature)
</div>
<div class='schedule schedule--polling'>
  <b>Polling schedule:</b><br>
  <b>Registration days</b> (May 12, 13, 14):
    every <b>60 s</b> from 11:50&nbsp;ET (pre-open baseline),
    every <b>15 s</b> for the first 5 min after noon ET (the explosive open moment),
    every <b>60 s</b> until 12:30 ET,
    every <b>5 min</b> until 1:00 PM ET,
    then every <b>10 min</b> until 3:00 PM ET.<br>
  <b>May 11&ndash;15</b>: hourly background polling.<br>
  <b>May 16&ndash;Jul 10</b>: once daily (08:00 ET).<br>
  <b>After Jul 10</b>: monitoring stops &mdash; all summer classes have started by then.
</div>
"""

_HTML_FOOT = "</body></html>"


if __name__ == "__main__":
    raise SystemExit(main())
