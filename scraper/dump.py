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
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config

ET = ZoneInfo("America/New_York")

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "snapshots.sqlite"
LATEST_CSV = DATA_DIR / "latest.csv"
SNAPSHOTS_CSV = DATA_DIR / "snapshots.csv"
INDEX_HTML = Path("index.html")


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
    rows = conn.execute(
        """
        SELECT sec.fmid, sec.activity_code, sec.name, sec.reg_event, sec.type_code,
               sec.date_start, sec.date_end, sec.time_start, sec.time_end, sec.days,
               sec.location, sec.ages, sec.cost,
               latest.ts, latest.status, latest.enrolled, latest.waitlist
        FROM sections sec
        LEFT JOIN (
            SELECT s.fmid, s.ts, s.status, s.enrolled, s.waitlist
            FROM snapshots s
            JOIN (SELECT fmid, MAX(ts) AS max_ts FROM snapshots GROUP BY fmid) m
              ON m.fmid = s.fmid AND m.max_ts = s.ts
        ) latest ON latest.fmid = sec.fmid
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


# ----- HTML page -----------------------------------------------------------

def _dump_index_html(conn) -> None:
    now_utc = dt.datetime.now(dt.timezone.utc)
    now_et_str = _fmt_et(now_utc.isoformat(timespec="seconds"))

    # Watchlist
    watch_rows = [_latest_row_for(conn, fmid) for fmid in config.WATCH_FMIDS]
    watch_rows = [r for r in watch_rows if r is not None]

    # Anything currently non-Unavailable
    active = conn.execute(
        """
        SELECT sec.fmid, sec.activity_code, sec.name, sec.type_code, sec.location, sec.days,
               sec.time_start, sec.time_end, sec.ages, sec.cost,
               latest.status, latest.enrolled, latest.waitlist, latest.ts
        FROM sections sec
        JOIN (
            SELECT s.fmid, s.ts, s.status, s.enrolled, s.waitlist
            FROM snapshots s
            JOIN (SELECT fmid, MAX(ts) AS max_ts FROM snapshots GROUP BY fmid) m
              ON m.fmid = s.fmid AND m.max_ts = s.ts
        ) latest ON latest.fmid = sec.fmid
        WHERE latest.status != 'Unavailable'
        ORDER BY
          CASE latest.status WHEN 'Full' THEN 0 WHEN 'Waitlist' THEN 1
                              WHEN 'Available' THEN 2 ELSE 3 END,
          sec.name, sec.activity_code
        """
    ).fetchall()

    sellout = _sellout_rows(conn)

    parts: list[str] = []
    parts.append(_HTML_HEAD.format(now=html.escape(now_et_str)))

    parts.append("<h2>Watchlist</h2>")
    if not watch_rows:
        parts.append("<p class='dim'>No watchlist sections seen yet.</p>")
    else:
        parts.append("<div class='scroll-box'>" + _render_table(watch_rows, include_actions=True) + "</div>")

    parts.append("<h2>Sell-out speed</h2>")
    parts.append("<p class='dim'>How fast each section transitioned from open (Unavailable/Available) "
                 "to scarce (Waitlist/Full). Shortest first.</p>")
    if not sellout:
        parts.append("<p class='dim'>No sections have sold out yet.</p>")
    else:
        parts.append("<div class='scroll-box'>" + _render_sellout_table(sellout) + "</div>")

    parts.append("<h2>Currently open (Available, Waitlist, or Full)</h2>")
    if not active:
        parts.append("<p class='dim'>Nothing is open yet. Registration hasn't started.</p>")
    else:
        parts.append(_render_filterable_table(active, table_id="active", include_actions=True))

    parts.append("<h2>Downloads</h2>")
    parts.append(
        "<ul>"
        "<li><a href='data/latest.csv'>latest.csv</a> — one row per section, current state</li>"
        "<li><a href='data/snapshots.csv'>snapshots.csv</a> — full status history</li>"
        "<li><a href='data/snapshots.sqlite'>snapshots.sqlite</a> — raw SQLite database</li>"
        "</ul>"
    )
    parts.append(_HTML_FOOT)
    INDEX_HTML.write_text("".join(parts))


# ----- Data helpers --------------------------------------------------------

def _latest_row_for(conn, fmid: str):
    return conn.execute(
        """
        SELECT sec.fmid, sec.activity_code, sec.name, sec.type_code, sec.location, sec.days,
               sec.time_start, sec.time_end, sec.ages, sec.cost,
               s.status, s.enrolled, s.waitlist, s.ts
        FROM sections sec
        LEFT JOIN (
            SELECT * FROM snapshots WHERE fmid = ? ORDER BY ts DESC LIMIT 1
        ) s ON s.fmid = sec.fmid
        WHERE sec.fmid = ?
        """,
        (fmid, fmid),
    ).fetchone()


def _sellout_rows(conn) -> list[dict]:
    """For each section that has ever been observed as Waitlist or Full, compute
    the upper bound on sell-out duration:

        start_ts  = REG_OPENS_AT_UTC[reg_event] if known,
                    else last observed Available/Unavailable snapshot before scarce
        scarce_ts = first observed Waitlist or Full snapshot
        duration  = scarce_ts - start_ts (clamped to >= 0)
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
        )
        SELECT
            fs.fmid,
            sec.name, sec.activity_code, sec.type_code, sec.reg_event,
            sec.days, sec.time_start, sec.time_end, sec.location, sec.ages,
            lo.open_ts,
            fs.scarce_ts,
            (SELECT status FROM snapshots WHERE fmid=fs.fmid AND ts=fs.scarce_ts
             ORDER BY ts LIMIT 1) AS scarce_status,
            (SELECT status FROM snapshots WHERE fmid=fs.fmid
             ORDER BY ts DESC LIMIT 1) AS current_status
        FROM first_scarce fs
        LEFT JOIN last_open lo ON lo.fmid = fs.fmid
        JOIN sections sec ON sec.fmid = fs.fmid
        """
    ).fetchall()

    out: list[dict] = []
    for r in raw:
        d = dict(r)
        scarce_dt = dt.datetime.fromisoformat(d["scarce_ts"])
        # Choose the start: known registration open time wins when available
        reg_open_iso = config.REG_OPENS_AT_UTC.get(d["reg_event"] or "")
        if reg_open_iso:
            start_dt = dt.datetime.fromisoformat(reg_open_iso)
            start_source = "reg-open"
        elif d["open_ts"]:
            start_dt = dt.datetime.fromisoformat(d["open_ts"])
            start_source = "last-poll"
        else:
            # First observation already scarce, no known reg-open → can't compute
            continue
        duration_s = max(0, int((scarce_dt - start_dt).total_seconds()))
        d["start_ts_utc"] = start_dt.isoformat()
        d["start_source"] = start_source
        d["duration_s"] = duration_s
        out.append(d)
    out.sort(key=lambda x: x["duration_s"])
    return out


# ----- Formatting helpers --------------------------------------------------

def _fmt_et(ts_utc_iso: str | None) -> str:
    """'2026-05-12T16:14:47+00:00' → 'May 12, 12:14 PM ET'"""
    if not ts_utc_iso:
        return ""
    try:
        u = dt.datetime.fromisoformat(ts_utc_iso)
    except ValueError:
        return ts_utc_iso
    e = u.astimezone(ET)
    return e.strftime("%b %d, %-I:%M:%S %p ET")


def _fmt_et_short(ts_utc_iso: str | None) -> str:
    """Same as _fmt_et but without seconds (for compact cells)."""
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


# ----- Table renderers -----------------------------------------------------

def _render_table(rows, *, include_actions: bool, table_id: str | None = None) -> str:
    table_attrs = f" id='{table_id}'" if table_id else ""
    head = (
        f"<table{table_attrs}><thead><tr>"
        "<th>Status</th><th>Type</th><th>Class</th><th>Activity</th><th>When</th>"
        "<th>Location</th><th>Ages</th><th>Enrolled</th><th>Waitlist</th>"
        "<th>Last seen (ET)</th>"
    )
    if include_actions:
        head += "<th>Link</th>"
    head += "</tr></thead><tbody>"
    body: list[str] = [head]
    for r in rows:
        time_label = f"{r['days'] or ''} {r['time_start'] or ''}-{r['time_end'] or ''}".strip()
        status = r["status"] or "?"
        type_code = r["type_code"] or ""
        type_label = config.TYPE_LABELS.get(type_code, type_code)
        link = (
            f"<a href='https://vaarlingtonweb.myvscloud.com/webtrac/web/iteminfo.html?"
            f"Module=AR&FMID={html.escape(r['fmid'])}' target='_blank'>open</a>"
        ) if include_actions else ""
        search_blob = " ".join(filter(None, [
            r["name"], r["activity_code"], r["location"], time_label,
            r["ages"], type_label, r["fmid"],
        ])).lower()
        ts_iso = r["ts"] or ""
        body.append(
            f"<tr"
            f" data-status='{html.escape(status)}'"
            f" data-type='{html.escape(type_code)}'"
            f" data-search='{html.escape(search_blob)}'"
            f">"
            f"<td><span class='pill pill--{_pill_class(status)}'>{html.escape(status)}</span></td>"
            f"<td>{html.escape(type_label)}</td>"
            f"<td>{html.escape(r['name'] or '')}</td>"
            f"<td>{html.escape(r['activity_code'] or '')}</td>"
            f"<td>{html.escape(time_label)}</td>"
            f"<td>{html.escape(r['location'] or '')}</td>"
            f"<td>{html.escape(r['ages'] or '')}</td>"
            f"<td>{'' if r['enrolled'] is None else r['enrolled']}</td>"
            f"<td>{'' if r['waitlist'] is None else r['waitlist']}</td>"
            f"<td><time datetime='{html.escape(ts_iso)}'>{html.escape(_fmt_et_short(ts_iso))}</time></td>"
            f"{('<td>' + link + '</td>') if include_actions else ''}"
            f"</tr>"
        )
    body.append("</tbody></table>")
    return "".join(body)


def _render_filterable_table(rows, *, table_id: str, include_actions: bool) -> str:
    if not rows:
        return ""
    statuses = sorted({r["status"] for r in rows if r["status"]})
    types_seen = sorted({r["type_code"] or "" for r in rows})
    status_options = "".join(
        f"<option value='{html.escape(s)}'>{html.escape(s)}</option>" for s in statuses
    )
    type_options = "".join(
        f"<option value='{html.escape(c)}'>{html.escape(config.TYPE_LABELS.get(c, c) or '(untyped)')}</option>"
        for c in types_seen
    )
    filters = (
        f"<div class='filters'>"
        f"<input type='search' id='{table_id}-q' placeholder='Search name, location, day, time…'>"
        f"<select id='{table_id}-status'><option value=''>All statuses</option>{status_options}</select>"
        f"<select id='{table_id}-type'><option value=''>All types</option>{type_options}</select>"
        f"<span class='count'>Showing <b id='{table_id}-shown'>{len(rows)}</b> of {len(rows)}</span>"
        f"</div>"
    )
    table = "<div class='scroll-box'>" + _render_table(
        rows, include_actions=include_actions, table_id=table_id
    ) + "</div>"
    script = (
        "<script>(function(){"
        f"var id={table_id!r};"
        "var q=document.getElementById(id+'-q'),"
        "ss=document.getElementById(id+'-status'),"
        "ts=document.getElementById(id+'-type'),"
        "shown=document.getElementById(id+'-shown'),"
        "tbl=document.getElementById(id);"
        "function apply(){"
        "var qv=q.value.toLowerCase().trim(),sv=ss.value,tv=ts.value,n=0;"
        "tbl.querySelectorAll('tbody tr').forEach(function(tr){"
        "var m=(!sv||tr.dataset.status===sv)"
        "&&(!tv||tr.dataset.type===tv)"
        "&&(!qv||tr.dataset.search.indexOf(qv)>=0);"
        "tr.style.display=m?'':'none';if(m)n++;"
        "});shown.textContent=n;}"
        "q.addEventListener('input',apply);"
        "ss.addEventListener('change',apply);"
        "ts.addEventListener('change',apply);"
        "})();</script>"
    )
    return filters + table + script


def _render_sellout_table(rows) -> str:
    head = (
        "<table><thead><tr>"
        "<th>Sold out within</th>"
        "<th>Final status</th>"
        "<th>Type</th><th>Class</th><th>Activity</th><th>When</th>"
        "<th>Location</th><th>Ages</th>"
        "<th>Registration opened (ET)</th>"
        "<th>First scarce (ET)</th>"
        "<th>Link</th>"
        "</tr></thead><tbody>"
    )
    body = [head]
    for r in rows:
        time_label = f"{r['days'] or ''} {r['time_start'] or ''}-{r['time_end'] or ''}".strip()
        type_label = config.TYPE_LABELS.get(r["type_code"] or "", r["type_code"] or "")
        status = r["current_status"] or r["scarce_status"] or "?"
        # The displayed "registration opened" timestamp: from REG_OPENS_AT_UTC if
        # known (start_source='reg-open'), otherwise from the last-observed
        # pre-scarcity poll. The cell title surfaces which it is, since the
        # latter is only an upper bound.
        start_title = "registration window" if r["start_source"] == "reg-open" else "last poll before sell-out (upper bound)"
        body.append(
            f"<tr>"
            f"<td><b>≤ {_fmt_duration(r['duration_s'])}</b></td>"
            f"<td><span class='pill pill--{_pill_class(status)}'>{html.escape(status)}</span></td>"
            f"<td>{html.escape(type_label)}</td>"
            f"<td>{html.escape(r['name'] or '')}</td>"
            f"<td>{html.escape(r['activity_code'] or '')}</td>"
            f"<td>{html.escape(time_label)}</td>"
            f"<td>{html.escape(r['location'] or '')}</td>"
            f"<td>{html.escape(r['ages'] or '')}</td>"
            f"<td title='{html.escape(start_title)}'>{html.escape(_fmt_et_short(r['start_ts_utc']))}</td>"
            f"<td>{html.escape(_fmt_et_short(r['scarce_ts']))}</td>"
            f"<td><a href='https://vaarlingtonweb.myvscloud.com/webtrac/web/iteminfo.html?"
            f"Module=AR&FMID={html.escape(r['fmid'])}' target='_blank'>open</a></td>"
            f"</tr>"
        )
    body.append("</tbody></table>")
    return "".join(body)


_HTML_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Arlington Park & Rec — registration monitor</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --bg: #fafafa; --fg: #111; --dim: #777; --line: #e3e3e3;
    --open: #2e7d32; --wait: #c77c00; --full: #c62828; --off: #555;
  }}
  body {{ font: 14px/1.45 -apple-system, system-ui, sans-serif; margin: 2em auto; max-width: 1100px;
         padding: 0 1em; color: var(--fg); background: var(--bg); }}
  h1 {{ margin-bottom: .2em; }}
  h2 {{ margin-top: 2em; border-bottom: 1px solid var(--line); padding-bottom: .2em; }}
  .meta, .dim {{ color: var(--dim); }}
  .lede {{ color: var(--fg); max-width: 70ch; }}
  .lede p {{ margin: .4em 0; }}
  /* Scrollable table container — default size ≈ 20 rows.
     A row is ~26px (line-height 1.45 × 14px font + 4px padding × 2 ≈ 28px including border).
     20 × 28 + 32 (sticky header) ≈ 592px. */
  .scroll-box {{ max-height: 592px; overflow: auto;
                 border: 1px solid var(--line); border-radius: 4px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line);
           vertical-align: top; white-space: nowrap; }}
  th {{ font-weight: 600; background: #f0f0f0; position: sticky; top: 0; z-index: 1; }}
  .pill {{ display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 11px;
          font-weight: 600; color: white; }}
  .pill--open {{ background: var(--open); }}
  .pill--wait {{ background: var(--wait); }}
  .pill--full {{ background: var(--full); }}
  .pill--off  {{ background: var(--off); }}
  a {{ color: #1a64c8; }}
  .filters {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
              margin: 8px 0 12px; }}
  .filters input, .filters select {{
      padding: 4px 8px; font-size: 13px;
      border: 1px solid var(--line); border-radius: 4px; background: white; }}
  .filters input[type=search] {{ min-width: 240px; flex: 1; max-width: 380px; }}
  .filters .count {{ color: var(--dim); font-size: 13px; margin-left: auto; }}
</style>
</head><body>
<h1>Arlington Park &amp; Rec — registration monitor</h1>
<div class='lede'>
  <p>This page tracks how quickly Arlington VA Park &amp; Rec classes fill up
     during their registration windows. A scheduled GitHub Actions job polls
     the public <a href='https://vaarlingtonweb.myvscloud.com/webtrac/web/search.html?interfaceparameter=WebTrac'>WebTrac</a>
     catalog and records each section's status (Available / Waitlist / Full /
     Unavailable) over time into the SQLite database below.</p>
  <p>The <b>Sell-out speed</b> table shows the upper-bound time it took for each
     section to transition from open to scarce — shortest first. Use the filters
     on the <b>Currently open</b> table to narrow by status or activity type.</p>
  <p class='meta'>Last update: {now}</p>
</div>
"""

_HTML_FOOT = "</body></html>"


if __name__ == "__main__":
    raise SystemExit(main())
