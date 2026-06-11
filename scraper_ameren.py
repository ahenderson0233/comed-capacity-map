#!/usr/bin/env python3
"""
scraper_ameren.py  --  rebuild feeder_segments.json (Option B: server-side centroids)
=====================================================================================
Reproduces the dashboard's Ameren point index WITHOUT downloading 1.67M polygons.
Instead of pulling full geometry and reducing to a centroid client-side, it asks
ArcGIS for the cell CENTROID directly (returnCentroid=true), which is ~10-50x
smaller and finishes inside a GitHub Actions job.

Output structure is byte-compatible with the live index:
    {"keep":1.0,"cols":["feeder","lon","lat","gen","load"],"segments":[ ... ]}
  - distribution rows: [feeder, lon, lat, gen, load]            (5 elements)
  - subtransmission  : [feeder, lon, lat, gen, 0, 1]            (6 elements, ST flag)

BUILD RULES (verified against the existing index):
  - gen  : IL_HC_Grids/FeatureServer/0   field MAXGENMW_TXT
  - load : AIC_LC_Grids/FeatureServer/0  field MAXLOADMW_TXT
  - join : by cell centroid rounded to 5 decimals
  - comma-packed FEEDERID="A,B" with value "1.2,3.4" -> one row per feeder
  - keep a distribution row if max(gen, load) >= FLOOR (1.0)
  - ST   : ST_Grids/FeatureServer/0, capacity hca_con_txt (auto-detected), load=0,
           keep if hca >= FLOOR

Writes feeder_segments.json next to this script (repo root). Exits non-zero if the
result fails validation, so the GitHub Actions workflow leaves the good file in place.

Standard library only. Public services -- no token.
"""
import os, sys, re, json, time, math, collections, threading
import urllib.request, urllib.parse, urllib.error

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ".",
                   "feeder_segments.json")
ORG = "3jEEGnl6c1x9Sze7"
SVC = "https://services5.arcgis.com/%s/arcgis/rest/services" % ORG
GEN  = SVC + "/IL_HC_Grids/FeatureServer/0"      # generation hosting capacity
LOAD = SVC + "/AIC_LC_Grids/FeatureServer/0"     # load capacity
ST   = SVC + "/ST_Grids/FeatureServer/0"         # subtransmission
FLOOR = 1.0
PRECISION = 5            # centroid rounding (matches existing index)
PAGE = 2000
REQ_PER_MIN = 3500
MAX_429_WAITS = 12
HEADERS = {"User-Agent": "Mozilla/5.0 (ameren-capacity-refresh)", "Accept-Encoding": "identity"}

# ---- expected-range guardrails (validation gate) -------------------------------
EXPECT_ROWS_MIN, EXPECT_ROWS_MAX = 300000, 900000
EXPECT_FEEDERS_MIN = 1500
IL_BBOX = (-91.9, 36.8, -86.9, 42.7)   # lon_min, lat_min, lon_max, lat_max

class _Rate:
    def __init__(s, n): s.n=max(1,int(n)); s.calls=collections.deque(); s.lk=threading.Lock()
    def acquire(s):
        while True:
            with s.lk:
                now=time.monotonic()
                while s.calls and now-s.calls[0]>=60: s.calls.popleft()
                if len(s.calls)<s.n: s.calls.append(now); return
                w=60-(now-s.calls[0])+0.02
            time.sleep(min(max(w,0.01),3.0))
_LIM=_Rate(REQ_PER_MIN)

def _retry_secs(msg):
    m=re.search(r"retry after\s+(\d+)\s*sec", str(msg), re.I); return int(m.group(1)) if m else 60

