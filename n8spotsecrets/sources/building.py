"""Building: LA County assessor parcel for the address + LADBS permits within 60 m.

parcel()  -> ain, apn_dashed, year_built, units, use, sqft, beds, baths, situs, match, parcel_count
permits() -> count_5y, count_total, recent[] (date, type, description, status)

0.2.2: the parcel used to be "the best of the first 8 parcels within 40 m of the point".
The Census point sits in the street, often 70 m from the building, and the county
service applies its record cap before its distance test, so that search returned
nothing (453 S Spring) or a neighbor (541 for 548 S Spring). Now:
  1. look the parcel up by its assessor address (house number + street name),
     keeping only rows on the same street within 500 m of the point;
  2. if the assessor has no such address, ask the ArcGIS geocoder for the rooftop
     point of the address and take the parcel that contains it;
  3. otherwise report no parcel. A neighbor is never returned.
A condo building is many parcels at one address; they are summed into one record.
"""
import math
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from .. import geocode as _geo
from ..http import SourceError, arcgis_query, get_json, pick, socrata

PARCEL_LAYER = "https://public.gis.lacounty.gov/public/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0"
PARCEL_FIELDS = ("AIN,SitusHouseNo,SitusFraction,SitusDirection,SitusUnit,SitusStreet,SitusCity,SitusZIP,"
                 "UseCode,UseType,UseDescription,YearBuilt1,EffectiveYear1,Units1,Bedrooms1,Bathrooms1,SQFTmain1,"
                 "CENTER_LAT,CENTER_LON")
PARCEL_MAX_ROWS = 1000        # the layer's maxRecordCount
SAME_ADDRESS_M = 500          # an assessor row with this house number and street must be this close to the point
LADBS = [("pi9x-tg5x", "2020+"), ("dyxf-7hc4", "2010-2019")]   # issued permits, data.lacity.org

_DIRS = {"N": "N", "S": "S", "E": "E", "W": "W", "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}
_TYPES = {"ST", "STREET", "AVE", "AVENUE", "BLVD", "BOULEVARD", "DR", "DRIVE", "RD", "ROAD", "PL", "PLACE", "WAY",
          "LN", "LANE", "CT", "COURT", "TER", "TERRACE", "PKWY", "PARKWAY", "HWY", "HIGHWAY", "CIR", "CIRCLE",
          "WALK", "ALY", "ALLEY", "TRL", "TRAIL", "SQ", "SQUARE"}


def _int(v):
    try:
        return int(float(str(v).replace(",", "")))
    except Exception:
        return None


def _year(v):
    y = _int(v)
    return y if y and 1800 <= y <= 2100 else None


def _street_parts(s):
    """'S SPRING ST.' -> ('S', 'SPRING');  'WILSHIRE BLVD' -> (None, 'WILSHIRE');  'W AVENUE 26' -> ('W', 'AVENUE 26')."""
    t = re.sub(r"[^A-Z0-9 ]", " ", str(s or "").upper()).split()
    d = None
    if len(t) >= 2 and t[0] in _DIRS:
        d = _DIRS[t[0]]
        t = t[1:]
    if len(t) >= 2 and t[-1] in _TYPES:
        t = t[:-1]
    return d, " ".join(t)


def _dist_m(lat1, lon1, lat2, lon2):
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2.0))
    y = math.radians(lat2 - lat1)
    return 6371000.0 * math.hypot(x, y)


def _same_street(row, want_dir, want_core):
    rd, rcore = _street_parts(row.get("SitusStreet"))
    if not rcore or rcore != want_core:
        return False
    d = _DIRS.get(str(row.get("SitusDirection") or "").strip().upper()) or rd
    return not (want_dir and d and want_dir != d)


def _by_address(lat, lon, house_no, street, timeout):
    """Assessor rows whose situs is this house number on this street, near the point."""
    hn = re.sub(r"[^0-9]", "", str(house_no or ""))
    want_dir, want_core = _street_parts(street)
    if not hn or not want_core:
        return [], 0
    where = "SitusHouseNo='%s' AND SitusStreet LIKE '%s%%'" % (hn, want_core.replace("'", "''"))
    j, ms = get_json(PARCEL_LAYER + "/query", {
        "where": where, "outFields": PARCEL_FIELDS, "returnGeometry": "false",
        "resultRecordCount": str(PARCEL_MAX_ROWS), "f": "json",
    }, timeout=timeout)
    rows = []
    for f in j.get("features") or []:
        a = f.get("attributes") or {}
        if not _same_street(a, want_dir, want_core):
            continue
        try:
            d = _dist_m(lat, lon, float(a["CENTER_LAT"]), float(a["CENTER_LON"]))
        except (KeyError, TypeError, ValueError):
            continue
        if d <= SAME_ADDRESS_M:
            rows.append(a)
    return rows, ms


