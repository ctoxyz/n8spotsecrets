"""Daily life within 800 m: what is open for business around the address, and the parks.

Businesses come from the City of LA "Listing of Active Businesses" (business tax registry, data.lacity.org 6rrh-rzua).
It is a registry, not a review site: it knows a grocery is licensed there, not whether it is any good.
Parks come from LA Recreation and Parks boundaries. City of Los Angeles only.
"""
from ..http import SourceError, arcgis_query, meters, socrata

BUSINESSES = "6rrh-rzua"
PARKS = "https://services5.arcgis.com/7nsPwEMP38bSkCjy/arcgis/rest/services/LARAP_Parks_040220/FeatureServer/0"

# label -> words looked for in the registry's own category text
CATEGORIES = [
    ("grocery", ("grocery",)),
    ("pharmacy", ("pharmac", "drug store", "drugstore")),
    ("restaurants", ("restaurant", "eating place")),
    ("bars", ("drinking place",)),
    ("coffee_snack", ("snack", "coffee")),
    ("fitness", ("fitness", "recreational sports")),
    ("laundry", ("laundr", "drycleaning", "dry cleaning")),
    ("banks", ("banking", "credit union", "depository credit")),
    ("hotels", ("traveler accommodation",)),
]


def _cat(desc):
    d = str(desc or "").lower()
    for label, words in CATEGORIES:
        if any(w in d for w in words):
            return label
    return None


def _name(r):
    return str(r.get("dba_name") or r.get("business_name") or "").strip().title()


def places(lat, lon, timeout=4.0):
    circle = "within_circle(location_1, %s, %s, 800)" % (lat, lon)
    rows, ms = socrata("data.lacity.org", BUSINESSES, {
        "$select": "primary_naics_description, count(*) as n", "$where": circle,
        "$group": "primary_naics_description", "$order": "n DESC", "$limit": "500",
    }, timeout=timeout)
    counts = {label: 0 for label, _ in CATEGORIES}
    total = 0
    for r in rows:
        n = int(r.get("n") or 0)
        total += n
        c = _cat(r.get("primary_naics_description"))
        if c:
            counts[c] += n
    out = {"radius_m": 800, "businesses": total, "counts": counts,
           "top_types": [{"type": r.get("primary_naics_description"), "n": int(r.get("n") or 0)} for r in rows[:12] if r.get("primary_naics_description")],
           "groceries": [], "pharmacies": [], "ms": ms}
    try:
        near, ms2 = socrata("data.lacity.org", BUSINESSES, {
            "$select": "business_name, dba_name, street_address, primary_naics_description, location_1",
            "$where": circle + " AND (upper(primary_naics_description) like '%GROCERY%' OR upper(primary_naics_description) like '%PHARMAC%' OR upper(primary_naics_description) like '%DRUG%')",
            "$limit": "300",
        }, timeout=timeout)
        out["ms"] += ms2
        seen = set()
        for r in near:
            loc = r.get("location_1") or {}
            d = meters(lat, lon, loc.get("latitude"), loc.get("longitude"))
            nm = _name(r)
            if not nm or d is None or (nm, d) in seen:
                continue
            seen.add((nm, d))
            item = {"name": nm, "address": str(r.get("street_address") or "").strip().title(), "distance_m": d}
            (out["pharmacies"] if _cat(r.get("primary_naics_description")) == "pharmacy" else out["groceries"]).append(item)
        out["groceries"] = sorted(out["groceries"], key=lambda x: x["distance_m"])[:5]
        out["pharmacies"] = sorted(out["pharmacies"], key=lambda x: x["distance_m"])[:3]
    except SourceError as e:
        out["named_error"] = str(e)[:120]
    return out


def parks(lat, lon, timeout=4.0):
    near, ms = arcgis_query(PARKS, lat, lon, timeout=timeout, distance_m=400, out_fields="Name,Park_Class,Area_Acres", max_records=10)
    far, ms2 = arcgis_query(PARKS, lat, lon, timeout=timeout, distance_m=800, out_fields="Name,Park_Class,Area_Acres", max_records=12)
    close = {str(r.get("Name") or "").strip() for r in near}
    out, seen = [], set()
    for r in near + far:
        nm = str(r.get("Name") or "").strip()
        if not nm or nm in seen:
            continue
        seen.add(nm)
        acres = r.get("Area_Acres")
        out.append({"name": nm.title(), "kind": str(r.get("Park_Class") or "").strip().title() or None,
                    "acres": round(float(acres), 1) if acres not in (None, "") else None, "within_m": 400 if nm in close else 800})
    return {"radius_m": 800, "parks": out[:8], "within_400m": len(close), "ms": ms + ms2}


def walk_index(am, pk, tr, bk):
    """0 to 100 from the records above. This is our own arithmetic, not Walk Score."""
    pts, why = 0, []
    g = (am or {}).get("groceries") or []
    if g:
        add = 25 if g[0]["distance_m"] <= 400 else 15
        pts += add
        why.append("grocery %d m" % g[0]["distance_m"])
    elif (am or {}).get("counts", {}).get("grocery"):
        pts += 10
    if (am or {}).get("pharmacies"):
        pts += 10
    c = (am or {}).get("counts") or {}
    eat = c.get("restaurants", 0) + c.get("coffee_snack", 0)
    pts += min(20, eat)
    if eat:
        why.append("%d places to eat" % eat)
    if (am or {}).get("businesses", 0) >= 300:
        pts += 5
    if (pk or {}).get("parks"):
        pts += 10 if pk.get("within_400m") else 6
    nb = (tr or {}).get("nearest_bus")
    if nb and nb.get("distance_m") is not None:
        pts += 10 if nb["distance_m"] <= 200 else 5 if nb["distance_m"] <= 400 else 2
    if (tr or {}).get("rail"):
        pts += 15
        why.append("rail %d m" % (tr["rail"][0].get("distance_m") or 0))
    if (bk or {}).get("bikeways"):
        pts += 5
    pts = max(0, min(100, pts))
    label = "walk to everything" if pts >= 85 else "very walkable" if pts >= 70 else "walkable for some errands" if pts >= 50 else "car helps" if pts >= 25 else "car needed"
    return {"score": pts, "label": label, "basis": why[:4]}
