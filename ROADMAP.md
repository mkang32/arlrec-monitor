# Roadmap — multi-county / multi-season expansion

The current codebase is shaped around "one county (Arlington), one season
(summer 2026), one user." Designed to expand to:
- All 4 registration seasons per year (spring/summer/fall/winter)
- Nearby counties (Fairfax, Prince George's, possibly Alexandria, Loudoun)
- Multiple years of historical data
- Possibly shareable with other parents

## Open questions to resolve before starting

1. What registration system does **Fairfax County** use? (likely ParkPass, verify)
2. What does **Prince George's County** use? (CivicRec / Daysmart, probably)
3. Does Alexandria use WebTrac (same as Arlington)?
4. Stay personal-use, or build toward a shareable tool? Determines whether
   Phase 5 (multi-user) is ever needed.
5. OK with introducing one hosted service (DB), or stick to pure git +
   GH Pages free tier?

## Phase 1 — Schema reshape (half-day, do first)

Add `(site, season)` dimensions without changing infrastructure:
- `sites` table: `id, name, vendor, base_url, timezone`
- `seasons` table: `id, site_id, name, registration_events, registration_opens_at_utc`
- Add `site_id` + `season_id` to `sections` (snapshots inherit via section)
- Migrate existing data with `site='arlington'`, `season='2026-summer'`

## Phase 2 — Vendor abstraction (one day per vendor)

Split `scraper/webtrac.py` into:
- `scraper/sites/base.py` — abstract `SiteClient` interface
- `scraper/sites/webtrac.py` — Vermont Systems implementation (current code)
- `scraper/sites/parkpass.py` — Fairfax (new)
- `scraper/sites/civicrec.py` — PG County (new)

Each vendor exposes the same interface so adding a new one is contained.

## Phase 3 — Config-driven (half-day, big readability win)

Replace `scraper/config.py` with YAML configs:
- `configs/sites/<site>.yaml` — vendor, base_url, type_labels
- `configs/seasons/<site>-<year>-<season>.yaml` — events, watch_fmids, opens_at times
- `configs/global.yaml` — alert transitions, polling cadences

Workflows iterate active season configs. Adding a season becomes a YAML file
edit, no Python changes.

## Phase 4 — Hosted DB (only when sqlite-in-git hurts)

Trigger: when `data/snapshots.sqlite` clones get slow or hit GitHub's 100MB cap.
Years away at current growth rate.

Move to **Turso** (free SQLite cloud) or **Neon/Supabase** (free Postgres).
Scraper writes to remote; static site reads via daily JSON export.

## Phase 5 — Multi-user (only if going shareable, big scope jump)

Per-user watchlists, GitHub OAuth, per-user alert routing. This is the
"now it's a product" inflection point. Skip unless real demand.

## Anti-todos (things to NOT do prematurely)

- No backend framework (FastAPI, etc.) before Phase 5
- No repo split until vendor scrapers are reused by a third party
- No Postgres migration just for "scaling" — SQLite handles billions of rows fine
- No queue/worker system — GH Actions cron is fine at this volume even after expansion

## How to apply

When a future session asks about expanding the project (more counties, more
seasons, sharing), open this file first, surface the open questions, and
propose starting with Phase 1.