def _by_point(lat, lon, timeout):
    """Assessor rows whose polygon contains the point (a condo building returns one row per unit)."""
    return arcgis_query(PARCEL_LAYER, lat, lon, timeout=timeout, out_fields=PARCEL_FIELDS, max_records=PARCEL_MAX_ROWS)


def _is_residential(r):
    return str(r.get("UseCode") or "").strip().startswith("0") or str(r.get("UseType") or "").strip().lower() == "residential"


def _most_common(vals, tie=min):
    vals = [v for v in vals if v not in (None, "")]
    if not vals:
        return None
    c = Counter(vals)
    top = max(c.values())
    return tie(v for v, n in c.items() if n == top)


def _summarize(rows, match, house_no, ms):
    rows = sorted(rows, key=lambda r: str(r.get("AIN") or ""))
    rep = next((r for r in rows if not str(r.get("SitusUnit") or "").strip()), rows[0])
    n = len(rows)
    ain = str(rep.get("AIN") or "").strip()
    rep_hn = str(rep.get("SitusHouseNo") or "").strip()
    if not rep_hn.strip("0"):
        rep_hn = ""          # the assessor stores "0" when a parcel has no street number
    d, core_street = _DIRS.get(str(rep.get("SitusDirection") or "").strip().upper()), re.sub(r"[.\s]+$", "", str(rep.get("SitusStreet") or "").strip())
    bits = [rep_hn, str(rep.get("SitusFraction") or "").strip(), d or "", core_street]
    if n == 1:
        bits.append(str(rep.get("SitusUnit") or "").strip())
    situs = " ".join(b for b in bits if b)
    if n == 1:
        year, eff = _year(rep.get("YearBuilt1")), _year(rep.get("EffectiveYear1"))
        units, beds, baths, sqft = _int(rep.get("Units1")), _int(rep.get("Bedrooms1")), _int(rep.get("Bathrooms1")), _int(rep.get("SQFTmain1"))
        use = pick(rep, r"^UseDescription$", r"^UseType$", r"^UseCode$")
    else:
        year = _most_common([_year(r.get("YearBuilt1")) for r in rows])
        eff = _most_common([_year(r.get("EffectiveYear1")) for r in rows])
        res = [r for r in rows if _is_residential(r)]
        units = sum(_int(r.get("Units1")) or 0 for r in (res or rows))
        if res and not units:
            units = len(res)
        beds = baths = None
        sqft = sum(_int(r.get("SQFTmain1")) or 0 for r in rows) or None
        with_unit = sum(1 for r in rows if str(r.get("SitusUnit") or "").strip())
        if len(res) >= 2 and with_unit >= 2:
            use = "Condominium building"
        else:
            use = _most_common([str(r.get("UseDescription") or "").strip() for r in (res or rows)], tie=lambda g: sorted(g)[0])
    return {
        "ain": ain,
        "ains": sorted({str(r.get("AIN") or "").strip() for r in rows} - {""}),      # every parcel of the building; not sent out
        "apn_dashed": "%s-%s-%s" % (ain[:4], ain[4:7], ain[7:]) if len(ain) == 10 else ain,
        "year_built": year,
        "effective_year": eff,
        "units": units,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "use": use,
        "situs": situs or "address not on the assessor record",
        "situs_city": pick(rep, r"^SitusCity$"),
        "situs_zip": pick(rep, r"^SitusZIP$"),
        "parcels_nearby": n,
        "parcel_count": n,
        "parcel_count_capped": n >= PARCEL_MAX_ROWS,
        "match": match,
        "matched_house_no": bool(house_no) and rep_hn == str(house_no).strip(),
        "ms": ms,
    }


def parcel(lat, lon, house_no=None, street=None, timeout=4.0):
    t0 = time.time()
    deadline = t0 + max(1.0, timeout)

    def left(cap):
        return max(0.5, min(cap, deadline - time.time()))

    why = []
    if house_no and street:
        try:
            rows, _ = _by_address(lat, lon, house_no, street, left(3.5))
            if rows:
                return _summarize(rows, "address", house_no, int((time.time() - t0) * 1000))
            why.append("the assessor has no %s on this street" % house_no)
        except SourceError as e:
            why.append("assessor address lookup failed (%s)" % str(e)[:60])
        roof = None
        if time.time() < deadline - 0.6:
            try:
                roof = _geo.rooftop(house_no, street, lat, lon, timeout=left(2.5))
            except SourceError as e:
                why.append("rooftop lookup failed (%s)" % str(e)[:60])
        if roof and time.time() < deadline - 0.4:
            try:
                rows, _ = _by_point(roof["lat"], roof["lon"], left(4.0))
            except SourceError as e:
                rows = []
                why.append("assessor point lookup failed (%s)" % str(e)[:60])
            if rows:
                return _summarize(rows, "rooftop", house_no, int((time.time() - t0) * 1000))
            why.append("no parcel under the rooftop point")
        elif not roof:
            why.append("no rooftop point for it")
    else:
        rows, _ = _by_point(lat, lon, left(4.0))
        if rows:
            return _summarize(rows, "point", None, int((time.time() - t0) * 1000))
        why.append("no parcel contains this point")
    raise SourceError("no parcel: " + "; ".join(why)[:150])


