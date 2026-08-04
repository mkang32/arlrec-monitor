#!/usr/bin/env python3
"""
Reads the Arlington class registration sqlite database and emits a JSON
snapshot used by the static website page (arlington-class-registration.html).

Usage:
    python3 build_snapshot.py [path/to/snapshots.sqlite] [season_id] > snapshot.json

The output is a single JSON object with everything the page needs to render
without any live database calls. Re-run this script whenever you want to
refresh the data on the published page.
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone

# ---- bin SQL fragment matching the page's speed-bin definitions ----
BIN_SQL = (
    "COUNT(*) AS classes, "
    "SUM(CASE WHEN sold_out_within_seconds IS NOT NULL AND sold_out_within_seconds < 1200 THEN 1 ELSE 0 END) AS b_instant, "
    "SUM(CASE WHEN sold_out_within_seconds >= 1200 AND sold_out_within_seconds < 3600 THEN 1 ELSE 0 END) AS b_under1h, "
    "SUM(CASE WHEN sold_out_within_seconds >= 3600 AND sold_out_within_seconds < 21600 THEN 1 ELSE 0 END) AS b_sameday, "
    "SUM(CASE WHEN sold_out_within_seconds >= 21600 AND sold_out_within_seconds < 86400 THEN 1 ELSE 0 END) AS b_nextday, "
    "SUM(CASE WHEN sold_out_within_seconds >= 86400 THEN 1 ELSE 0 END) AS b_multiday, "
    "SUM(CASE WHEN sold_out_within_seconds IS NOT NULL THEN 1 ELSE 0 END) AS sold_out, "
    "ROUND(AVG(sold_out_within_seconds), 0) AS avg_secs"
)

SLOT_EXPR = (
    "CASE "
    "WHEN CAST(SUBSTR(time_start,1,INSTR(time_start,':')-1) AS INTEGER) + CASE WHEN time_start LIKE '%pm' AND CAST(SUBSTR(time_start,1,INSTR(time_start,':')-1) AS INTEGER) < 12 THEN 12 ELSE 0 END < 12 THEN 'Morning' "
    "WHEN CAST(SUBSTR(time_start,1,INSTR(time_start,':')-1) AS INTEGER) + CASE WHEN time_start LIKE '%pm' AND CAST(SUBSTR(time_start,1,INSTR(time_start,':')-1) AS INTEGER) < 12 THEN 12 ELSE 0 END < 16 THEN 'Midday' "
    "WHEN CAST(SUBSTR(time_start,1,INSTR(time_start,':')-1) AS INTEGER) + CASE WHEN time_start LIKE '%pm' AND CAST(SUBSTR(time_start,1,INSTR(time_start,':')-1) AS INTEGER) < 12 THEN 12 ELSE 0 END < 18 THEN 'After-school' "
    "ELSE 'Evening' END"
)

DAYTYPE_EXPR = "CASE WHEN days IN ('Sa','Su') THEN 'Weekend' ELSE 'Weekday' END"

AGE_BUCKET_EXPR = (
    "CASE "
    "WHEN ages LIKE '0%' OR ages LIKE '1.%' THEN 'Babies & toddlers (0-2)' "
    "WHEN ages LIKE '2%' OR ages LIKE '3%' THEN 'Preschool (3-4)' "
    "WHEN ages LIKE '4-4%' OR ages LIKE '4.5%' OR ages LIKE '4-5%' OR ages LIKE '5-5%' OR ages LIKE '5-6%' OR ages LIKE '4-6%' OR ages LIKE '3.25%' THEN 'Pre-K & K (4-6)' "
    "WHEN ages LIKE '5-7%' OR ages LIKE '6-8%' OR ages LIKE '7-8%' OR ages LIKE '6-12%' OR ages LIKE '7-13%' OR ages LIKE '8-12%' OR ages LIKE '9-12%' OR ages LIKE '6-9%' THEN 'School-age (5-12)' "
    "WHEN ages LIKE '13%' OR ages LIKE '14%' OR ages LIKE '15-17%' THEN 'Teen (13-17)' "
    "WHEN ages LIKE '15-99%' OR ages LIKE '16%' OR ages LIKE '18%' OR ages LIKE '21%' THEN 'Adult (15+)' "
    "ELSE 'Other / mixed' END"
)


def category_where(value):
    """SQL fragment to filter by category. '' = all, '__none__' = empty type_label."""
    if value == "":
        return ""
    if value == "__none__":
        return " AND (type_label IS NULL OR type_label = '')"
    safe = value.replace("'", "''")
    return f" AND type_label = '{safe}'"


def season_where(season_id):
    """SQL fragment restricting currently_open to one season (site+season are
    baked into `season_id`, e.g. 'arlington-2026-fall'). Not user input —
    always one of scraper/db.py's known season ids — so simple inlining is fine."""
    safe = season_id.replace("'", "''")
    return f"season_id = '{safe}'"


