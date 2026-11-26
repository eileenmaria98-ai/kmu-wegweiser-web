#!/usr/bin/env python3
"""Holt Basel-Stadt Open Data (data.bs.ch) und schreibt data.json fuer die Baustellenkarte.
Aufruf: python3 -u -I basel_karte.py <ausgabeordner>
"""
import json, math, re, sys, time, os, urllib.parse, urllib.request
from datetime import date, timedelta, datetime, timezone
from collections import Counter

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
BASE = "https://data.bs.ch/api/explore/v2.1/catalog/datasets"
TODAY = datetime.now(timezone.utc).astimezone().date()
try:
    from zoneinfo import ZoneInfo
    TODAY = datetime.now(ZoneInfo("Europe/Zurich")).date()
except Exception:
    pass
T = TODAY.isoformat()
SOON = (TODAY + timedelta(days=14)).isoformat()
WEEK = (TODAY + timedelta(days=7)).isoformat()


def get(url, params=None, tries=6):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "basel-baustellenkarte"}), timeout=90) as r:
                return json.load(r)
        except Exception as e:
            last = e
            time.sleep(2 + i)
    raise RuntimeError(f"Abruf fehlgeschlagen: {url} ({last})")


def records(ds, where, select, order=None):
    p = {"where": where, "select": select, "limit": -1, "timezone": "Europe/Zurich"}
    print("  GET", ds, where[:80], flush=True)
    return get(f"{BASE}/{ds}/exports/json", p)


# ---------- Projektion (Meter, lokal) ----------
LAT0, LON0 = 47.56, 7.60
KX = 111320 * math.cos(math.radians(LAT0))
KY = 110574


OFF = [0.0, 0.0]


def prj(lon, lat):
    return ((lon - LON0) * KX - OFF[0], -(lat - LAT0) * KY - OFF[1])


def rings(geom):
    """Liste von (typ, [punkte]) aus GeoJSON-Geometrie. typ 'line' oder 'poly'."""
    if not geom:
        return []
    if geom.get("type") == "Feature":
        geom = geom["geometry"]
    t, c = geom["type"], geom["coordinates"]
    if t == "Point":
        return [("pt", [c])]
    if t == "MultiPoint":
        return [("pt", c)]
    if t == "LineString":
        return [("line", c)]
    if t == "MultiLineString":
        return [("line", l) for l in c]
    if t == "Polygon":
        return [("poly", r) for r in c]
    if t == "MultiPolygon":
        return [("poly", r) for p in c for r in p]
    if t == "GeometryCollection":
        return [x for g in geom["geometries"] for x in rings(g)]
    return []


def simplify(pts, tol):
    if len(pts) < 3:
        return pts
    keep = [pts[0]]
    for p in pts[1:-1]:
        if abs(p[0] - keep[-1][0]) + abs(p[1] - keep[-1][1]) >= tol:
            keep.append(p)
    keep.append(pts[-1])
    return keep


def path_d(shapes, tol=2.0, close_poly=True):
    parts = []
    for typ, pts in shapes:
        xy = [prj(*p[:2]) for p in pts]
        if typ == "pt":
            for x, y in xy:
                parts.append(f"M{x:.0f} {y:.0f}h0")
            continue
        xy = simplify(xy, tol)
        if len(xy) < 2:
            continue
        s = "M" + " ".join(f"{x:.0f} {y:.0f}" for x, y in xy)
        if typ == "poly" and close_poly:
            s += "Z"
        parts.append(s)
    return "".join(parts)


def centroid(shapes):
    xs, ys, n = 0, 0, 0
    for _, pts in shapes:
        for p in pts:
            x, y = prj(*p[:2])
            xs += x; ys += y; n += 1
    return (round(xs / n), round(ys / n)) if n else None


CELL = 40


