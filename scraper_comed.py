#!/usr/bin/env python3
"""
scraper_comed.py  --  rebuild comed_corridors.geojson + comed_townships.geojson
================================================================================
Pulls ComEd's BESS Hosting Capacity feeder layers (same method as your
comed_scraper.py) and writes the two data files the dashboard loads:

    layer 75 (buffered feeder)  -> comed_corridors.geojson
    layer 71 (township overview)-> comed_townships.geojson

Fields kept on the corridor layer match what index.html renders/queries:
    Feeder_N, PV_HC_kW, EV_HC_kW, BESS_HC, Feeder_Q, SS_N, Queue_RD
Geometry is written at 5-decimal precision and minified, matching the deployed
comed_corridors.geojson (~20 MB, ~5,500 features).

ACCESS: ComEd's FeatureServer is behind an ArcGIS Application Proxy that mints an
anonymous token when the request carries the app's exact Referer + Origin. We set
those headers (public app id). Queries are POSTed and paged by OBJECTID.

Writes both files next to this script (repo root). Exits non-zero if validation
fails, so the workflow leaves the good files in place.

TERMS: ComEd publishes this data for identifying potential DER interconnection
sites only; accuracy is not guaranteed and it may not be redistributed.
Automated republication to a public site leans on that 'no redistribution' note --
confirm you are comfortable with that before scheduling this.

Standard library only.
"""
import os, sys, json, time
import urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else "."
PORTAL = "https://exelonutilities.maps.arcgis.com"
BESS_APP_ID = "c4068de162b943c9bd81fe4c4fbfe0ea"
SERVICE_BASE = ("https://utility.arcgis.com/usrsvcs/servers/"
                "9d1c207b6423446ca9eadd78cac261ae/rest/services/"
                "ComEd_BESS_Hosting_Capacity_032026/FeatureServer")
HEADERS = {"Referer":"%s/apps/webappviewer/index.html?id=%s"%(PORTAL,BESS_APP_ID),
           "Origin":PORTAL, "User-Agent":"Mozilla/5.0 (comed-hosting-refresh)"}
# layer id -> (output filename, fields to keep)
CORR_FIELDS = ["Feeder_N","PV_HC_kW","EV_HC_kW","BESS_HC","Feeder_Q","SS_N","Queue_RD"]
JOBS = [
    (75, "comed_corridors.geojson", CORR_FIELDS),
    (71, "comed_townships.geojson", None),   # None = keep all (small overview layer)
]
PAGE = 100
MAX_WORKERS = 8
PRECISION = 5
# validation guardrails
CORR_MIN, CORR_MAX = 3000, 9000

def post(url, params):
    body=urllib.parse.urlencode(params).encode("utf-8"); last=None
    for att in range(4):
        try:
            r=urllib.request.Request(url, data=body, method="POST")
            for k,v in HEADERS.items(): r.add_header(k,v)
            r.add_header("Content-Type","application/x-www-form-urlencoded"); r.add_header("Accept-Encoding","identity")
            with urllib.request.urlopen(r, timeout=120) as resp:
                d=json.loads(resp.read().decode("utf-8","replace"))
            if isinstance(d,dict) and "error" in d: raise RuntimeError("ArcGIS error: %s"%json.dumps(d["error"])[:300])
            return d
        except (urllib.error.URLError, ValueError, RuntimeError) as e:
            last=e
            if att==3: raise
            time.sleep(2**att)
    raise last

def meta(lid): return post("%s/%d"%(SERVICE_BASE,lid), {"f":"json"})
def count(lid): return int(post("%s/%d/query"%(SERVICE_BASE,lid), {"where":"1=1","returnCountOnly":"true","f":"json"}).get("count",0))

def page(lid, offset, outfields, oid):
    p={"where":"1=1","outFields":(",".join(outfields) if outfields else "*"),
       "returnGeometry":"true","outSR":"4326","geometryPrecision":str(PRECISION),"f":"geojson",
       "resultOffset":offset,"resultRecordCount":PAGE,"orderByFields":oid}
    d=post("%s/%d/query"%(SERVICE_BASE,lid), p)
    if d.get("type")!="FeatureCollection": raise RuntimeError("layer %d not geojson (keys %s)"%(lid,list(d.keys())[:6]))
    return d.get("features",[])

def download(lid, outfields):
    m=meta(lid); oid=m.get("objectIdField") or "OBJECTID"; total=count(lid)
    print("  layer %d: %s features"%(lid,"{:,}".format(total)))
    offsets=list(range(0,total,PAGE)); feats=[]; done=[0]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs={ex.submit(page,lid,o,outfields,oid):o for o in offsets}
        for fu in as_completed(futs):
            feats.extend(fu.result()); done[0]+=1
            sys.stdout.write("\r    pages %d/%d (%s features)"%(done[0],len(offsets),"{:,}".format(len(feats)))); sys.stdout.flush()
    sys.stdout.write("\n")
    return feats

def main():
    t=time.time(); print("ComEd BESS hosting-capacity refresh"); print("Source:",SERVICE_BASE)
    results={}
    for lid, fname, fields in JOBS:
        feats=download(lid, fields)
        if lid==75 and not (CORR_MIN <= len(feats) <= CORR_MAX):
            print("VALIDATION FAILED: corridors=%d outside [%d,%d]; leaving files untouched."%(len(feats),CORR_MIN,CORR_MAX)); sys.exit(1)
        if not feats:
            print("VALIDATION FAILED: layer %d returned 0 features; leaving files untouched."%lid); sys.exit(1)
        if lid==75:
            need=set(f.lower() for f in CORR_FIELDS)
            have=set(k.lower() for k in (feats[0].get("properties") or {}).keys())
            missing=[f for f in CORR_FIELDS if f.lower() not in have]
            if missing: print("VALIDATION FAILED: corridor fields missing %s"%missing); sys.exit(1)
        results[fname]=feats
    # write only after both layers validated
    for fname, feats in results.items():
        path=os.path.join(BASE,fname)
        with open(path,"w",encoding="utf-8") as fh:
            json.dump({"type":"FeatureCollection","features":feats}, fh, separators=(",",":"))
        print("  wrote %s (%s features, %.1f MB)"%(fname,"{:,}".format(len(feats)),os.path.getsize(path)/1e6))
    print("OK in %ds"%round(time.time()-t))

if __name__=="__main__":
    main()
