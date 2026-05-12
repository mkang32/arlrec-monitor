"""HTTP + HTML parsing for vaarlingtonweb.myvscloud.com (Vermont Systems WebTrac).

Cloudflare in front of the site blocks plain requests/urllib/curl — we MUST use
curl_cffi with a real browser TLS fingerprint. From GitHub Actions runner IPs,
Cloudflare's bot detection adapts over time: any given fingerprint can flip
from "OK" to "403 Blocked" without warning. We therefore try a list of known-good
fingerprints in priority order, and on a 403 we swap to the next one and rebuild
the session. The first working fingerprint is sticky for the rest of the run.

History:
  2026-05-11: chrome* blocked, safari17_0/safari15_5/firefox133 worked.
  2026-05-12: safari17_0 also blocked; safari15_5/safari18_0/firefox133 still work.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Iterator

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

log = logging.getLogger(__name__)

BASE = "https://vaarlingtonweb.myvscloud.com/webtrac/web"
SEARCH_URL = f"{BASE}/search.html"
ITEMINFO_URL = f"{BASE}/iteminfo.html"
LANDING_PARAMS = {"interfaceparameter": "WebTrac"}

# Try these in order. First one that doesn't 403 wins for the rest of the run.
# Keep at least 3 here — if Cloudflare flips two of them in a single day we
# still have a fallback.
IMPERSONATE_CHAIN = ["safari18_0", "firefox133", "safari15_5", "safari17_0"]

# Status pill values that appear in the results table
STATUS_VALUES = {"Available", "Waitlist", "Full", "Unavailable"}


@dataclass
class SectionRow:
    fmid: str
    activity_code: str        # e.g. "310404-A"
    name: str
    status: str               # Available / Waitlist / Full / Unavailable
    date_start: str | None
    date_end: str | None
    time_start: str | None
    time_end: str | None
    days: str | None
    location: str | None
    ages: str | None
    cost: str | None


@dataclass
class EnrollmentCounts:
    enrolled: int
    waitlist: int


class WebTracClient:
    """Stateful session: holds Cloudflare cookies + CSRF token across requests.

    On a 403, swap to the next fingerprint in IMPERSONATE_CHAIN and rebuild the
    session (cookies + CSRF). One swap per failed request, then propagate.
    """

    def __init__(self, polite_delay_s: float = 0.5,
                 impersonate_chain: list[str] | None = None):
        self._chain = list(impersonate_chain or IMPERSONATE_CHAIN)
        self._chain_idx = 0
        self.session = self._new_session()
        self._csrf: str | None = None
        self._polite_delay_s = polite_delay_s
        self._last_request_at = 0.0

    @property
    def current_fingerprint(self) -> str:
        return self._chain[self._chain_idx]

    def _new_session(self):
        return cffi_requests.Session(impersonate=self.current_fingerprint)

    def _rotate_fingerprint(self) -> bool:
        """Move to next fingerprint, rebuild session, drop CSRF. Returns False
        if no more profiles to try."""
        if self._chain_idx + 1 >= len(self._chain):
            return False
        self._chain_idx += 1
        log.warning("rotating fingerprint to %s after 403", self.current_fingerprint)
        self.session = self._new_session()
        self._csrf = None
        return True

    def _throttle(self):
        elapsed = time.time() - self._last_request_at
        if elapsed < self._polite_delay_s:
            time.sleep(self._polite_delay_s - elapsed)
        self._last_request_at = time.time()

    def _get(self, url: str, params: dict | None = None, timeout: int = 30) -> str:
        self._throttle()
        r = self.session.get(url, params=params, timeout=timeout)
        if r.status_code == 403:
            if self._rotate_fingerprint():
                # CSRF likely invalid for the new session — caller should warm_up again.
                # For warm_up itself, just retry once with the new fingerprint.
                self._throttle()
                r = self.session.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.text

    def warm_up(self) -> None:
        """Hit the search landing page to acquire cookies + CSRF token. Rotates
        fingerprints on 403 until one works or the chain is exhausted."""
        last_exc: Exception | None = None
        for attempt in range(len(self._chain) + 1):
            try:
                html = self._get(SEARCH_URL, LANDING_PARAMS)
                m = re.search(r'name="_csrf_token"[^>]*value="([^"]+)"', html)
                if not m:
                    raise RuntimeError("Could not extract CSRF token from search landing page")
                self._csrf = m.group(1)
                log.info("warm-up OK with fingerprint=%s", self.current_fingerprint)
                return
            except Exception as e:
                last_exc = e
                if not self._rotate_fingerprint():
                    break
        raise RuntimeError(f"warm-up failed across all fingerprints: {last_exc}")

    def _ensure_csrf(self) -> str:
        if not self._csrf:
            self.warm_up()
        assert self._csrf
        return self._csrf

    def search(self, *, type_code: str | None = None, registrationevent: str | None = None,
               keyword: str | None = None) -> list[SectionRow]:
        """Run an Activity search with one filter, paginating until all results are collected.

        WebTrac default page size is 25; pagination param is `Page` (capital P).
        """
        params: dict[str, str] = {
            "display": "detail",
            "module": "AR",
            "Search": "Yes",
            "_csrf_token": self._ensure_csrf(),
        }
        if type_code:
            params["type"] = type_code
        if registrationevent:
            params["registrationevent"] = registrationevent
        if keyword:
            params["keyword"] = keyword

        all_rows: list[SectionRow] = []
        seen_fmids: set[str] = set()
        page = 1
        while True:
            html = self._get(SEARCH_URL, {**params, "Page": str(page)})
            new = list(_parse_results_table(html))
            added = 0
            for r in new:
                if r.fmid in seen_fmids:
                    continue
                seen_fmids.add(r.fmid)
                all_rows.append(r)
                added += 1
            total = _parse_total_count(html)
            if total is None:
                # No "Showing results" indicator means no results at all
                break
            shown_to = _parse_shown_upper(html) or len(all_rows)
            if shown_to >= total or added == 0:
                break
            page += 1
            if page > 200:  # paranoid cap
                break
        return all_rows

    def enrollment_counts(self, fmid: str) -> EnrollmentCounts | None:
        """Fetch the enrollment count details modal for one section.

        Returns None if the page doesn't contain enrollment counts (e.g. for
        Unavailable pre-registration sections, the counts may be absent).
        """
        html = self._get(ITEMINFO_URL, {"Module": "AR", "FMID": fmid, "option": "enrollmentcounts"})
        return _parse_enrollment_counts(html)


# ----- Parsers ---------------------------------------------------------------


_FMID_RE = re.compile(r"[?&]FMID=(\d+)")
_SHOWING_RE = re.compile(r"Showing results (\d+)-(\d+) of (\d+)")


def _parse_total_count(html: str) -> int | None:
    m = _SHOWING_RE.search(html)
    return int(m.group(3)) if m else None


def _parse_shown_upper(html: str) -> int | None:
    m = _SHOWING_RE.search(html)
    return int(m.group(2)) if m else None


def _parse_results_table(html: str) -> Iterator[SectionRow]:
    soup = BeautifulSoup(html, "html.parser")
    # WebTrac renders one <table id="arwebsearch_output_table"> per activity group
    # on each page (the id is reused, not unique). Iterate them all.
    tables = soup.find_all("table", id="arwebsearch_output_table")
    if not tables:
        return
    for table in tables:
        tbody = table.find("tbody")
        if not tbody:
            continue
        yield from _parse_table_body(tbody)


def _parse_table_body(tbody) -> Iterator[SectionRow]:
    for tr in tbody.find_all("tr", recursive=False):
        cells = {td.get("data-title"): td for td in tr.find_all("td")}
        if not cells:
            continue

        # FMID lives in any link with FMID=... — pull from Activity # cell
        fmid = None
        act_cell = cells.get("Activity #")
        if act_cell:
            a = act_cell.find("a", href=True)
            if a:
                m = _FMID_RE.search(a["href"])
                if m:
                    fmid = m.group(1)
        if not fmid:
            continue

        status = _cell_text(cells.get("Availability"))
        if status not in STATUS_VALUES:
            # accept anything that's the visible label, but warn-friendly
            status = status or "Unknown"

        date_start, date_end = _split_range(_cell_text(cells.get("Dates")))
        time_start, time_end = _split_range(_cell_text(cells.get("Times")))

        yield SectionRow(
            fmid=fmid,
            activity_code=_cell_text(cells.get("Activity #")) or "",
            name=_cell_text(cells.get("Description")) or "",
            status=status,
            date_start=date_start,
            date_end=date_end,
            time_start=time_start,
            time_end=time_end,
            days=_cell_text(cells.get("Days")),
            location=_cell_text(cells.get("Location")),
            ages=_cell_text(cells.get("Ages")),
            cost=_cell_text(cells.get("Cost")),
        )


def _cell_text(td) -> str | None:
    if td is None:
        return None
    text = td.get_text(" ", strip=True)
    return text or None


_RANGE_DASH_RE = re.compile(r"\s+-\s*")


def _split_range(s: str | None) -> tuple[str | None, str | None]:
    """'04/14/2026 - 06/16/2026' -> ('04/14/2026', '06/16/2026')."""
    if not s:
        return (None, None)
    parts = _RANGE_DASH_RE.split(s, maxsplit=1)
    if len(parts) == 2:
        return (parts[0].strip(), parts[1].strip())
    return (parts[0].strip(), None)


_ENROLLED_RE = re.compile(
    r'Overall Total Enrolled:&nbsp;</strong></span>\s*<span class="info-block-text">(\d+)</span>'
)
_WAITLIST_RE = re.compile(
    r'On Waitlist:&nbsp;</strong></span>\s*<span class="info-block-text">(\d+)</span>'
)


def _parse_enrollment_counts(html: str) -> EnrollmentCounts | None:
    m1 = _ENROLLED_RE.search(html)
    m2 = _WAITLIST_RE.search(html)
    if not m1 or not m2:
        return None
    return EnrollmentCounts(enrolled=int(m1.group(1)), waitlist=int(m2.group(1)))