def fetch(conn, sql):
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


DEFAULT_SEASON_ID = "arlington-2026-summer"


def build_snapshot(db_path, season_id=DEFAULT_SEASON_ID):
    conn = sqlite3.connect(db_path)
    sw = season_where(season_id)

    # ---- universal metadata + headline stats ----
    # We intentionally don't expose "fastest" or "all_times" (for a median).
    # Polling cadence puts a measurement floor on individual durations, so
    # those numbers would be misleading. The "Fast sellouts (<20 min)" bucket
    # is captured by instant_rows below — UI derives count + ratio from it.
    overall = fetch(conn, (
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN sold_out_within_seconds IS NOT NULL THEN 1 ELSE 0 END) AS sold_out, "
        "MAX(reg_opens_at) AS latest_reg "
        f"FROM currently_open WHERE {sw}"
    ))[0]

    # ---- category chart (bin distribution per category) ----
    cat_rows_raw = fetch(conn, (
        "SELECT COALESCE(NULLIF(type_label,''), 'Other') AS category, "
        "sold_out_within_seconds AS secs "
        f"FROM currently_open WHERE {sw} AND sold_out_within_seconds IS NOT NULL"
    ))
    totals_by_cat = fetch(conn, (
        "SELECT COALESCE(NULLIF(type_label,''), 'Other') AS category, "
        "COUNT(*) AS classes, "
        "SUM(CASE WHEN sold_out_within_seconds IS NOT NULL THEN 1 ELSE 0 END) AS sold_out "
        f"FROM currently_open WHERE {sw} GROUP BY category"
    ))

    # ---- filter dropdown population ----
    filter_cats = fetch(conn, (
        "SELECT COALESCE(NULLIF(type_label,''), '__none__') AS cat, "
        "SUM(CASE WHEN sold_out_within_seconds IS NOT NULL THEN 1 ELSE 0 END) AS sold_out, "
        "COUNT(*) AS classes "
        f"FROM currently_open WHERE {sw} GROUP BY cat HAVING sold_out > 0 ORDER BY sold_out DESC"
    ))

    # ---- instant sellouts table ----
    instant_rows = fetch(conn, (
        "SELECT name, COALESCE(NULLIF(type_label,''),'Other') AS category, "
        "location, days, time_start, ages "
        f"FROM currently_open WHERE {sw} AND sold_out_within_seconds IS NOT NULL AND sold_out_within_seconds < 1200 "
        "ORDER BY category ASC, name ASC, location ASC, days ASC, time_start ASC"
    ))

    # ---- per-filter precomputed chart data ----
    filter_values = [""] + [c["cat"] for c in filter_cats]
    per_filter = {}
    for fv in filter_values:
        key = fv if fv else "__all__"
        cw = category_where(fv)

        days = fetch(conn, (
            f"SELECT days, {BIN_SQL} "
            "FROM currently_open "
            f"WHERE {sw} AND days IN ('M','Tu','W','Th','F','Sa','Su'){cw} "
            "GROUP BY days"
        ))
        time_rows = fetch(conn, (
            f"SELECT {DAYTYPE_EXPR} AS daytype, {SLOT_EXPR} AS slot, {BIN_SQL} "
            "FROM currently_open "
            f"WHERE {sw} AND days IN ('M','Tu','W','Th','F','Sa','Su'){cw} "
            "GROUP BY daytype, slot"
        ))
        ages = fetch(conn, (
            f"SELECT {AGE_BUCKET_EXPR} AS bucket, {BIN_SQL} "
            f"FROM currently_open WHERE {sw}{cw} GROUP BY bucket"
        ))
        locations = fetch(conn, (
            f"SELECT location, {BIN_SQL} "
            f"FROM currently_open WHERE {sw} AND location IS NOT NULL{cw} "
            "GROUP BY location HAVING classes >= 3 AND sold_out > 0 "
            "ORDER BY (1.0 * sold_out / classes) DESC, sold_out DESC LIMIT 12"
        ))
        per_filter[key] = {
            "days": days, "time": time_rows, "age": ages, "location": locations,
        }

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "category_rows": cat_rows_raw,
        "totals_by_cat": totals_by_cat,
        "filter_cats": filter_cats,
        "instant_rows": instant_rows,
        "per_filter": per_filter,
    }
    return snapshot


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/snapshots.sqlite"
    season_id = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_SEASON_ID
    snap = build_snapshot(db_path, season_id)
    json.dump(snap, sys.stdout, indent=2, default=str)


if __name__ == "__main__":
    main()
