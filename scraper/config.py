"""Static config. Edit this file to change what's monitored."""
from __future__ import annotations

# Sections the user wants email alerts on (Fin 1 Aquatics for their kid).
WATCH_FMIDS: list[str] = [
    "350201712",
    "350201635",
]

# State transitions that should trigger an alert (prev -> new).
# We alert on anything going scarcer, but not the reverse (cancellations).
ALERT_TRANSITIONS: list[tuple[str | None, str]] = [
    (None, "Available"),          # first time we see it open
    ("Unavailable", "Available"), # registration just opened
    ("Available", "Waitlist"),
    ("Waitlist", "Full"),
    ("Available", "Full"),        # filled so fast we never saw Waitlist
]

# Type-code filter values to iterate during catalog scans. From the type dropdown.
# Focused on kids/family-relevant categories; trim or expand as desired.
KID_TYPES: list[str] = [
    "AQUAT",       # Aquatics
    "AQUATPVT",    # Aquatics - Private Lessons
    "CRART",       # Arts & Crafts
    "CERAM",       # Ceramics
    "COOKI",       # Cooking
    "DANCE",       # Dance
    "DWPT",        # Drawing/Painting
    "FAMLY",       # Family Programs
    "GYMNA",       # Gymnastics
    "JEWEL",       # Jewelry
    "LANGU",       # Language
    "MARTS",       # Martial Arts
    "MOVE",        # Movement
    "MUSIC",       # Music
    "NCTR",        # Nature & History
    "OST",         # Out of School Time
    "PHOTO",       # Photography
    "PREK",        # Preschool/Playgroups
    "SCDI",        # Science & Discovery
    "FIBER",       # Sewing & Fiber Arts
    "SPORT",       # Sports
    "SPCDI",       # Sports Clinics/Drop In
    "LEAGUE",      # Sports League
    "CAMP",        # Summer Camp
    "TEEN",        # Teen Programs
    "TENIS",       # Tennis
    "THEAT",       # Theater
    "WELLN",       # Wellness
    "WOOD",        # Woodworking
    "YOGA",        # Yoga
]

# Registration-event filters for narrow hot-window polling.
# These match the actual events in the registrationevent dropdown.
REG_EVENTS = {
    "gymnastics_2026_summer": "ENJOYSUMMER1",  # 2026-05-12 12:00 ET
    "aquatics_2026_summer":   "ENJOYSUMMER2",  # 2026-05-13 12:00 ET
    "other_2026_summer":      "ENJOYSUMMER",   # 2026-05-14 12:00 ET
    "camp_2026_dpr":          "CAMP",
    "camp_2026_partner":      "CAMP1",
}

DB_PATH = "data/snapshots.sqlite"
