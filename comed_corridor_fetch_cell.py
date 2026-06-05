# COMED CORRIDOR DETAIL FETCH  -  run ONCE in Jupyter, then re-run the Illinois_Dashboard cell.
# Downloads ComEd's corridor generalization layers (75 buffered, 74 sixteenth, 73 quarter, 72 section)
# into the Illinois (combined)\data folder so individual corridors render crisply at every zoom.
# Uses the same Exelon Referer/Origin headers your comed_scraper.py uses (your own session, legitimate access).
# Source webapp: https://exelonutilities.maps.arcgis.com/apps/webappviewer/index.html?id=c4068de162b943c9bd81fe4c4fbfe0ea
import os, sys, json, time, urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DATA_DIR = Path(r"C:\Users\ahend\Downloads\Decennial Summer Work\Project Reverse Uno\Illinois (combined)\data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

EXELON_PORTAL = "https://exelonutilities.maps.arcgis.com"
BESS_APP_ID   = "c4068de162b943c9bd81fe4c4fbfe0ea"
SERVICE_BASE  = ("https://utility.arcgis.com/usrsvcs/servers/"
                 "9d1c207b6423446ca9eadd78cac261ae/rest/services/"
                 "ComEd_BESS_Hosting_Capacity_032026/FeatureServer")
HEADERS = {
    "Referer": "%s/apps/webappviewer/index.html?id=%s" % (EXELON_PORTAL, BESS_APP_ID),
    "Origin":  EXELON_PORTAL,
    "User-Agent": "Mozilla/5.0 (hosting-capacity-mirror)",
}
NAMEMAP = {75: "comed_corridors.geojson", 74: "comed_sixteenth.geojson",
           73: "comed_quarter.geojson",  72: "comed_section.geojson"}
TARGET_LAYERS = [75, 74, 73, 72]
PAGE_SIZE, MAX_WORKERS = 100, 8

def _post(url, params):
    body = urllib.parse.urlencode(params).encode("utf-8"); last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            for k, v in HEADERS.items(): req.add_header(k, v)
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            req.add_header("Accept-Encoding", "identity")
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError("ArcGIS error: %s" % json.dumps(data["error"])[:300])
            return data
        except (urllib.error.URLError, ValueError, RuntimeError) as exc:
            last = exc
            if attempt == 3: raise
            time.sleep(2 ** attempt)
    raise last

def meta(lid):  return _post("%s/%d" % (SERVICE_BASE, lid), {"f": "json"})
def count(lid):
    return int(_post("%s/%d/query" % (SERVICE_BASE, lid),
               {"where":"1=1","returnCountOnly":"true","f":"json"}).get("count", 0))
def page(lid, off, n, oid):
    d = _post("%s/%d/query" % (SERVICE_BASE, lid),
        {"where":"1=1","outFields":"*","returnGeometry":"true","outSR":"4326",
         "geometryPrecision":"6","f":"geojson","resultOffset":off,"resultRecordCount":n,"orderByFields":oid})
    if d.get("type") != "FeatureCollection":
        raise RuntimeError("layer %d not geojson: %s" % (lid, list(d.keys())[:5]))
    return d.get("features", [])

print("ComEd corridor fetch ->", DATA_DIR)
ok = 0
for lid in TARGET_LAYERS:
    try:
        m = meta(lid); oid = m.get("objectIdField") or "OBJECTID"
        pg = max(1, min(int(m.get("maxRecordCount") or PAGE_SIZE), PAGE_SIZE))
        tot = count(lid); print("\nLayer %d (%s): %d features" % (lid, NAMEMAP[lid], tot))
        offs = list(range(0, tot, pg)); feats = []; done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(page, lid, o, pg, oid): o for o in offs}
            for f in as_completed(futs):
                feats.extend(f.result()); done += 1
                sys.stdout.write("\r  pages %d/%d (%d feats)" % (done, len(offs), len(feats))); sys.stdout.flush()
        sys.stdout.write("\n")
        outp = DATA_DIR / NAMEMAP[lid]
        json.dump({"type":"FeatureCollection","features":feats}, open(outp, "w"))
        print("  saved", outp.name, "(%d features, %.1f MB)" % (len(feats), outp.stat().st_size/1e6)); ok += 1
    except Exception as e:
        print("  ERROR layer %d: %s" % (lid, e))
print("\n%d/4 layers saved. Now RE-RUN the Illinois_Dashboard cell, then Illinois_Server." % ok)
if ok < 4: print("If you saw 403/permission errors, run this from the same machine/session where comed_scraper.py works.")
