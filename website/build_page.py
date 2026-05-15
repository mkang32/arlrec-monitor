#!/usr/bin/env python3
"""
Generate the static Arlington class registration page in one step:

    python3 build_page.py [DB] [TEMPLATE] [OUTPUT]

Defaults:
    DB       = data/snapshots.sqlite
    TEMPLATE = template.html
    OUTPUT   = arlington-class-registration.html

The site URL used in OG/Twitter meta tags comes from the SITE_URL environment
variable (e.g. `SITE_URL=https://example.com python3 build_page.py`). When
unset, falls back to a placeholder so the file is still well-formed for local
preview — but social previews won't render correctly until SITE_URL is set.

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
SITE_URL_DEFAULT = "https://example.com"  # social previews break with this; set SITE_URL env to fix


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/snapshots.sqlite"
    template_path = Path(sys.argv[2] if len(sys.argv) > 2 else "template.html")
    output_path = Path(sys.argv[3] if len(sys.argv) > 3 else "arlington-class-registration.html")

    site_url = os.environ.get("SITE_URL", SITE_URL_DEFAULT).rstrip("/")
    if site_url == SITE_URL_DEFAULT:
        print(f"NOTE: SITE_URL not set; using placeholder {SITE_URL_DEFAULT}. "
              "Social previews (LinkedIn/Twitter/iMessage) will not render the image.",
              file=sys.stderr)

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
