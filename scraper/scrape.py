"""Main scraper entry point.

Modes:
    --mode watchlist     poll only WATCH_FMIDs (fast; used during hot windows)
    --mode catalog       enumerate full catalog via type filters (used routinely)
    --mode types         like catalog, but ONLY updates section metadata
                         (esp. type_code) — no status snapshots, no alerts
    --mode event EVENT   poll one registrationevent (e.g. ENJOYSUMMER1)

Each run writes one snapshot per section to data/snapshots.sqlite and sends
email alerts for state transitions listed in ALERT_TRANSITIONS.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import traceback

from . import config, db, notify
from .webtrac import EnrollmentCounts, SectionRow, WebTracClient

log = logging.getLogger("scrape")


def utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _record(conn, ts: str, row: SectionRow, counts: EnrollmentCounts | None,
            *, type_code: str | None = None, reg_event: str | None = None,
            season_id: str = db.DEFAULT_SEASON_ID) -> str | None:
    """Insert metadata + snapshot for one section. Returns prior status if any."""
    prev = db.latest_status(conn, row.fmid)
    db.upsert_section(
        conn,
        fmid=row.fmid,
        activity_code=row.activity_code,
        name=row.name,
        type_code=type_code,
        reg_event=reg_event,
        date_start=row.date_start,
        date_end=row.date_end,
        time_start=row.time_start,
        time_end=row.time_end,
        days=row.days,
        location=row.location,
        ages=row.ages,
        cost=row.cost,
        ts=ts,
        season_id=season_id,
    )
    db.insert_snapshot(
        conn,
        fmid=row.fmid,
        ts=ts,
        status=row.status,
        enrolled=counts.enrolled if counts else None,
        waitlist=counts.waitlist if counts else None,
    )
    return prev


def _maybe_alert(conn, *, row: SectionRow, prev_status: str | None,
                 counts: EnrollmentCounts | None, ts: str) -> None:
    if row.fmid not in config.WATCH_FMIDS and row.activity_code not in config.WATCH_ACTIVITY_CODES:
        return
    transition = (prev_status, row.status)
    if transition not in config.ALERT_TRANSITIONS:
        return
    if not notify.alerts_configured():
        log.warning("alert wanted for %s (%s->%s) but email creds not configured",
                    row.fmid, prev_status, row.status)
        return
    counts_line = (
        f"\nEnrolled: {counts.enrolled} | Waitlist: {counts.waitlist}"
        if counts else ""
    )
    subject = f"[ARL-REC] {row.name} ({row.activity_code}) → {row.status}"
    body = (
        f"Section status changed: {prev_status or 'unseen'} → {row.status}\n\n"
        f"Class:    {row.name} ({row.activity_code})\n"
        f"FMID:     {row.fmid}\n"
        f"When:     {row.days or '?'} {row.time_start}-{row.time_end} "
        f"({row.date_start}-{row.date_end})\n"
        f"Where:    {row.location}\n"
        f"Ages:     {row.ages}\n"
        f"Cost:     {row.cost}"
        f"{counts_line}\n\n"
        f"Snapshot at {ts}"
    )
    try:
        notify.send_alert(subject=subject, body=body)
        db.record_alert(conn, fmid=row.fmid, prev_status=prev_status,
                        new_status=row.status, ts=ts)
        log.info("alert sent: %s %s->%s", row.fmid, prev_status, row.status)
    except Exception:
        log.error("alert send failed:\n%s", traceback.format_exc())


def scrape_one_query(client: WebTracClient, conn, *, ts: str,
                     type_code: str | None = None,
                     reg_event: str | None = None,
                     fetch_counts: bool = True,
                     fetch_counts_for_unavailable: bool = False,
                     metadata_only: bool = False,
                     season_id: str = db.DEFAULT_SEASON_ID) -> int:
    """Run one search query, store all returned sections. Returns row count.

    fetch_counts=False skips all enrollment-count lookups (fastest path; used
    during hot windows where status transitions are the time-series of interest
    and counts can be filled in by the routine cron afterwards).

    metadata_only=True upserts only the section row (name, type_code, schedule)
    and skips snapshot inserts + alerts. Used by `--mode types` to backfill
    type_code on sections that were discovered via event-based scrapes.
    """
    rows = client.search(type_code=type_code, registrationevent=reg_event)
    log.info("query (type=%s event=%s) -> %d rows", type_code, reg_event, len(rows))
    # If we searched by event and the event has a known single-category fallback,
    # tag matched sections with that type when no explicit type was used.
    effective_type = type_code or (config.EVENT_TYPE_FALLBACK.get(reg_event or "") if reg_event else None)
    for row in rows:
        if metadata_only:
            db.upsert_section(
                conn, fmid=row.fmid, activity_code=row.activity_code, name=row.name,
                type_code=effective_type, reg_event=reg_event,
                date_start=row.date_start, date_end=row.date_end,
                time_start=row.time_start, time_end=row.time_end, days=row.days,
                location=row.location, ages=row.ages, cost=row.cost, ts=ts,
                season_id=season_id,
            )
            continue
        counts: EnrollmentCounts | None = None
        if fetch_counts and (row.status != "Unavailable" or fetch_counts_for_unavailable):
            try:
                counts = client.enrollment_counts(row.fmid)
            except Exception:
                log.warning("enrollment fetch failed for %s:\n%s",
                            row.fmid, traceback.format_exc())
        prev = _record(conn, ts, row, counts, type_code=effective_type, reg_event=reg_event,
                       season_id=season_id)
        _maybe_alert(conn, row=row, prev_status=prev, counts=counts, ts=ts)
    return len(rows)


def scrape_watchlist(client: WebTracClient, conn, *, ts: str) -> int:
    """Poll each watch-list FMID directly. Uses the section's own iteminfo page
    for enrollment counts; status comes from search-by-keyword fallback OR
    from the iteminfo page text. We use a keyword search per FMID so we also
    get fresh metadata (status, dates, etc.)."""
    n = 0
    for fmid in config.WATCH_FMIDS:
        try:
            counts = client.enrollment_counts(fmid)
        except Exception:
            log.warning("enrollment fetch failed for watchlist %s", fmid, exc_info=True)
            counts = None
        # Use a synthetic row — we don't have the rich search-table data here,
        # but db.upsert keeps any previously-stored metadata via COALESCE.
        # Status: if we can hit the enrollment page successfully, infer at least
        # that the section exists. We need the search results to know status,
        # so fall back to a direct primarycode-style search if needed.
        row = _watchlist_status_row(client, fmid)
        if row is None:
            log.warning("watchlist FMID %s: no status row could be parsed", fmid)
            continue
        prev = _record(conn, ts, row, counts)
        _maybe_alert(conn, row=row, prev_status=prev, counts=counts, ts=ts)
        n += 1
    return n


def _watchlist_status_row(client: WebTracClient, fmid: str) -> SectionRow | None:
    """Find a section row for a specific FMID by trying registrationevent searches.
    If not found, we still create a stub row with status Unknown — but ideally
    one of the configured events covers it.
    """
    for ev in config.REG_EVENTS.values():
        try:
            rows = client.search(registrationevent=ev)
        except Exception:
            continue
        for r in rows:
            if r.fmid == fmid:
                return r
    # Fallback: try iteminfo page directly to get the basic name at least.
    # Without status from the results table, mark Unknown.
    return SectionRow(
        fmid=fmid, activity_code="", name="(metadata unknown)",
        status="Unknown", date_start=None, date_end=None,
        time_start=None, time_end=None, days=None, location=None,
        ages=None, cost=None,
    )


def scrape_catalog(client: WebTracClient, conn, *, ts: str,
                   fetch_counts: bool = True, metadata_only: bool = False,
                   season_id: str = db.DEFAULT_SEASON_ID) -> int:
    total = 0
    for type_code in config.ALL_TYPES:
        try:
            total += scrape_one_query(client, conn, ts=ts, type_code=type_code,
                                       fetch_counts=fetch_counts,
                                       metadata_only=metadata_only,
                                       season_id=season_id)
        except Exception:
            log.warning("catalog query failed for type=%s", type_code, exc_info=True)
    # If we're in metadata-only mode, also sweep each single-category registration
    # event so we tag sections that haven't been assigned a WebTrac type yet
    # (common for new-season shadow sections before registration opens).
    if metadata_only:
        for reg_event in config.EVENT_TYPE_FALLBACK:
            try:
                total += scrape_one_query(client, conn, ts=ts, reg_event=reg_event,
                                           metadata_only=True,
                                           season_id=config.EVENT_SEASON.get(reg_event, season_id))
            except Exception:
                log.warning("event-fallback query failed for event=%s",
                            reg_event, exc_info=True)
    return total


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True, choices=["watchlist", "catalog", "types", "event"])
    p.add_argument("--event", help="registrationevent code (for --mode event)")
    p.add_argument("--db", default=config.DB_PATH)
    p.add_argument("--season",
                   help="season_id to tag newly-discovered sections with "
                        "(default: auto-resolved from --event via config.EVENT_SEASON "
                        "for --mode event, else arlington-2026-summer)")
    p.add_argument("--skip-counts", action="store_true",
                   help="Skip per-section enrollment count lookups (faster; status only)")
    args = p.parse_args()

    client = WebTracClient()
    client.warm_up()
    ts = utcnow_iso()

    fetch_counts = not args.skip_counts
    with db.connect(args.db) as conn:
        if args.mode == "watchlist":
            # Watch-list always pulls counts — that's the whole point of the list.
            n = scrape_watchlist(client, conn, ts=ts)
        elif args.mode == "catalog":
            season_id = args.season or db.DEFAULT_SEASON_ID
            n = scrape_catalog(client, conn, ts=ts, fetch_counts=fetch_counts, season_id=season_id)
        elif args.mode == "types":
            season_id = args.season or db.DEFAULT_SEASON_ID
            n = scrape_catalog(client, conn, ts=ts, metadata_only=True, season_id=season_id)
        elif args.mode == "event":
            if not args.event:
                p.error("--event is required when --mode event")
            season_id = args.season or config.EVENT_SEASON.get(args.event, db.DEFAULT_SEASON_ID)
            n = scrape_one_query(client, conn, ts=ts, reg_event=args.event,
                                 fetch_counts=fetch_counts, season_id=season_id)
        else:
            raise AssertionError(args.mode)
    log.info("done: %d sections recorded at %s", n, ts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
