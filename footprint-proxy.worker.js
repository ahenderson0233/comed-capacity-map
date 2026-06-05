/* Illinois Capacity & Siting Dashboard - footprint proxy (Cloudflare Worker)
 * Replicates the local server's /gis and /reportall endpoints so the Site Footprint
 * Analysis works from a static host (GitHub Pages). CORS is locked to the allowed origins.
 *
 * SETUP (free):
 *  1. https://workers.cloudflare.com  ->  Create Worker  ->  paste this file  ->  Deploy.
 *  2. Worker Settings -> Variables -> add a SECRET named  REPORTALL_KEY  = your ReportAllUSA client key.
 *  3. Copy the Worker URL (https://xxxx.workers.dev).
 *  4. In index.html set:  var PROXY_BASE="https://xxxx.workers.dev";
 *  If you add a custom domain or a second site, add it to ALLOWED_ORIGINS below.
 */
const ALLOW = [".arcgis.com","geo.fcc.gov","reportallusa.com",".il.us",".gov",".org",
  "kcsgis.com","mijackson.org","mcgisweb.org","greenecountyassessor.org","gscplanning.com","wiu.edu","k3gis.net"];
const ALLOWED_ORIGINS = [
  "https://ahenderson0233.github.io",   // GitHub Pages (origin = host only, not the /comed-capacity-map/ path)
  "http://localhost:8767",
  "http://127.0.0.1:8767"
];
const UA = "Mozilla/5.0 ameren-dashboard-proxy";

function cors(req){
  const o = req.headers.get("Origin") || "";
  const allow = ALLOWED_ORIGINS.includes(o) ? o : ALLOWED_ORIGINS[0];
  return {"Access-Control-Allow-Origin":allow,"Access-Control-Allow-Methods":"GET,POST,OPTIONS","Access-Control-Allow-Headers":"Content-Type","Vary":"Origin"};
}
function allowed(u){ try{ const h=new URL(u).hostname.toLowerCase(); return ALLOW.some(s=> h.endsWith(s) || h.includes(s)); }catch(e){ return false; } }

export default {
  async fetch(req, env){
    const C = cors(req);
    const J = (o, code=200) => new Response(JSON.stringify(o), {status:code, headers:{...C,"Content-Type":"application/json"}});
    const url = new URL(req.url);
    if(req.method === "OPTIONS") return new Response(null, {headers:C});

    if(url.pathname === "/gis"){
      const u = url.searchParams.get("u") || "";
      if(!allowed(u)) return J({error:"host not allowed"}, 400);
      const init = {method:req.method, headers:{"User-Agent":UA}};
      if(req.method === "POST"){ init.body = await req.text(); init.headers["Content-Type"]="application/x-www-form-urlencoded"; }
      try{
        const r = await fetch(u, init); const body = await r.text();
        return new Response(body, {headers:{...C, "Content-Type": r.headers.get("content-type") || "application/json"}});
      }catch(e){ return J({error:String(e)}); }
    }

    if(url.pathname === "/reportall"){
      const lat = url.searchParams.get("lat"), lon = url.searchParams.get("lon");
      if(!lat || !lon) return J({error:"lat/lon required"}, 400);
      const key = env.REPORTALL_KEY;
      if(!key) return J({error:"no ReportAll key set"});
      const u = "https://reportallusa.com/api/parcels?client=" + encodeURIComponent(key)
              + "&v=9&rpp=1&si_srid=4326&spatial_intersect=" + encodeURIComponent(`POINT(${lon} ${lat})`);
      try{
        const d = await (await fetch(u, {headers:{"User-Agent":UA}})).json();
        const out = (d.results || []).map(r => ({
          wkt: r.geom_as_wkt, acreage: r.acreage, calc_acreage: r.acreage_calc || r.calc_acreage,
          owner: r.owner, pin: r.parcel_id || r.parcelnumb || r.robust_id,
          county: r.county_name || r.county, address: r.address || r.addr_full || r.saddno_saddstr
        }));
        return J({parcels: out, count: d.count});
      }catch(e){ return J({error:String(e)}); }
    }
    return J({error:"not found"}, 404);
  }
};
