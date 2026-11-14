"""Veterans: the nearest VA sites to the address, and how many veterans live in its census tract. Nationwide.

VA sites come from the Veterans Health Administration's public facility layer (medical centers, clinics and
Vet Centers). The layer also carries the name and email of a staff contact for each site; those are never asked for.
The tract count is ACS 5-year table B21001 (civilians 18 and over, veterans among them) and needs the same free
Census key as the price check."""
from .. import http as _http
from ..http import SourceError

VHA = "https://services2.arcgis.com/VFLAJVozK0rtzQmT/arcgis/rest/services/Veterans_Health_Administration_Medical_Facilities/FeatureServer/0"
ACS = "https://api.census.gov/data/%s/acs/acs5"
WITHIN_KM = 40
_FIELDS = "STA_NAME,S_ADD1,S_CITY,S_STATE,S_ZIP"


def _s(v):
    return str(v).strip() if v not in (None, "") else None


def facilities(lat, lon, timeout=4.0):
    """The four nearest VA sites within 40 km, nearest first."""
    j, ms = _http.get_json(VHA + "/query", {
        "geometry": "%s,%s" % (lon, lat), "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "distance": str(WITHIN_KM * 1000), "units": "esriSRUnit_Meter",
        "where": "1=1", "outFields": _FIELDS, "returnGeometry": "true", "outSR": "4326", "resultRecordCount": "200", "f": "json",
    }, timeout=timeout)
    rows, seen = [], set()
    for f in j.get("features") or []:
        a, g = f.get("attributes") or {}, f.get("geometry") or {}
        name = _s(a.get("STA_NAME"))
        if not name or g.get("x") is None or g.get("y") is None:
            continue
        d = int(_http.meters(lat, lon, float(g["y"]), float(g["x"])))
        key = (name.lower(), d // 50)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"name": name, "address": _s(a.get("S_ADD1")), "city": _s(a.get("S_CITY")), "state": _s(a.get("S_STATE")),
                     "zip": _s(a.get("S_ZIP")), "distance_m": d})
    rows.sort(key=lambda r: r["distance_m"])
    return {"facilities": rows[:4], "count": len(rows), "within_km": WITHIN_KM, "ms": ms}


def tract(tract_geoid, key, timeout=4.0):
    """Veterans among the adults of one census tract."""
    geoid = str(tract_geoid or "")
    if not key:
        raise SourceError("CENSUS_API_KEY not set")
    if len(geoid) != 11 or not geoid.isdigit():
        raise SourceError("no census tract number for this address")
    last = None
    for year in ("2023", "2022"):
        try:
            j, ms = _http.get_json(ACS % year, {"get": "B21001_001E,B21001_002E", "for": "tract:%s" % geoid[5:],
                                                "in": "state:%s county:%s" % (geoid[:2], geoid[2:5]), "key": key}, timeout=timeout)
            if not isinstance(j, list) or len(j) < 2:
                raise SourceError("no ACS row")
            d = dict(zip(j[0], j[1]))
            adults, vets = int(d["B21001_001E"]), int(d["B21001_002E"])
            if adults < 0 or vets < 0:
                raise SourceError("the Census does not publish this count for the tract")
            return {"veterans": vets, "adults": adults, "pct": round(100.0 * vets / adults, 1) if adults else None, "acs_year": year, "ms": ms}
        except (KeyError, TypeError, ValueError):
            last = SourceError("unexpected ACS answer")
        except SourceError as e:
            last = e
    raise last or SourceError("ACS unavailable")
