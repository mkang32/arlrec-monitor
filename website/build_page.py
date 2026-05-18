#!/usr/bin/env python3
"""
Generate the static Arlington class registration page in one step:

    python3 build_page.py [DB] [TEMPLATE] [OUTPUT]

Defaults:
    DB       = data/snapshots.sqlite
    TEMPLATE = template.html
    OUTPUT   = arlington-class-registration.html

The site URL used in OG/Twitter meta tags comes from the SITE_URL environment
variable (e.g. `SITE_URL=https://staging.example.com python3 build_page.py`).
When unset, falls back to the production custom domain so plain `python3
build_page.py` produces a deployable file with correct social previews.

Reads the sqlite database, computes everything the page needs, then writes a
fully self-contained HTML file with the snapshot baked in. Upload OUTPUT to
any static host. Re-run this whenever you want to refresh the published page.
"""
import json
import os
import sys
from pathlib import Path

from build_snapshot import build_snapshot

PLACEHOLDER = "/*__SNAPSHOT_JSON__*/"
SITE_URL_TOKEN = "__SITE_URL__"
SITE_URL_DEFAULT = "https://selloutwatcher.com"  # production custom domain; override with SITE_URL env for staging


def main():
    # Default paths assume running from repo root (e.g. `python3 website/build_page.py`)
    # or from inside website/ — both supported via the path checks below.
    here = Path(__file__).resolve().parent
    repo_root = here.parent
    default_db = repo_root / "data" / "snapshots.sqlite"
    default_template = here / "template.html"
    default_output = repo_root / "arlington-va" / "2026-summer" / "index.html"

    db_path = sys.argv[1] if len(sys.argv) > 1 else str(default_db)
    template_path = Path(sys.argv[2] if len(sys.argv) > 2 else default_template)
    output_path = Path(sys.argv[3] if len(sys.argv) > 3 else default_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    site_url = os.environ.get("SITE_URL", SITE_URL_DEFAULT).rstrip("/")

    snapshot = build_snapshot(db_path)
    snapshot_json = json.dumps(snapshot, separators=(",", ":"), default=str)

    template = template_path.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        sys.exit(
            f"ERROR: placeholder {PLACEHOLDER!r} not found in {template_path}. "
            "Make sure the template still contains the marker in the SNAPSHOT script tag."
        )
    # Replace placeholder + the inline default {...} immediately after it.
    # The template uses: window.__SNAPSHOT__ = /*__SNAPSHOT_JSON__*/{ ... default ... };
    # Strategy: find the placeholder and replace from there to the next `};` with the new JSON.
    start = template.index(PLACEHOLDER)
    # find the closing `};` after the placeholder (end of the assignment statement)
    end_marker = "};</script>"
    end = template.index(end_marker, start)
    new_assignment = snapshot_json + ";</script>"
    output = template[:start] + new_assignment + template[end + len(end_marker):]

    # Site URL substitution for OG/Twitter meta tags.
    output = output.replace(SITE_URL_TOKEN, site_url)

    output_path.write_text(output, encoding="utf-8")
    print(f"wrote {output_path} ({output_path.stat().st_size:,} bytes)")
    print(f"data snapshot generated at {snapshot['generated_at']}")
    print(f"  {snapshot['overall']['total']} classes tracked, "
          f"{snapshot['overall']['sold_out']} sold out, "
          f"{len(snapshot['instant_rows'])} instant sellouts")
    print(f"site URL: {site_url}")


if __name__ == "__main__":
    main()
