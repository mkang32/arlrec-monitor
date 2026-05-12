"""Static config. Edit this file to change what's monitored."""
from __future__ import annotations

# Sections the user wants email alerts on.
WATCH_FMIDS: list[str] = [
    "350201712",   # Fin 1 (Aquatics) — Sun 10:35am, Long Bridge
    "350201635",   # Fin 1 (Aquatics) — Fri 4:25pm, Long Bridge
    "350216762",   # Gymnasticats Age 4 — Sat 10:30am, Barcroft
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

# Type-code → human label. Order is meaningful: matches the WebTrac type dropdown.
# Used to enumerate the catalog and to render filterable labels in the viz.
TYPE_LABELS: dict[str, str] = {
    "AQUAT":     "Aquatics",
    "AQUATPVT":  "Aquatics - Private Lessons",
    "CRART":     "Arts & Crafts",
    "CERAM":     "Ceramics",
    "COOKI":     "Cooking",
    "DANCE":     "Dance",
    "DWPT":      "Drawing/Painting",
    "FAMLY":     "Family Programs",
    "GYMNA":     "Gymnastics",
    "JEWEL":     "Jewelry",
    "LANGU":     "Language",
    "MARTS":     "Martial Arts",
    "MOVE":      "Movement",
    "MUSIC":     "Music",
    "NCTR":      "Nature & History",
    "OST":       "Out of School Time",
    "PHOTO":     "Photography",
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
    "WELLN":     "Wellness",
    "WOOD":      "Woodworking",
    "YOGA":      "Yoga",
}

KID_TYPES: list[str] = list(TYPE_LABELS.keys())

# Registration-event filters for narrow hot-window polling.
# These match the actual events in the registrationevent dropdown.
REG_EVENTS = {
    "gymnastics_2026_summer": "ENJOYSUMMER1",  # 2026-05-12 12:00 ET
    "aquatics_2026_summer":   "ENJOYSUMMER2",  # 2026-05-13 12:00 ET
    "other_2026_summer":      "ENJOYSUMMER",   # 2026-05-14 12:00 ET
    "camp_2026_dpr":          "CAMP",
    "camp_2026_partner":      "CAMP1",
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
}

# When each registration event opened (UTC ISO). Used by the viz to compute
# sell-out duration as "scarce_ts - reg_opens_at" rather than the looser
# "scarce_ts - last_open_observation_ts".
REG_OPENS_AT_UTC: dict[str, str] = {
    "ENJOYSUMMER1": "2026-05-12T16:00:00+00:00",
    "ENJOYSUMMER2": "2026-05-13T16:00:00+00:00",
    "ENJOYSUMMER":  "2026-05-14T16:00:00+00:00",
    # CAMP / CAMP1 opened earlier in the year — exact moment unknown; left unset.
}

DB_PATH = "data/snapshots.sqlite"
