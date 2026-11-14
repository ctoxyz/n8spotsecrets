"""Address -> point, census tract, incorporated place. No keys.

Returns a dict:
  lat, lon, matched, house_no, street, zip, tract (6 digits), tract_geoid (11), county (3),
  place, la_city (bool), la_county (bool), ms, geocoder
Raises SourceError("address not found") when no service matches the address, and
SourceError(<reason>) when no service answered in time.

0.2.2: the US Census one-line geocoder was a single call with no retry, so one slow
answer killed the whole report. Now:
  - Census is called, and called again if the first call has not answered in 1.2 s.
  - The ArcGIS World geocoder runs at the same time.
  - Census wins when it answers within 2.5 s, or later if ArcGIS has no point yet.
  - Otherwise the ArcGIS point is used, and tract / place / county come from the
    Census coordinates service or TIGERweb, whichever answers first. If neither
    answers, city and county come from the ArcGIS record.
"""
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from . import http as _http
from .http import SourceError

CENSUS_ONELINE = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
CENSUS_COORDS = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
ARCGIS_WORLD = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
TIGERWEB_IDENTIFY = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/identify"

HEDGE_AFTER_S = 1.2
ARCGIS_AFTER_S = 2.5   # past this, a ready ArcGIS point beats waiting on Census
ARCGIS_MIN_SCORE = 90
ARCGIS_ADDRESS_TYPES = ("PointAddress", "Subaddress", "StreetAddress", "StreetAddressExt", "StreetInt")
ARCGIS_ROOFTOP_TYPES = ("PointAddress", "Subaddress")

# One shared pool; calls that lose the race finish in the background and are ignored.
_POOL = ThreadPoolExecutor(max_workers=8)


def _place_flags(geos):
    place = ((geos.get("Incorporated Places") or [{}])[0]).get("NAME") or ""
    tract = (geos.get("Census Tracts") or [{}])[0]
    county = (geos.get("Counties") or [{}])[0]
    return {
        "place": place,
        "la_city": place.lower().startswith("los angeles"),
        "la_county": (county.get("NAME") or "").lower().startswith("los angeles") or tract.get("COUNTY") == "037",
        "tract": tract.get("TRACT"),
        "tract_geoid": tract.get("GEOID"),
        "county": tract.get("COUNTY") or county.get("COUNTY"),
    }


# ---------------------------------------------------------------- single services
def _census_oneline(address, timeout):
    j, ms = _http.get_json(CENSUS_ONELINE, {
        "address": address, "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json",
    }, timeout=timeout)
    matches = (j.get("result") or {}).get("addressMatches") or []
    if not matches:
        return None
    m = matches[0]
    c = m["coordinates"]
    out = {"lat": float(c["y"]), "lon": float(c["x"]), "matched": m.get("matchedAddress"), "ms": ms, "geocoder": "census"}
    ma = m.get("matchedAddress") or address
    hn = re.match(r"\s*(\d+)\s+(.*?),", ma)
    out["house_no"] = hn.group(1) if hn else None
    out["street"] = hn.group(2).strip() if hn else None
    zp = re.search(r"\b(\d{5})\s*$", ma)
    out["zip"] = zp.group(1) if zp else None
    out.update(_place_flags(m.get("geographies") or {}))
    return out


