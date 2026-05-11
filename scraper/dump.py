"""Render the SQLite snapshots into committed-to-the-repo artifacts:
    data/latest.csv      — one row per section: current status + most recent counts
    data/snapshots.csv   — every snapshot row (history)
    data/index.html      — readable HTML page focused on the watchlist + active sections

Generates everything from data/snapshots.sqlite. Idempotent — safe to re-run.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import sqlite3
from pathlib import Path

from . import config

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "snapshots.sqlite"
LATEST_CSV = DATA_DIR / "latest.csv"
SNAPSHOTS_CSV = DATA_DIR / "snapshots.csv"
# index.html lives at the repo root so GitHub Pages serves it as the site root.
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
            JOIN (
                SELECT fmid, MAX(ts) AS max_ts FROM snapshots GROUP BY fmid
            ) m ON m.fmid = s.fmid AND m.max_ts = s.ts
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


def _dump_index_html(conn) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    section_count, snapshot_count = conn.execute(
        "SELECT (SELECT COUNT(*) FROM sections), (SELECT COUNT(*) FROM snapshots)"
    ).fetchone()

    # Watchlist
    watch_rows = [
        _latest_row_for(conn, fmid) for fmid in config.WATCH_FMIDS
    ]
    watch_rows = [r for r in watch_rows if r is not None]

    # Anything currently non-Unavailable (i.e. registration open or once was)
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

    # Recent interesting transitions across all sections
    transitions = conn.execute(
        """
        WITH ranked AS (
            SELECT s.fmid, s.ts, s.status,
                   LAG(s.status) OVER (PARTITION BY s.fmid ORDER BY s.ts) AS prev_status
            FROM snapshots s
        )
        SELECT r.fmid, sec.name, sec.activity_code, r.ts, r.prev_status, r.status
        FROM ranked r
        JOIN sections sec USING (fmid)
        WHERE r.prev_status IS NOT NULL
          AND r.prev_status != r.status
        ORDER BY r.ts DESC
        LIMIT 40
        """
    ).fetchall()

    parts: list[str] = []
    parts.append(_HTML_HEAD.format(now=html.escape(now)))
    parts.append(
        f"<p class='meta'>{section_count} sections tracked &middot; "
        f"{snapshot_count} snapshots &middot; last update "
        f"<time datetime='{html.escape(now)}'>{html.escape(now)}</time></p>"
    )

    parts.append("<h2>Watchlist</h2>")
    if not watch_rows:
        parts.append("<p class='dim'>No watchlist sections seen yet.</p>")
    else:
        parts.append(_render_table(watch_rows, include_actions=True))

    parts.append("<h2>Currently open (Available, Waitlist, or Full)</h2>")
    if not active:
        parts.append("<p class='dim'>Nothing is open yet. Registration hasn't started.</p>")
    else:
        parts.append(_render_filterable_table(active, table_id="active", include_actions=True))

    parts.append("<h2>Recent status changes</h2>")
    if not transitions:
        parts.append("<p class='dim'>No transitions observed yet.</p>")
    else:
        parts.append("<table><thead><tr><th>When (UTC)</th><th>Class</th>"
                     "<th>Activity</th><th>From</th><th>To</th></tr></thead><tbody>")
        for t in transitions:
            parts.append(
                f"<tr><td><time datetime='{html.escape(t['ts'])}'>{html.escape(t['ts'])}</time></td>"
                f"<td>{html.escape(t['name'] or '')}</td>"
                f"<td>{html.escape(t['activity_code'] or '')}</td>"
                f"<td><span class='pill pill--{_pill_class(t['prev_status'])}'>"
                f"{html.escape(t['prev_status'] or '')}</span></td>"
                f"<td><span class='pill pill--{_pill_class(t['status'])}'>"
                f"{html.escape(t['status'])}</span></td></tr>"
            )
        parts.append("</tbody></table>")

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


def _render_table(rows, *, include_actions: bool, table_id: str | None = None) -> str:
    table_attrs = f" id='{table_id}'" if table_id else ""
    head = (
        f"<table{table_attrs}><thead><tr>"
        "<th>Status</th><th>Type</th><th>Class</th><th>Activity</th><th>When</th>"
        "<th>Location</th><th>Ages</th><th>Enrolled</th><th>Waitlist</th>"
        "<th>Last seen</th>"
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
        # Searchable blob for client-side text filter
        search_blob = " ".join(filter(None, [
            r["name"], r["activity_code"], r["location"], time_label,
            r["ages"], type_label, r["fmid"],
        ])).lower()
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
            f"<td><time datetime='{html.escape(r['ts'] or '')}'>{html.escape(r['ts'] or '')}</time></td>"
            f"{('<td>' + link + '</td>') if include_actions else ''}"
            f"</tr>"
        )
    body.append("</tbody></table>")
    return "".join(body)


def _render_filterable_table(rows, *, table_id: str, include_actions: bool) -> str:
    """Wrap _render_table with a filter bar (search + status + type) and the
    tiny vanilla-JS that wires them up. No external libraries."""
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
    table = _render_table(rows, include_actions=include_actions, table_id=table_id)
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


def _pill_class(status: str | None) -> str:
    return {
        "Available": "open",
        "Waitlist":  "wait",
        "Full":      "full",
        "Unavailable": "off",
    }.get(status or "", "off")


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
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line);
           vertical-align: top; }}
  th {{ font-weight: 600; background: #f0f0f0; position: sticky; top: 0; }}
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
<p class='meta'>Source: <a href='https://vaarlingtonweb.myvscloud.com/webtrac/web/search.html?interfaceparameter=WebTrac'>WebTrac</a>.
Updated by GitHub Actions.</p>
"""

_HTML_FOOT = "</body></html>"


if __name__ == "__main__":
    raise SystemExit(main())
