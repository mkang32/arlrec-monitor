#!/usr/bin/env python3
"""
Generate the social-preview image (Open Graph / Twitter Card) for the
static page.

Outputs `website/og-preview.png` at 1200x630px. Pulls a key stat from the same
sqlite snapshot used by build_page.py, so the image stays in sync with the
published data.

Style: clean, text-focused, matches the page's cream/dark/orange palette.
No charts, no logos — just a headline + one highlighted stat + footer URL.

Usage:
    python3 build_og_image.py [DB]   (default: ../data/snapshots.sqlite)
    SITE_URL=https://your-domain.org python3 build_og_image.py
"""
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from build_snapshot import build_snapshot

OUTPUT = Path(__file__).resolve().parent.parent / "arlington-va" / "2026-summer" / "og-preview.png"
WIDTH, HEIGHT = 1200, 630

# Palette aligned with template.html
BG       = (250, 250, 247)   # #fafaf7
ACCENT   = (227, 132, 16)    # orange — for the headline stat number
DARK     = (26, 26, 26)      # #1a1a1a — primary text
GRAY     = (107, 107, 102)   # #6b6b66 — secondary text
LINK     = (26, 100, 200)    # #1a64c8 — footer URL

# Font lookup. GH Actions runners (Ubuntu) ship DejaVu by default; macOS uses
# Helvetica. Picking the first path that exists keeps both environments happy
# without bundling a TTF in the repo.
FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",  # index 1 is typically Bold; PIL defaults to 0 so this is just regular weight as a fallback
]
FONT_REGULAR_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def load_font(candidates, size):
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def text_width(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def main():
    here = Path(__file__).resolve().parent
    default_db = here.parent / "data" / "snapshots.sqlite"
    db_path = sys.argv[1] if len(sys.argv) > 1 else str(default_db)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    snapshot = build_snapshot(db_path)

    instant_count = len(snapshot.get("instant_rows", []))
    total = snapshot["overall"]["total"]
    sold_out = snapshot["overall"]["sold_out"]

    # Strip protocol from URL for the footer (visual cleanliness)
    site_url_raw = os.environ.get("SITE_URL", "").rstrip("/")
    footer_url = site_url_raw.replace("https://", "").replace("http://", "") or "your-domain-here"

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # 8px top accent stripe
    draw.rectangle([0, 0, WIDTH, 8], fill=ACCENT)

    margin = 80
    y = 90

    # Small uppercase label
    label_font = load_font(FONT_BOLD_PATHS, 22)
    draw.text((margin, y), "ARLINGTON  PARK  &  REC", font=label_font, fill=GRAY)
    y += 56

    # Big headline (two lines)
    headline_font = load_font(FONT_BOLD_PATHS, 68)
    draw.text((margin, y), "How fast Arlington", font=headline_font, fill=DARK)
    y += 80
    draw.text((margin, y), "classes sell out", font=headline_font, fill=DARK)
    y += 110

    # Highlighted stat: big orange number + supporting copy
    big_num_font = load_font(FONT_BOLD_PATHS, 88)
    stat_label_font = load_font(FONT_REGULAR_PATHS, 26)
    num_text = f"{instant_count}"
    draw.text((margin, y), num_text, font=big_num_font, fill=ACCENT)
    num_w = text_width(draw, num_text, big_num_font)
    # Visually anchor the supporting copy to the number's baseline
    draw.text(
        (margin + num_w + 28, y + 36),
        "sections sold out within 20 minutes",
        font=stat_label_font,
        fill=DARK,
    )
    y += 110

    # Subtitle
    subtitle_font = load_font(FONT_REGULAR_PATHS, 22)
    draw.text(
        (margin, y),
        f"Summer 2026 registration analysis · {sold_out} of {total} sections sold out",
        font=subtitle_font,
        fill=GRAY,
    )

    # Footer URL
    footer_font = load_font(FONT_BOLD_PATHS, 24)
    draw.text((margin, HEIGHT - 60), footer_url, font=footer_font, fill=LINK)

    img.save(OUTPUT, "PNG", optimize=True)
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")
    print(f"  headline stat: {instant_count} instant sellouts ({sold_out}/{total} total)")
    print(f"  footer URL:    {footer_url}")


if __name__ == "__main__":
    main()