def req(url, params, method="POST"):
    waits=0; att=0
    while True:
        _LIM.acquire()
        try:
            if method=="POST":
                body=urllib.parse.urlencode(params).encode("utf-8")
                r=urllib.request.Request(url, data=body, method="POST")
                r.add_header("Content-Type","application/x-www-form-urlencoded")
            else:
                r=urllib.request.Request(url+(("?"+urllib.parse.urlencode(params)) if params else ""))
            for k,v in HEADERS.items(): r.add_header(k,v)
            with urllib.request.urlopen(r, timeout=180) as resp:
                d=json.loads(resp.read().decode("utf-8","replace"))
            if isinstance(d,dict) and "error" in d:
                e=d["error"]
                if e.get("code")==429 and waits<MAX_429_WAITS:
                    w=_retry_secs(json.dumps(e)); sys.stdout.write("\n  [quota] 429 wait %ds\n"%w); time.sleep(w); waits+=1; continue
                raise RuntimeError("ArcGIS error: %s"%json.dumps(e)[:300])
            return d
        except urllib.error.HTTPError as ex:
            if ex.code==429 and waits<MAX_429_WAITS:
                w=_retry_secs(ex.headers.get("Retry-After","60")); sys.stdout.write("\n  [quota] HTTP429 wait %ds\n"%w); time.sleep(w); waits+=1; continue
            att+=1
            if att>=5: raise
            time.sleep(2**att)
        except (urllib.error.URLError, ValueError, RuntimeError):
            att+=1
            if att>=5: raise
            time.sleep(2**att)

def fnum(x):
    if x is None: return None
    m=re.search(r"-?\d+(\.\d+)?", str(x)); return float(m.group(0)) if m else None

def cell_feeders(fstr, vstr):
    """FEEDERID 'A,B' + value '1.2,3.4' -> [(feeder, val), ...] (mirrors dashboard cellFeeders)."""
    fs=str(fstr if fstr is not None else "").split(","); vs=str(vstr if vstr is not None else "").split(",")
    out=[]
    for i,f in enumerate(fs):
        f=f.strip()
        if not f: continue
        v=fnum(vs[i]) if i<len(vs) else None
        out.append((f, v))
    return out

def count(url):
    try: return int(req(url+"/query", {"where":"1=1","returnCountOnly":"true","f":"json"}).get("count",0))
    except Exception: return -1

def pull_centroids(url, value_field, label):
    """Page every cell as a centroid; return dict {(lon5,lat5): {feeder: value}}."""
    total=count(url); print("  %s: %s cells"%(label, "{:,}".format(total) if total>=0 else "?"))
    out={}; offset=0; got=0
    while True:
        d=req(url+"/query", {"where":"1=1","outFields":"FEEDERID,"+value_field,
                             "returnGeometry":"false","returnCentroid":"true","outSR":"4326",
                             "orderByFields":"OBJECTID","resultOffset":offset,"resultRecordCount":PAGE,"f":"json"})
        feats=d.get("features",[])
        if not feats: break
        for ft in feats:
            a=ft.get("attributes",{}) or {}
            c=ft.get("centroid") or {}
            x=c.get("x"); y=c.get("y")
            if x is None or y is None: continue
            key=(round(x,PRECISION), round(y,PRECISION))
            slot=out.get(key)
            if slot is None: slot=out[key]={}
            for feeder,val in cell_feeders(a.get("FEEDERID"), a.get(value_field)):
                if val is None: continue
                if feeder not in slot or val>slot[feeder]: slot[feeder]=val
        got+=len(feats); offset+=len(feats)
        sys.stdout.write("\r    %s pulled %s"%(label, "{:,}".format(got))); sys.stdout.flush()
        if len(feats)<PAGE: break
    sys.stdout.write("\n")
    return out

def build_distribution():
    gen = pull_centroids(GEN,  "MAXGENMW_TXT",  "gen (IL_HC_Grids)")
    load= pull_centroids(LOAD, "MAXLOADMW_TXT", "load (AIC_LC_Grids)")
    rows=[]; keys=set(gen.keys()) | set(load.keys())
    for key in keys:
        lon,lat=key; gslot=gen.get(key,{}); lslot=load.get(key,{})
        for feeder in (set(gslot.keys()) | set(lslot.keys())):
            g=gslot.get(feeder); l=lslot.get(feeder)
            gv=g if g is not None else 0.0; lv=l if l is not None else 0.0
            if max(gv,lv) < FLOOR: continue
            rows.append([feeder, lon, lat, round(gv,2), round(lv,2)])
    print("  distribution rows kept (max(gen,load)>=%.1f): %s"%(FLOOR, "{:,}".format(len(rows))))
    return rows

