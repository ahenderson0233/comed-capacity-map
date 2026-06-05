# Illinois Capacity & Siting Dashboard (ComEd + Ameren)

Hosting-capacity + site-selection map for ComEd + Ameren Illinois (all values MW).
Static site (GitHub Pages) or local via a small Python server.

## Files (all flat at repo root)
- index.html ................ the app (loads its data from the repo root)
- feeder_segments.json ...... Ameren feeder-segment index
- comed_corridors.geojson ... ComEd buffered-feeder corridors
- comed_townships.geojson ... ComEd township overview
- footprint-proxy.worker.js . OPTIONAL Cloudflare Worker (footprint on static hosting)
- illinois_dashboard_cell.py  builder (regenerates index.html)
- illinois_server_cell.py ... local dev server (/gis + /reportall); key blanked
- comed_corridor_fetch_cell.py one-off ComEd corridor fetch

## GitHub Pages
Commit everything to the repo root, enable Pages (root). Map, utility selector, Find,
popups, capacity layers all work. Footprint runs in direct/estimate mode.

## Full footprint on static hosting
Top of footprint code in index.html:  var PROXY_BASE = "";  // set to your Worker URL
Deploy footprint-proxy.worker.js at workers.cloudflare.com, add SECRET REPORTALL_KEY,
set PROXY_BASE, re-commit. Worker CORS is locked to https://ahenderson0233.github.io .

## Secrets
ReportAll key is blanked in illinois_server_cell.py. Never commit the real key.