import threading
_PERMIT_GEO = {}          # dsid -> ("point", col) | ("latlon", latcol, loncol)
_PERMIT_LOCK = threading.Lock()


def _permit_geo(dsid, timeout):
    """Learn how this dataset stores location from one sample row (cached per process)."""
    with _PERMIT_LOCK:
        if dsid in _PERMIT_GEO:
            return _PERMIT_GEO[dsid]
    rows, _ = socrata("data.lacity.org", dsid, {"$limit": "1"}, timeout=timeout)
    geo = None
    if rows:
        r = rows[0]
        for k, v in r.items():
            if isinstance(v, dict) and ("coordinates" in v or "latitude" in v):
                geo = ("point", k)
                break
        if not geo:
            latc = next((k for k in r if k.lower() in ("latitude", "lat")), None)
            lonc = next((k for k in r if k.lower() in ("longitude", "lon", "long")), None)
            if latc and lonc:
                geo = ("latlon", latc, lonc)
    if not geo:
        raise SourceError("%s: no location column in sample row" % dsid)
    with _PERMIT_LOCK:
        _PERMIT_GEO[dsid] = geo
    return geo


def _permit_rows(dsid, lat, lon, timeout):
    geo = _permit_geo(dsid, timeout)
    if geo[0] == "point":
        where = "within_circle(%s, %s, %s, 60)" % (geo[1], lat, lon)
    else:
        dl = 0.00055
        where = "%s between %s and %s AND %s between %s and %s" % (geo[1], lat - dl, lat + dl, geo[2], lon - dl, lon + dl)
    params = {"$limit": "40", "$order": ":id DESC", "$where": where}
    return socrata("data.lacity.org", dsid, params, timeout=timeout)


def permits(lat, lon, timeout=4.0):
    from concurrent.futures import ThreadPoolExecutor
    rows_all = []
    ms_total = 0
    errors = []
    with ThreadPoolExecutor(max_workers=len(LADBS)) as ex:
        futs = {ex.submit(_permit_rows, dsid, lat, lon, timeout): (dsid, label) for dsid, label in LADBS}
        for f, (dsid, label) in futs.items():
            try:
                rows, ms = f.result(timeout=timeout + 0.5)
                ms_total = max(ms_total, ms)
                for r in rows:
                    r["_set"] = label
                rows_all.extend(rows)
            except SourceError as e:
                errors.append("%s: %s" % (dsid, e))
            except Exception as e:
                errors.append("%s: %s" % (dsid, str(e)[:80] or "timeout"))
    if not rows_all and errors:
        raise SourceError("; ".join(errors)[:200])
    recent_set = [e for e in errors if e.startswith(LADBS[0][0])]
    if recent_set:
        # the five-year count lives in the 2020+ set; without it "0 permits in 5 years" would be a guess
        raise SourceError("LADBS permits since 2020 did not answer (%s)" % recent_set[0][:120])

    def pdate(r):
        v = pick(r, r"issue_date", r"^issue", r"status_date", r"date")
        try:
            return datetime.fromisoformat(str(v)[:19])
        except Exception:
            return None

    items = []
    for r in rows_all:
        d = pdate(r)
        items.append({
            "date": d.date().isoformat() if d else None,
            "type": pick(r, r"^permit_type$", r"permit_type", r"^type$"),
            "sub_type": pick(r, r"permit_sub_type", r"sub_type"),
            "description": (str(pick(r, r"work_desc", r"description", r"use_desc", default="")) or "")[:140],
            "status": pick(r, r"^status_desc$", r"^status$", r"status"),
            "valuation": pick(r, r"valuation"),
            "address": pick(r, r"primary_address", r"^address$"),
            "_d": d,
        })
    items.sort(key=lambda x: x["_d"] or datetime.min, reverse=True)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365 * 5)
    out = {
        "count_total": len(items),
        "count_5y": sum(1 for i in items if i["_d"] and i["_d"] >= cutoff),
        "recent": [{k: v for k, v in i.items() if k != "_d"} for i in items[:6]],
        "ms": ms_total,
    }
    if errors:
        out["partial"] = errors
    return out
