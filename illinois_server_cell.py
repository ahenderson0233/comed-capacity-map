# ILLINOIS COMBINED DASHBOARD - LOCAL SERVER (parcel proxy + ReportAllUSA)
# Run the dashboard builder cell first (it writes illinois_dashboard.html into the folder),
# then run THIS cell. It serves the dashboard over http://127.0.0.1 and adds two endpoints
# the page uses when served:  /gis  (proxies county / USA Structures / FCC REST, bypassing
# browser CORS)  and  /reportall  (nationwide parcels for vendor-locked counties).
import http.server, socketserver, threading, webbrowser, urllib.request, urllib.parse, json
from pathlib import Path

FOLDER = Path(r"C:\Users\ahend\Downloads\Decennial Summer Work\Project Reverse Uno\Illinois (combined)")
REPORTALL_KEY = ""   # set locally; never commit
PORT = 8767

# Only forward to GIS-like hosts (prevents this from being an open proxy).
ALLOW = (".arcgis.com", "geo.fcc.gov", "reportallusa.com", ".il.us", ".gov", ".org",
         "kcsgis.com", "mijackson.org", "mcgisweb.org", "greenecountyassessor.org",
         "gscplanning.com", "wiu.edu", "k3gis.net")
UA = {"User-Agent": "Mozilla/5.0 ameren-dashboard-proxy"}

def _fetch(url, data=None, ctype=None):
    req = urllib.request.Request(url, data=data, headers=dict(UA))
    if ctype:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def _allowed(url):
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return bool(host) and any(host.endswith(s) or s in host for s in ALLOW)

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(FOLDER), **k)
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path == "/gis":
            u = urllib.parse.parse_qs(p.query).get("u", [""])[0]
            if not _allowed(u):
                return self._send(400, '{"error":"host not allowed"}')
            try:
                return self._send(200, _fetch(u))
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)}))
        if p.path == "/reportall":
            q = urllib.parse.parse_qs(p.query)
            try:
                lat = float(q.get("lat", [""])[0]); lon = float(q.get("lon", [""])[0])
            except Exception:
                return self._send(400, '{"error":"lat/lon required"}')
            if not REPORTALL_KEY:
                return self._send(200, '{"error":"no ReportAll key set"}')
            try:
                u = ("https://reportallusa.com/api/parcels?client=" + urllib.parse.quote(REPORTALL_KEY)
                     + "&v=9&rpp=1&si_srid=4326&spatial_intersect=" + urllib.parse.quote("POINT(%f %f)" % (lon, lat)))
                d = json.loads(_fetch(u).decode("utf-8", "replace"))
                out = []
                for r in (d.get("results") or []):
                    out.append({
                        "wkt": r.get("geom_as_wkt"),
                        "acreage": r.get("acreage"),
                        "calc_acreage": r.get("acreage_calc") or r.get("calc_acreage"),
                        "owner": r.get("owner"),
                        "pin": r.get("parcel_id") or r.get("parcelnumb") or r.get("robust_id"),
                        "county": r.get("county_name") or r.get("county"),
                        "address": r.get("address") or r.get("addr_full") or r.get("saddno_saddstr"),
                    })
                return self._send(200, json.dumps({"parcels": out, "count": d.get("count")}))
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)}))
        return super().do_GET()
    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        if p.path == "/gis":
            u = urllib.parse.parse_qs(p.query).get("u", [""])[0]
            if not _allowed(u):
                return self._send(400, '{"error":"host not allowed"}')
            ln = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(ln) if ln else None
            try:
                return self._send(200, _fetch(u, data=body, ctype="application/x-www-form-urlencoded"))
            except Exception as e:
                return self._send(200, json.dumps({"error": str(e)}))
        return self._send(404, '{"error":"not found"}')
    def log_message(self, *a):
        pass

class S(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

def _in_notebook():
    try:
        from IPython import get_ipython
        ip = get_ipython()
        return ip is not None and ip.__class__.__name__ == "ZMQInteractiveShell"
    except Exception:
        return False

if not (FOLDER / "illinois_dashboard.html").exists():
    print("illinois_dashboard.html not found in", FOLDER, "- run the dashboard builder cell first.")
else:
    try:
        _HTTPD.shutdown(); _HTTPD.server_close()
    except Exception:
        pass
    try:
        httpd = S(("127.0.0.1", PORT), H)
    except OSError:
        httpd = S(("127.0.0.1", 0), H)
    _HTTPD = httpd
    port = httpd.server_address[1]
    url = "http://127.0.0.1:%d/illinois_dashboard.html" % port
    print("Serving:", url)
    print("ReportAllUSA:", ("key set - vendor-locked counties enabled" if REPORTALL_KEY else "NO KEY (free county data only; paste REPORTALL_KEY to enable)"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        webbrowser.open(url)
    except Exception:
        pass
    if not _in_notebook():
        print("Press Ctrl+C to stop.")
        try:
            while True:
                threading.Event().wait(1)
        except KeyboardInterrupt:
            httpd.shutdown()
