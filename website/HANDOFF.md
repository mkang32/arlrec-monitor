# Handoff — getting this on the website

You're picking up a static-site visualization of Arlington County's class
registration sellout patterns. Everything you need is in this `website/`
directory.

## What's already done

`arlington-class-registration.html` in this directory is a complete,
self-contained page. It renders five Chart.js bar charts and a Grid.js
sortable table from a JSON snapshot embedded in the page itself. No backend
calls. No external data dependencies beyond two CDN script tags
(`cdn.jsdelivr.net`) for Chart.js v4.5 and Grid.js v5.0, both pinned with
subresource-integrity hashes.

The data snapshot bakes in results from `../data/snapshots.sqlite` at build
time. Re-running `python3 build_page.py` (with `build_snapshot.py` as a
sibling) regenerates the HTML with fresh numbers. See `README.md` in this
directory for the file layout and the snapshot's JSON shape.

## What the user wants from you

The user (a non-developer) wants this page published as a standalone URL on
their existing website. Likely tasks for you:

1. **Hosting choice.** Ask the user where their site is hosted. If they don't
   know, the friendliest options are GitHub Pages (free, requires a GitHub
   account) or Netlify Drop (literally drag-and-drop the HTML file). Help
   them set up whichever is most natural for them.
2. **Style integration.** The page uses a neutral light theme (cream
   background `#fafaf7`, dark text, blue/orange chart palette). If the
   user's site has a strong brand, you'll likely want to adjust the CSS in
   `template.html` to match. After editing, regenerate the final HTML with
   `python3 build_page.py`. **Don't edit `arlington-class-registration.html`
   directly** — it's a generated artifact and your changes will be lost the
   next time the user refreshes the data.
3. **Automated refresh.** The current refresh model is manual: user runs
   `build_page.py`, re-uploads the HTML. If they want automatic refreshes
   (e.g. nightly), set up a cron job or GitHub Action that runs the scraper
   + `build_page.py` + pushes to their host.
4. **Navigation / discoverability.** Once published, add a link from the
   user's existing site nav so people can find it.

## What's intentionally not in scope

- **Live data.** The user explicitly chose a static snapshot over a live API
  endpoint. Don't refactor toward fetch-from-server unless they explicitly
  ask for it.
- **A backend.** Same reason.
- **A framework rewrite.** The page is vanilla JS by design. Don't port it
  to React/Vue/etc. unless asked.

## Things to know about the data

- The source database (`../data/snapshots.sqlite`) is populated by an
  automated scraper that polls Arlington County's registration system every
  ~15–20 minutes. The polling interval is the only meaningful caveat
  baked into the visualizations: any class that sold out within one poll
  cycle gets a `sold_out_within_seconds` value matching the poll interval,
  not the true sellout time. The page already explains this in the chart
  descriptions and the "About this data" section — keep that framing if you
  edit copy.
- The "currently_open" table tracks classes from three registration events
  (`ENJOYSUMMER`, `ENJOYSUMMER1`, `ENJOYSUMMER2`) opening May 12–14, 2026.
  Numbers will grow as more events are tracked. The visualization is
  designed to age gracefully — categories with no sellouts simply don't
  appear in the filter dropdown or the location chart.

## Test it before you ship

The user can't easily preview HTML files locally. Help them:

```bash
cd website/
python3 -m http.server 8000
# then open http://localhost:8000/arlington-class-registration.html
```

Verify:

- All four stat cards in the header populate with numbers.
- The category chart at the top shows stacked horizontal bars by
  category, with a legend across the top.
- The category filter dropdown has 7 categories + "All categories"; changing
  it re-renders the four lower charts.
- The time-of-day chart has solid bars (weekday) and striped bars (weekend)
  with a small key directly above the chart.
- The instant-sellout table has 80+ rows, with search and pagination.
- Resize the window to ~400px wide — the layout should reflow cleanly.

If a chart renders empty, check the browser console. The most likely cause
is a structural mismatch between the snapshot keys and what the JS expects;
the `build_snapshot.py` query column names need to stay aligned with the
field names the JS reads.

## Where the user is at

Friendly, technical-adjacent. Not a developer but reads code comfortably.
They built the scraper that populates the sqlite database, so they know the
data well. Don't over-explain the data model; do over-explain the hosting
and deployment steps.
