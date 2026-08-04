"""Static config. Edit this file to change what's monitored."""
from __future__ import annotations

# Sections the user wants email alerts on.
WATCH_FMIDS: list[str] = [
    "350201712",   # Fin 1 (Aquatics) — Sun 10:35am, Long Bridge
    "350201635",   # Fin 1 (Aquatics) — Fri 4:25pm, Long Bridge
    "350201558",   # Fin 1 (Aquatics) — Tue 5:00pm, Long Bridge
    "350201613",   # Fin 1 (Aquatics) — Thu 5:00pm, Long Bridge
    "350216762",   # Gymnasticats Age 4 — Sat 10:30am, Barcroft
    "350225141",   # Tip Top Ninjas 1 - Mon 4:45 pm, Barcroft
]

# Sections the user wants alerts on, identified by activity_code instead of
# fmid. Use this when the fmid isn't resolvable yet — e.g. new-season sections
# before WebTrac's catalog search comes back online (it goes fully dark in the
# days just before a registration window opens; see CLAUDE.md). Checked in
# addition to WATCH_FMIDS in _maybe_alert, so these still fire on the very
# first scrape that ever sees the section, even though we never looked up
# its fmid ourselves.
WATCH_ACTIVITY_CODES: list[str] = [
    "110405-D",    # Fall 2026 watchlist
    "110405-AA",   # Fall 2026 watchlist
    "110405-AP",   # Fall 2026 watchlist
    "110405-S",    # Fall 2026 watchlist
    "110405-L",    # Fall 2026 watchlist
    "110405-H",    # Fall 2026 watchlist
    "110405-AK",   # Fall 2026 watchlist
    "110405-W",    # Fall 2026 watchlist
]

# State transitions that should trigger an alert (prev -> new).
# We alert on anything going scarcer, but not the reverse (cancellations).
ALERT_TRANSITIONS: list[tuple[str | None, str]] = [
    (None, "Available"),          # first time we see it open
    (None, "Waitlist"),            # first observation already on waitlist
    (None, "Full"),                 # first observation already full (rare but possible)
    ("Unavailable", "Available"), # registration just opened
    ("Unavailable", "Waitlist"),  # filled to waitlist between our polls
    ("Unavailable", "Full"),      # filled completely between our polls
    ("Available", "Waitlist"),
    ("Available", "Full"),         # filled so fast we never saw Waitlist
    ("Waitlist", "Full"),
]

# Type-code → human label. Order matches the WebTrac type dropdown.
# Mirrors the full WebTrac taxonomy — used to enumerate the catalog (via
# `--mode types`) and to render filterable labels in the UI. The initial
# version excluded "adult-only" types (FIT, PILAT, etc.) when this project
# was scoped to kid classes; that scope expanded to ALL classes, and the
# missing types meant some clearly-kid sections (e.g. "Zumba Kids", which
# WebTrac files under FIT) ended up uncategorized. Now includes all 39
# types WebTrac currently exposes.
TYPE_LABELS: dict[str, str] = {
    "OSAP":      "55+ Classes",
    "SAOT":      "55+ Trips",
    "AQUAT":     "Aquatics",
    "AQUATPVT":  "Aquatics - Private Lessons",
    "CRART":     "Arts & Crafts",
    "CERAM":     "Ceramics",
    "COOKI":     "Cooking",
    "DANCE":     "Dance",
    "DOG":       "Dog Obedience",
    "DWPT":      "Drawing/Painting",
    "FAMLY":     "Family Programs",
    "FIT":       "Fitness",
    "GYMNA":     "Gymnastics",
    "TRO":       "Individuals with Disabilities",
    "JEWEL":     "Jewelry",
    "LANGU":     "Language",
    "MARTS":     "Martial Arts",
    "MOVE":      "Movement",
    "MUSIC":     "Music",
    "NCTR":      "Nature & History",
    "OST":       "Out of School Time",
    "PART":      "Party Reservations",
    "PHOTO":     "Photography",
    "PICKLE":    "Pickleball",
    "PILAT":     "Pilates",
    "PREK":      "Preschool/Playgroups",
    "SCDI":      "Science & Discovery",
    "FIBER":     "Sewing & Fiber Arts",
    "SPORT":     "Sports",
    "SPCDI":     "Sports Clinics/Drop In",
    "LEAGUE":    "Sports League",
    "CAMP":      "Summer Camp",
    "TEEN":      "Teen Programs",
    "TENIS":     "Tennis",
    "THEAT":     "Theater",
    "SS":        "This-n-That",
    "WELLN":     "Wellness",
    "WOOD":      "Woodworking",
    "YOGA":      "Yoga",
}