def _arcgis(address, timeout):
    j, ms = _http.get_json(ARCGIS_WORLD, {
        "f": "json", "singleLine": address, "maxLocations": "5", "outFields": "*", "countryCode": "USA",
    }, timeout=timeout)
    said = " " + re.sub(r"[^a-z0-9]+", " ", str(address).lower()) + " "
    for c in j.get("candidates") or []:
        a = c.get("attributes") or {}
        loc = c.get("location") or {}
        score = float(c.get("score") or a.get("Score") or 0)
        if score < ARCGIS_MIN_SCORE or a.get("Addr_type") not in ARCGIS_ADDRESS_TYPES:
            continue
        if loc.get("x") is None or loc.get("y") is None:
            continue
        match = str(a.get("Match_addr") or c.get("address") or "")
        parts = [p.strip() for p in match.split(",")]
        city = (a.get("City") or (parts[-3] if len(parts) >= 4 else "")).strip()
        postal = (a.get("Postal") or "").strip()
        # "250 Spring Street Los Angeles CA" scored 100 for Claremont. Only take a place the address names.
        city_said = bool(city) and (" " + re.sub(r"[^a-z0-9]+", " ", city.lower()) + " ") in said
        zip_said = bool(re.match(r"^\d{5}", postal)) and (" " + postal[:5] + " ") in said
        if not (city_said or zip_said):
            continue
        sub = (a.get("Subregion") or "").strip()
        st_addr = (a.get("StAddr") or "").strip()
        hn = re.match(r"\s*(\d+)\s+(.*)$", st_addr)
        return {
            "lat": float(loc["y"]), "lon": float(loc["x"]),
            "matched": match.upper() or None,
            "house_no": (a.get("AddNum") or (hn.group(1) if hn else "")) or None,
            "street": (hn.group(2).strip().upper() if hn else None),
            "zip": postal[:5] if re.match(r"^\d{5}", postal) else None,
            "place": (city + " city") if city else "",
            "la_city": city.lower() == "los angeles",
            "la_county": sub.lower().startswith("los angeles"),
            "tract": None, "tract_geoid": None, "county": "037" if sub.lower().startswith("los angeles") else None,
            "ms": ms, "geocoder": "arcgis",
        }
    return None


def rooftop(house_no, street, lat, lon, timeout=3.0, max_m=400.0):
    """Rooftop-level point for 'house_no street' near (lat, lon), or None.

    The Census point is interpolated along the street centerline and can sit 70 m from
    the building. ArcGIS PointAddress matches sit on the building or its parcel.
    Only a candidate with the same house number, within max_m of the given point, is used.
    """
    d = 0.006
    j, ms = _http.get_json(ARCGIS_WORLD, {
        "f": "json", "singleLine": "%s %s" % (house_no, street), "maxLocations": "5", "outFields": "*",
        "countryCode": "USA", "location": "%s,%s" % (lon, lat),
        "searchExtent": "%s,%s,%s,%s" % (lon - d, lat - d, lon + d, lat + d),
    }, timeout=timeout)
    best = None
    for c in j.get("candidates") or []:
        a = c.get("attributes") or {}
        loc = c.get("location") or {}
        if a.get("Addr_type") not in ARCGIS_ROOFTOP_TYPES or float(c.get("score") or 0) < ARCGIS_MIN_SCORE:
            continue
        if str(a.get("AddNum") or "").strip() != str(house_no).strip():
            continue
        try:
            y, x = float(loc["y"]), float(loc["x"])
        except (KeyError, TypeError, ValueError):
            continue
        m = 6371000.0 * math.hypot(math.radians(x - lon) * math.cos(math.radians(lat)), math.radians(y - lat))
        if m <= max_m and (best is None or m < best["dist_m"]):
            best = {"lat": y, "lon": x, "dist_m": int(m), "matched": a.get("Match_addr") or c.get("address"),
                    "addr_type": a.get("Addr_type"), "ms": ms}
    return best


def _census_coords(lat, lon, timeout, require_tract=True):
    j, ms = _http.get_json(CENSUS_COORDS, {
        "x": lon, "y": lat, "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json",
    }, timeout=timeout)
    geos = (j.get("result") or {}).get("geographies") or {}
    if require_tract and not geos.get("Census Tracts"):
        raise SourceError("no tract at point")
    return _place_flags(geos)


def _tigerweb(lat, lon, timeout):
    d = 0.001
    j, ms = _http.get_json(TIGERWEB_IDENTIFY, {
        "f": "json", "geometry": "%s,%s" % (lon, lat), "geometryType": "esriGeometryPoint", "sr": "4326",
        "layers": "all", "tolerance": "0", "returnGeometry": "false",
        "mapExtent": "%s,%s,%s,%s" % (lon - d, lat - d, lon + d, lat + d), "imageDisplay": "100,100,96",
    }, timeout=timeout)
    geos = {}
    for r in j.get("results") or []:
        name = r.get("layerName") or ""
        if name in ("Census Tracts", "Incorporated Places", "Counties") and name not in geos:
            geos[name] = [r.get("attributes") or {}]
    if not geos.get("Census Tracts"):
        raise SourceError("no tract at point")
    return _place_flags(geos)


