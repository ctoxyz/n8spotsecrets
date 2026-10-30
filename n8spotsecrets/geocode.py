"""Address -> point, census tract, incorporated place. US Census geocoder, no key.

Returns a dict:
  lat, lon, matched, house_no, tract (6 digits), tract_geoid (11), county (3), place, la_city (bool)
Raises SourceError when the address does not match.
"""
import re

from . import http as _http
from .http import SourceError

CENSUS_ONELINE = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
CENSUS_COORDS = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"


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


def geocode(address, timeout=4.0):
    j, ms = _http.get_json(CENSUS_ONELINE, {
        "address": address, "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json",
    }, timeout=timeout)
    matches = (j.get("result") or {}).get("addressMatches") or []
    if not matches:
        raise SourceError("address not found")
    m = matches[0]
    c = m["coordinates"]
    out = {"lat": float(c["y"]), "lon": float(c["x"]), "matched": m.get("matchedAddress"), "ms": ms}
    ma = m.get("matchedAddress") or address
    hn = re.match(r"\s*(\d+)\s+(.*?),", ma)
    out["house_no"] = hn.group(1) if hn else None
    out["street"] = hn.group(2).strip() if hn else None
    zp = re.search(r"\b(\d{5})\s*$", ma)
    out["zip"] = zp.group(1) if zp else None
    out.update(_place_flags(m.get("geographies") or {}))
    return out


def reverse(lat, lon, timeout=4.0):
    """Point -> tract / place flags (for callers that already have coordinates)."""
    j, ms = _http.get_json(CENSUS_COORDS, {
        "x": lon, "y": lat, "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json",
    }, timeout=timeout)
    geos = (j.get("result") or {}).get("geographies") or {}
    out = {"lat": float(lat), "lon": float(lon), "matched": None, "house_no": None, "street": None, "zip": None, "ms": ms}
    out.update(_place_flags(geos))
    return out