ALL_TYPES: list[str] = list(TYPE_LABELS.keys())

# Registration-event filters for narrow hot-window polling.
# These match the actual events in the registrationevent dropdown.
REG_EVENTS = {
    "gymnastics_2026_summer": "ENJOYSUMMER1",  # 2026-05-12 12:00 ET
    "aquatics_2026_summer":   "ENJOYSUMMER2",  # 2026-05-13 12:00 ET
    "other_2026_summer":      "ENJOYSUMMER",   # 2026-05-14 12:00 ET
    "camp_2026_dpr":          "CAMP",
    "camp_2026_partner":      "CAMP1",
    "gymnastics_2026_fall":   "ENJOYFALL1",    # 2026-08-04 12:00 ET
    "aquatics_2026_fall":     "ENJOYFALL2",    # 2026-08-05 12:00 ET
    "other_2026_fall":        "ENJOYFALL",     # 2026-08-06 12:00 ET
    "55plus_2026_fall":       "55FALL",        # 2026-08-18 — exact time unconfirmed
}

# reg_event -> season_id (scraper/db.py `seasons` table). Lets scrape.py
# auto-resolve which season a `--mode event` scrape belongs to, so workflows
# only need to pass --event, not a redundant --season.
EVENT_SEASON: dict[str, str] = {
    "ENJOYSUMMER1": "arlington-2026-summer",
    "ENJOYSUMMER2": "arlington-2026-summer",
    "ENJOYSUMMER":  "arlington-2026-summer",
    "CAMP":         "arlington-2026-summer",
    "CAMP1":        "arlington-2026-summer",
    "ENJOYFALL1":   "arlington-2026-fall",
    "ENJOYFALL2":   "arlington-2026-fall",
    "ENJOYFALL":    "arlington-2026-fall",
    "55FALL":       "arlington-2026-fall",
}

# Fallback type when a section is discovered via a registrationevent filter but
# not (yet) under any WebTrac `type` filter — common for new-season sessions
# whose category is assigned by WebTrac only after registration opens. Use only
# for events that are single-category; mixed events like ENJOYSUMMER are skipped.
EVENT_TYPE_FALLBACK: dict[str, str] = {
    "ENJOYSUMMER1": "GYMNA",
    "ENJOYSUMMER2": "AQUAT",
    "CAMP":         "CAMP",
    "CAMP1":        "CAMP",
    "ENJOYFALL1":   "GYMNA",
    "ENJOYFALL2":   "AQUAT",
    # ENJOYFALL (Nature/other) and 55FALL are mixed-category, like ENJOYSUMMER —
    # no single fallback type applies, so they're intentionally left unmapped.
}

# When each registration event opened (UTC ISO). Used by the viz to compute
# sell-out duration as "scarce_ts - reg_opens_at" rather than the looser
# "scarce_ts - last_open_observation_ts".
REG_OPENS_AT_UTC: dict[str, str] = {
    "ENJOYSUMMER1": "2026-05-12T16:00:00+00:00",
    "ENJOYSUMMER2": "2026-05-13T16:00:00+00:00",
    "ENJOYSUMMER":  "2026-05-14T16:00:00+00:00",
    # CAMP / CAMP1 opened earlier in the year — exact moment unknown; left unset.
    "ENJOYFALL1":   "2026-08-04T16:00:00+00:00",
    "ENJOYFALL2":   "2026-08-05T16:00:00+00:00",
    "ENJOYFALL":    "2026-08-06T16:00:00+00:00",
    # 55FALL opens 8/18 — exact time of day unconfirmed; left unset.
}

DB_PATH = "data/snapshots.sqlite"
