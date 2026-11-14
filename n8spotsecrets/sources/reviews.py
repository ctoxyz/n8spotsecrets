"""Tenant reviews from Google Maps, for the apartment building at the address.

This is the one source that needs a key and can cost money, so it is not part of the report: the report never
calls it. A site that has a Google Maps Platform key (Places API (New)) asks for reviews separately, per view.

Google's rules, which this module is written around:
  - nothing from the answer may be stored or cached, except the place id; nothing here is kept
  - a review is shown with its author's name next to it, and with Google Maps named as the source
  - at most five reviews come back for a place

Finding the building: apartment-type places within 75 m of the point, then one text search for the address
(anything further than 250 m is dropped). A place is used only when Google's own address for it has the house
number and street that were asked for; the nearest apartment building is often the one across the street, and its
reviews are about another landlord. With no such place the answer is no place, and the page keeps its links."""
import json
import re

from .. import http as _http
from ..http import SourceError

PLACES = "https://places.googleapis.com/v1/places"
TYPES = ["apartment_building", "apartment_complex", "condominium_complex", "housing_complex"]
MASK = ("places.id,places.displayName,places.formattedAddress,places.location,places.types,places.rating,"
        "places.userRatingCount,places.googleMapsUri,places.reviews")
NEAR_M = 75.0
TEXT_M = 250.0
_SKIP = {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST", "ST", "STREET", "AVE", "AVENUE", "BLVD",
         "BOULEVARD", "DR", "DRIVE", "RD", "ROAD", "PL", "PLACE", "WAY", "LN", "LANE", "CT", "COURT", "TER", "TERRACE", "HWY", "PKWY"}


def same_address(asked, google_address):
    """Is Google's place at the house number and on the street that was asked for? Only the part before the first comma counts."""
    a = re.findall(r"[A-Z0-9]+", str(asked or "").upper().split(",")[0])
    g = re.findall(r"[A-Z0-9]+", str(google_address or "").upper().split(",")[0])
    if not a or not g or not a[0].isdigit() or a[0] != g[0]:
        return False
    core = [w for w in a[1:] if w not in _SKIP]
    return bool(core) and all(w in g[1:] for w in core)


def _pick(places, address):
    ok = sorted((p for p in places if same_address(address, p.get("address"))), key=lambda p: (p["distance_m"] is None, p["distance_m"] or 0))
    return ([p for p in ok if p.get("ratings")] or ok or [None])[0]


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
    """-> {place: {...} or None, candidates, other_address, how, ms}. Raises SourceError when Google does not answer or refuses.

    candidates: places Google returned in reach; other_address: how many of them are at another address and were not used."""
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
    place = _pick(places, address)
    if place is None and address:
        how = "text"
        circle["circle"]["radius"] = TEXT_M
        j, ms2 = _post("searchText", {"textQuery": "%s apartments" % address, "maxResultCount": 5, "locationBias": circle}, key, timeout)
        ms += ms2
        seen = {p.get("id") for p in places}
        more = [p for p in (_place(x, lat, lon) for x in (j.get("places") or [])) if p["distance_m"] is not None and p["distance_m"] <= TEXT_M]
        places += [p for p in more if p.get("id") not in seen]
        place = _pick(more, address)
    return {"place": place, "candidates": len(places), "other_address": len([p for p in places if not same_address(address, p.get("address"))]), "how": how, "ms": ms}
