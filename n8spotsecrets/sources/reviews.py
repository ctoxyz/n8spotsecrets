"""Tenant reviews from Google Maps, for the apartment building at the address.

This is the one source that needs a key and can cost money, so it is not part of the report: the report never
calls it. A site that has a Google Maps Platform key (Places API (New)) asks for reviews separately, per view.

Google's rules, which this module is written around:
  - nothing from the answer may be stored or cached, except the place id; nothing here is kept
  - a review is shown with its author's name next to it, and with Google Maps named as the source
  - at most five reviews come back for a place

Finding the building: apartment-type places within 75 m of the point, nearest first. If Google has none of those
types there, one text search for the address is tried and anything further than 100 m is dropped."""
import json

from .. import http as _http
from ..http import SourceError

PLACES = "https://places.googleapis.com/v1/places"
TYPES = ["apartment_building", "apartment_complex", "condominium_complex", "housing_complex"]
MASK = ("places.id,places.displayName,places.formattedAddress,places.location,places.types,places.rating,"
        "places.userRatingCount,places.googleMapsUri,places.reviews")
NEAR_M = 75.0
TEXT_M = 100.0


def _post(method, body, key, timeout):
    return _http.get_json("%s:%s" % (PLACES, method), timeout=timeout, data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": MASK})


def _text(v):
    return (v or {}).get("text") if isinstance(v, dict) else (v if isinstance(v, str) else None)


def _place(p, lat, lon):
    loc = p.get("location") or {}
    d = None
    if loc.get("latitude") is not None and loc.get("longitude") is not None:
        d = int(_http.meters(lat, lon, float(loc["latitude"]), float(loc["longitude"])))
    revs = []
    for r in (p.get("reviews") or [])[:5]:
        au = r.get("authorAttribution") or {}
        body = _text(r.get("text")) or _text(r.get("originalText"))
        if not body:
            continue
        revs.append({"rating": r.get("rating"), "text": body[:900], "cut": len(body) > 900, "when": r.get("relativePublishTimeDescription"),
                     "author": au.get("displayName"), "author_url": au.get("uri"), "url": r.get("googleMapsUri"), "flag_url": r.get("flagContentUri")})
    return {"id": p.get("id"), "name": _text(p.get("displayName")), "address": p.get("formattedAddress"), "types": p.get("types") or [],
            "rating": p.get("rating"), "ratings": p.get("userRatingCount"), "maps_url": p.get("googleMapsUri"), "distance_m": d, "reviews": revs}


def lookup(lat, lon, address, key, timeout=4.0):
    """-> {place: {...} or None, candidates, how, ms}. Raises SourceError when Google does not answer or refuses."""
    if not key:
        raise SourceError("no Google key")
    circle = {"circle": {"center": {"latitude": float(lat), "longitude": float(lon)}, "radius": NEAR_M}}
    how, ms, places = "nearby", 0, []
    try:
        j, ms = _post("searchNearby", {"includedTypes": TYPES, "maxResultCount": 5, "rankPreference": "DISTANCE", "locationRestriction": circle}, key, timeout)
        places = [_place(p, lat, lon) for p in (j.get("places") or [])]
    except SourceError as e:
        if "HTTP 400" not in str(e):
            raise
    if not places and address:
        how = "text"
        circle["circle"]["radius"] = 2 * TEXT_M
        j, ms2 = _post("searchText", {"textQuery": "%s apartments" % address, "maxResultCount": 5, "locationBias": circle}, key, timeout)
        ms += ms2
        places = [p for p in (_place(x, lat, lon) for x in (j.get("places") or [])) if p["distance_m"] is not None and p["distance_m"] <= TEXT_M]
    places.sort(key=lambda p: (p["distance_m"] is None, p["distance_m"] or 0))
    rated = [p for p in places if p.get("ratings")]
    return {"place": (rated or places or [None])[0], "candidates": len(places), "how": how, "ms": ms}