def build_st():
    total=count(ST); print("  ST (ST_Grids): %s cells"%("{:,}".format(total) if total>=0 else "?"))
    # auto-detect feeder + capacity field from first page
    feeder_keys=["feeder","FEEDERID","FEEDER","feeder_id"]; hca_keys=["hca_con_txt","MAXGENMW_TXT","hca","HCA","HCA_TXT"]
    rows=[]; offset=0; got=0; fk=hk=None
    while True:
        d=req(ST+"/query", {"where":"1=1","outFields":"*","returnGeometry":"false","returnCentroid":"true",
                            "outSR":"4326","orderByFields":"OBJECTID","resultOffset":offset,"resultRecordCount":PAGE,"f":"json"})
        feats=d.get("features",[])
        if not feats: break
        if fk is None:
            a0=feats[0].get("attributes",{}) or {}
            fk=next((k for k in feeder_keys if k in a0), None); hk=next((k for k in hca_keys if k in a0), None)
            print("    ST feeder field: %s | capacity field: %s"%(fk,hk))
            if not fk or not hk: raise RuntimeError("ST field auto-detect failed; keys=%s"%list(a0.keys()))
        for ft in feats:
            a=ft.get("attributes",{}) or {}; c=ft.get("centroid") or {}
            x=c.get("x"); y=c.get("y")
            if x is None or y is None: continue
            f=a.get(fk); g=fnum(a.get(hk))
            if f and g is not None and g>=FLOOR:
                rows.append([str(f), round(x,PRECISION), round(y,PRECISION), round(g,3), 0, 1])
        got+=len(feats); offset+=len(feats)
        sys.stdout.write("\r    ST pulled %s (kept %s)"%("{:,}".format(got),"{:,}".format(len(rows)))); sys.stdout.flush()
        if len(feats)<PAGE: break
    sys.stdout.write("\n")
    print("  ST rows kept (hca>=%.1f): %s"%(FLOOR,"{:,}".format(len(rows))))
    return rows

def validate(segments):
    n=len(segments)
    if not (EXPECT_ROWS_MIN <= n <= EXPECT_ROWS_MAX): return "row count %d outside [%d,%d]"%(n,EXPECT_ROWS_MIN,EXPECT_ROWS_MAX)
    feeders=set(s[0] for s in segments)
    if len(feeders) < EXPECT_FEEDERS_MIN: return "only %d distinct feeders (<%d)"%(len(feeders),EXPECT_FEEDERS_MIN)
    lo,la,hi,ha=IL_BBOX
    bad=sum(1 for s in segments if not (lo<=s[1]<=hi and la<=s[2]<=ha))
    if bad: return "%d rows outside IL bbox"%bad
    if not any(len(s)>5 for s in segments): return "no subtransmission (ST) rows present"
    return None

def main():
    t=time.time()
    print("Ameren feeder_segments refresh (Option B: returnCentroid)")
    print("Output:", OUT)
    dist=build_distribution()
    st=build_st()
    segments=dist+st
    err=validate(segments)
    if err:
        print("VALIDATION FAILED:", err); print("Leaving existing feeder_segments.json untouched."); sys.exit(1)
    doc={"keep":FLOOR,"cols":["feeder","lon","lat","gen","load"],"segments":segments}
    with open(OUT,"w",encoding="utf-8") as fh: json.dump(doc, fh, separators=(",",":"))
    mb=os.path.getsize(OUT)/1e6
    print("OK: %s rows (%s distribution + %s ST) -> %.1f MB in %ds"
          % ("{:,}".format(len(segments)),"{:,}".format(len(dist)),"{:,}".format(len(st)),mb,round(time.time()-t)))

if __name__=="__main__":
    main()