class Idx:
    def __init__(self):
        self.g = {}

    def add(self, x, y, tag):
        self.g.setdefault((int(x // CELL), int(y // CELL)), []).append((x, y, tag))

    def near(self, x, y, r):
        out = {}
        cx, cy, k, r2 = int(x // CELL), int(y // CELL), int(r // CELL) + 1, r * r
        for i in range(cx - k, cx + k + 1):
            for j in range(cy - k, cy + k + 1):
                for px, py, t in self.g.get((i, j), ()):
                    d = (px - x) ** 2 + (py - y) ** 2
                    if d <= r2 and (t not in out or d < out[t]):
                        out[t] = d
        return out


def dense(xy, step):
    out = [xy[0]]
    for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) // step))
        for i in range(1, n + 1):
            out.append((x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n))
    return out


def index_lines(idx, shapes, tag, step):
    for typ, pts in shapes:
        xy = [prj(*p[:2]) for p in pts]
        if not xy:
            continue
        for x, y in (dense(xy, step) if len(xy) > 1 else xy):
            idx.add(x, y, tag)


def samples(shapes, step=30, maxn=60):
    pts = []
    for typ, ring in shapes:
        xy = [prj(*p[:2]) for p in ring]
        if xy:
            pts += dense(xy, step) if len(xy) > 1 else xy
    if len(pts) > maxn:
        pts = [pts[int(i * len(pts) / maxn)] for i in range(maxn)]
    return pts


def sort_lines(names):
    return sorted(names, key=lambda s: (int(s) if s.isdigit() else 999, s))


def nearby(shapes):
    """Strassen, Tramlinien und Haltestellen in der Naehe einer Geometrie."""
    pts = samples(shapes)
    if not pts:
        return {"st": [], "tr": [], "hs": []}
    cnt, trs, hd = Counter(), set(), {}
    for x, y in pts:
        for n in IDX_STREET.near(x, y, 22):
            cnt[n] += 1
        trs.update(IDX_TRAM.near(x, y, 30))
        for n, d in IDX_STOP.near(x, y, 120).items():
            if n not in hd or d < hd[n]:
                hd[n] = d
    top = cnt.most_common(1)[0][1] if cnt else 0
    st = [n for n, c in cnt.most_common(4) if c >= max(1, 0.25 * top)][:3]
    hs = [n for n, _ in sorted(hd.items(), key=lambda kv: kv[1])[:3]]
    return {"st": st, "tr": sort_lines(trs), "hs": hs}


RANK = {"bewilligt": 0, "teilweise bewilligt": 1, "reserviert": 2, "gemeldet": 3}


def status_of(r):
    """Status laut Kanton. None = stornierte oder abgelehnte Eintraege, die nicht gezeigt werden."""
    b, e = (r.get("belestatbe") or "").strip(), (r.get("entsch_bez") or "").strip()
    if b in ("storniert", "nicht bewilligt"):
        return None
    if e == "abgelehnt" and b != "bewilligt":
        return None
    if b == "bewilligt" or (e == "bewilligt" and b not in ("teilweise bewilligt",)):
        return "bewilligt"
    if b == "teilweise bewilligt":
        return "teilweise bewilligt"
    if b in ("reserviert", "gemeldet"):
        return b
    return b or "gemeldet"


# ---------- Hintergrund ----------
print("Wohnviertel ...")
viertel = []
for r in records("100042", "1=1", "*"):
    name = r.get("wov_name") or r.get("wov_label") or r.get("name") or ""
    viertel.append({"name": name, "shapes": rings(r.get("geo_shape")), "c": r.get("geo_point_2d")})
allx, ally = [], []
for v in viertel:
    for _, pts in v["shapes"]:
        for p in pts:
            x, y = prj(*p[:2]); allx.append(x); ally.append(y)
X0, Y0, X1, Y1 = min(allx), min(ally), max(allx), max(ally)
PAD = 300
X0 -= PAD; Y0 -= PAD; X1 += PAD; Y1 += PAD
W, H = round(X1 - X0), round(Y1 - Y0)
print("Ausdehnung m:", W, H)


OFF[0], OFF[1] = X0, Y0


bg_viertel = [{"name": v["name"], "d": path_d(v["shapes"], 4)} for v in viertel]
labels = []
for v in viertel:
    c = v["c"]
    if v["name"] and c:
        x, y = prj(c["lon"], c["lat"])
        labels.append({"k": "v", "n": v["name"], "x": round(x), "y": round(y), "a": 0, "l": 0})


def seg_label(pts):
    """Mittelpunkt und Winkel (Grad, lesbar) einer Linie."""
    xy = [prj(*p[:2]) for p in pts]
    if len(xy) < 2:
        return None
    m = len(xy) // 2
    if len(xy) == 2:
        (x0, y0), (x1, y1) = xy; x, y = (x0 + x1) / 2, (y0 + y1) / 2
    else:
        x, y = xy[m]; (x0, y0), (x1, y1) = xy[m - 1], xy[min(m + 1, len(xy) - 1)]
    a = math.degrees(math.atan2(y1 - y0, x1 - x0))
    if a > 90: a -= 180
    if a <= -90: a += 180
    return round(x), round(y), round(a), math.hypot(xy[-1][0] - xy[0][0], xy[-1][1] - xy[0][1])


print("Gewaesser ...")
water = []
for r in records("100261", "1=1", "gz_gewaessername,geo_shape"):
    sh = rings(r.get("geo_shape"))
    water.append({"name": r.get("gz_gewaessername"), "d": path_d(sh, 3)})
    if r.get("gz_gewaessername") in ("Rhein", "Wiese", "Birs", "Birsig") and sh:
        longest = max((p for t, p in sh), key=len)
        lab = seg_label(longest)
        if lab:
            labels.append({"k": "w", "n": r["gz_gewaessername"], "x": lab[0], "y": lab[1], "a": lab[2], "l": 0})

print("Strassen ...")
roads_raw = records("100250", "1=1", "strassenname,strassennetzhierarchie_code,geo_shape")
MAIN = {"HLS": 0, "HLS-A": 0, "HVS": 1, "HSS": 2, "QSS": 3, "ES": 4}
LVL_LABEL = {"HVS": 1, "HSS": 2, "QSS": 3, "ES": 4}
bycls, IDX_STREET, best = {}, Idx(), {}
for r in roads_raw:
    c = r.get("strassennetzhierarchie_code") or "X"
    sh = rings(r.get("geo_shape"))
    name = r.get("strassenname")
    if c in MAIN:
        bycls.setdefault(MAIN[c], []).append(sh)
    if name:
        index_lines(IDX_STREET, sh, name, 20)
    if name and c in LVL_LABEL and sh:
        lab = seg_label(sh[0][1])
        if lab:
            cur = best.get(name)
            key = (LVL_LABEL[c], -lab[3])
            if cur is None or key < cur[0]:
                best[name] = (key, lab)
roads = [{"lvl": lvl, "d": "".join(path_d(s_, 6) for s_ in lst)} for lvl, lst in sorted(bycls.items())]
for name, (key, lab) in best.items():
    labels.append({"k": "s", "n": name, "x": lab[0], "y": lab[1], "a": lab[2], "l": key[0]})

print("Strassennetz fuer Routen ...")
nodes, ngrid, nnames, nidx, edges = [], {}, [], {}, []


def node_id(x, y):
    cx, cy = int(x // 3), int(y // 3)
    for i in (cx - 1, cx, cx + 1):
        for j in (cy - 1, cy, cy + 1):
            for nid in ngrid.get((i, j), ()):
                nx, ny = nodes[nid]
                if (nx - x) ** 2 + (ny - y) ** 2 <= 4.0:
                    return nid
    nodes.append((x, y))
    ngrid.setdefault((cx, cy), []).append(len(nodes) - 1)
    return len(nodes) - 1


for r in roads_raw:
    c = r.get("strassennetzhierarchie_code")
    if c not in MAIN:
        continue
    nm = r.get("strassenname") or ""
    if nm not in nidx:
        nidx[nm] = len(nnames); nnames.append(nm)
    for typ, pts in rings(r.get("geo_shape")):
        if typ != "line":
            continue
        xy = [prj(*p[:2]) for p in pts]
        if len(xy) < 2:
            continue
        L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(xy, xy[1:]))
        a, b = node_id(*xy[0]), node_id(*xy[-1])
        if a == b and L < 5:
            continue
        edges.append([a, b, round(L), MAIN[c], nidx[nm], [round(v) for p in simplify(xy, 4) for v in p]])
net = {"nodes": [[round(x), round(y)] for x, y in nodes], "edges": edges, "names": nnames}
# Zusammenhang pruefen
parent = list(range(len(nodes)))


def find(i):
    while parent[i] != i:
        parent[i] = parent[parent[i]]; i = parent[i]
    return i


for a, b, *_ in edges:
    parent[find(a)] = find(b)
comp = Counter(find(i) for i in range(len(nodes)))
print(" Netz: Knoten", len(nodes), "Kanten", len(edges), "groesste Komponente", round(100 * comp.most_common(1)[0][1] / len(nodes)), "%")

print("Tram ...")
tram_raw = records("100064", "fahrzeug='Tram'", "liniennmr,strecke,geo_shape")
IDX_TRAM, by_line = Idx(), {}
for r in tram_raw:
    n = str(r.get("liniennmr"))
    sh = rings(r.get("geo_shape"))
    by_line.setdefault(n, []).extend(sh)
    index_lines(IDX_TRAM, sh, n, 15)
tram = [{"n": n, "d": path_d(by_line[n], 3, False)} for n in sort_lines(by_line)]
print(" Tramlinien:", [t["n"] for t in tram])

IDX_STOP, stops = Idx(), []
for r in records("100063", "hstart='Tramhaltestelle' OR hstart='Tram- und Bushaltestelle' OR hstart='Bahnhof'", "name,angezname,hstart,geo_shape"):
    sh = rings(r.get("geo_shape"))
    if not sh:
        continue
    x, y = prj(*sh[0][1][0][:2])
    if not (-800 <= x <= W + 800 and -800 <= y <= H + 800):
        continue
    nm = (r.get("angezname") or r.get("name") or "").replace("Basel, ", "")
    lines = sort_lines(IDX_TRAM.near(x, y, 40))
    if not lines:
        continue
    stops.append({"n": nm, "x": round(x), "y": round(y), "l": lines, "b": 1 if r.get("hstart") == "Bahnhof" else 0})
    IDX_STOP.add(x, y, nm)
print(" Haltestellen:", len(stops))

# ---------- Baustellen ----------
print("Baustellen ...")
bs = records("100335", "1=1", "*")
ids = [b["id"] for b in bs if b.get("id")]
print(" Projekte:", len(bs))

geom_by_id = {}
for i in range(0, len(ids), 20):
    chunk = ids[i:i + 20]
    w = "begehrenid IN (" + ",".join(str(x) for x in chunk) + ") AND belgartbez IN ('Baustelle','Bauinstallation','Umleitung','Aktivität')"
    for r in records("100018", w, "begehrenid,belgartbez,geo_shape"):
        geom_by_id.setdefault(r["begehrenid"], []).extend(rings(r.get("geo_shape")))

baustellen = []
for b in bs:
    von, bis = b.get("datum_von"), b.get("datum_bis")
    if not von or not bis:
        continue
    if von <= T <= bis:
        status = "aktiv"
    elif T < von <= SOON:
        status = "bald"
    else:
        continue
    sh = geom_by_id.get(b["id"], [])
    baustellen.append({
        "id": b["id"], "name": b.get("projekt_name"), "text": b.get("projekt_beschrieb"),
        "info": b.get("projekt_info"), "link": b.get("projekt_link"),
        "von": von, "bis": bis, "status": status,
        "docs": [x for x in (b.get("dokument1"), b.get("dokument2"), b.get("dokument3")) if x],
        "d": path_d(sh, 2) if sh else "", "c": centroid(sh) if sh else None, **nearby(sh),
    })
print(" aktiv/bald:", Counter(x["status"] for x in baustellen), "ohne Geometrie:", sum(1 for x in baustellen if not x["d"]))

# ---------- Verkehrsfuehrung / Umleitung ----------
print("Verkehrsfuehrung ...")
verk, seen = [], set()
w = (f"datum_von<=date'{T}' AND datum_bis>=date'{T}' AND (belgartbez='Umleitung' OR "
     "((belgartbez='Baustelle' OR belgartbez='Bauinstallation') AND (search(bezeichng,'Umleitung') OR search(bezeichng,'Verkehrsführung') OR search(bezeichng,'Sperrung') OR search(bezeichng,'gesperrt'))))")
for r in records("100018", w, "begehrenid,bezeichng,belgartbez,datum_von,datum_bis,belestatbe,entsch_bez,geo_shape"):
    st = status_of(r)
    if st is None:
        continue
    key = (r["begehrenid"], r.get("bezeichng"))
    sh = rings(r.get("geo_shape"))
    if key in seen:
        for v in verk:
            if v["key"] == key:
                v["_sh"].extend(sh)
                if RANK.get(st, 9) < RANK.get(v["status"], 9):
                    v["status"] = st
        continue
    seen.add(key)
    verk.append({"key": key, "id": r["begehrenid"], "text": r.get("bezeichng"), "art": r.get("belgartbez"),
                 "von": r.get("datum_von"), "bis": r.get("datum_bis"), "status": st, "_sh": sh})
for v in verk:
    sh = v.pop("_sh"); v.pop("key")
    v["d"] = path_d(sh, 2); v["c"] = centroid(sh); v.update(nearby(sh))
print(" Verkehrsfuehrungen:", len(verk))

# ---------- Demos & Veranstaltungen ----------
print("Demos & Veranstaltungen ...")
DEMO = re.compile(r"demo|kundgebung|streik|umzug|marsch|protest|mahnwache", re.I)
w = (f"datum_von<=date'{WEEK}' AND datum_bis>=date'{T}' AND datum_bis<=date'{WEEK}' AND "
     "(belgartbez='Veranstaltung' OR belgartbez='Festivität' OR search(bezeichng,'Demonstration') OR search(bezeichng,'Kundgebung') OR search(bezeichng,'Streik') OR search(bezeichng,'Demo'))")
ev, evmap = [], {}
for r in records("100018", w, "begehrenid,bezeichng,belgartbez,artbeg_bez,datum_von,datum_bis,belestatbe,entsch_bez,geo_shape"):
    st = status_of(r)
    if st is None:
        continue
    name = (r.get("bezeichng") or "").strip()
    key = (name, r.get("datum_von"), r.get("datum_bis"))
    sh = rings(r.get("geo_shape"))
    if key in evmap:
        evmap[key]["_sh"].extend(sh)
        if RANK.get(st, 9) < RANK.get(evmap[key]["status"], 9):
            evmap[key]["status"] = st
        continue
    kind = "demo" if DEMO.search(name) else "event"
    e = {"text": name, "kind": kind, "von": r.get("datum_von"), "bis": r.get("datum_bis"), "status": st, "_sh": sh}
    evmap[key] = e
    ev.append(e)
for e in ev:
    sh = e.pop("_sh"); e["d"] = path_d(sh, 2); e["c"] = centroid(sh); e.update(nearby(sh))
ev.sort(key=lambda e: (e["von"], e["kind"] != "demo", e["text"]))
print(" Eintraege:", Counter(e["kind"] for e in ev))

assert len(baustellen) > 10, "Zu wenige Baustellen, Abbruch"
assert len(tram) >= 8 and len(stops) > 50, "Tramdaten unvollstaendig, Abbruch"
assert len(edges) > 3000, "Strassennetz unvollstaendig, Abbruch"

now = datetime.now(ZoneInfo("Europe/Zurich")) if "ZoneInfo" in globals() else datetime.now()
data = {
    "generated": now.strftime("%Y-%m-%d %H:%M"), "today": T, "w": W, "h": H, 
    "viertel": bg_viertel, "water": water, "roads": roads, "labels": labels, "tram": tram, "stops": stops, "net": net,
    "baustellen": baustellen, "verkehr": verk, "events": ev,
    "src": {"baustellen_modified": get(f"{BASE}/100335")["metas"]["default"]["modified"],
            "allmend_modified": get(f"{BASE}/100018")["metas"]["default"]["modified"]},
}
os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "data.json"), "w") as f:
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
print("data.json:", os.path.getsize(os.path.join(OUT, "data.json")) // 1024, "KB;", len(baustellen), "Baustellen,", len(verk), "Verkehrsfuehrungen,", len(ev), "Demos/Veranstaltungen")