# ---------------------------------------------------------------- race helpers
def _first_ok(futs, deadline):
    """First future that returns a non-error value before the deadline. (value, errors)."""
    pending = set(futs)
    errors = []
    while pending:
        left = deadline - time.time()
        if left <= 0:
            break
        done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
        for f in done:
            try:
                return f.result(), errors
            except Exception as e:
                errors.append(str(e)[:120])
    return None, errors


def _enrich(g, deadline):
    """Fill tract / place / county for an ArcGIS point. Keeps the ArcGIS flags if nothing answers."""
    left = max(0.5, deadline - time.time())
    futs = [_POOL.submit(_census_coords, g["lat"], g["lon"], left),
            _POOL.submit(_tigerweb, g["lat"], g["lon"], left)]
    flags, _ = _first_ok(futs, deadline)
    if flags:
        if not flags.get("place") and g.get("place"):
            flags["place"] = g["place"]
            flags["la_city"] = g["la_city"]
        g.update(flags)
    return g


# ---------------------------------------------------------------- public
def geocode(address, timeout=4.0):
    t0 = time.time()
    deadline = t0 + max(1.0, timeout)
    census = [_POOL.submit(_census_oneline, address, max(0.5, timeout))]
    arc = _POOL.submit(_arcgis, address, max(0.5, timeout))

    errors = []
    census_said_no = False
    hedged = False
    seen = set()
    while not census_said_no:
        for f in census:
            if f in seen or not f.done():
                continue
            seen.add(f)
            try:
                r = f.result()
            except Exception as e:
                errors.append("census: " + str(e)[:120])
                continue
            if r:
                r["ms"] = int((time.time() - t0) * 1000)
                return r
            census_said_no = True
        if census_said_no or time.time() >= deadline:
            break
        if time.time() >= t0 + ARCGIS_AFTER_S and arc.done() and not arc.exception() and arc.result():
            break
        pending = [f for f in census if not f.done()]
        if not hedged and (not pending or time.time() >= t0 + HEDGE_AFTER_S):
            # first call failed fast, or is slow: fire the second one
            census.append(_POOL.submit(_census_oneline, address, max(0.5, deadline - time.time())))
            hedged = True
            continue
        if not pending:
            break
        now = time.time()
        if not hedged:
            wait_until = min(deadline, t0 + HEDGE_AFTER_S)
        elif now < t0 + ARCGIS_AFTER_S:
            wait_until = min(deadline, t0 + ARCGIS_AFTER_S)
        else:
            wait_until = deadline
            if not arc.done():
                pending = pending + [arc]
        wait(pending, timeout=max(0.0, wait_until - now), return_when=FIRST_COMPLETED)

    # Census gave no point in time. Use ArcGIS if it has a good match.
    try:
        g = arc.result(timeout=max(0.0, deadline - time.time()))
    except Exception as e:
        g = None
        errors.append("arcgis: " + str(e)[:120])
    if g:
        _enrich(g, max(deadline, time.time() + 1.5))
        g["ms"] = int((time.time() - t0) * 1000)
        return g
    if census_said_no and arc.done():
        raise SourceError("address not found")
    raise SourceError("geocoders did not answer: " + ("; ".join(errors)[:200] or "timeout %.1fs" % timeout))


def reverse(lat, lon, timeout=4.0):
    """Point -> tract / place flags (for callers that already have coordinates)."""
    t0 = time.time()
    deadline = t0 + max(1.0, timeout)
    futs = [_POOL.submit(_census_coords, lat, lon, max(0.5, timeout), False),
            _POOL.submit(_tigerweb, lat, lon, max(0.5, timeout))]
    flags, errors = _first_ok(futs, deadline)
    if flags is None:
        raise SourceError("point lookup did not answer: " + "; ".join(errors)[:200])
    out = {"lat": float(lat), "lon": float(lon), "matched": None, "house_no": None, "street": None, "zip": None,
           "ms": int((time.time() - t0) * 1000)}
    out.update(flags)
    return out
