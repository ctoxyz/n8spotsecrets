"""Building: LA County assessor parcel at the point + LADBS permits within 60 m.

parcel()  -> ain, apn_dashed, year_built, units, use, sqft, beds, baths, situs
permits() -> count_5y, count_total, recent[] (date, type, description, status)
"""
import re
from datetime import datetime, timedelta, timezone

from ..http import SourceError, arcgis_query, pick, socrata

PARCEL_LAYER = "https://public.gis.lacounty.gov/public/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0"
LADBS = [("pi9x-tg5x", "2020+"), ("dyxf-7hc4", "2010-2019")]   # issued permits, data.lacity.org


def _int(v):
    try:
        return int(float(str(v).replace(",", "")))
    except Exception:
        return None


def _norm_street(s):
    s = re.sub(r"[^A-Z0-9 ]", "", str(s or "").upper())
    s = re.sub(r"\b(STREET|ST)\b", "ST", s)
    s = re.sub(r"\b(AVENUE|AVE)\b", "AVE", s)
    s = re.sub(r"\b(BOULEVARD|BLVD)\b", "BLVD", s)
    s = re.sub(r"\b(DRIVE|DR)\b", "DR", s)
    s = re.sub(r"\b(ROAD|RD)\b", "RD", s)
    s = re.sub(r"\b(PLACE|PL)\b", "PL", s)
    return re.sub(r"\s+", " ", s).strip()


def _score(r, house_no, street):
    """Lower is better: same street, nearest and same-parity house number, residential over parking/vacant."""
    sc = 0
    hn = re.sub(r"[^\d]", "", str(pick(r, r"^SitusHouseNo$", default="")))
    st = _norm_street(" ".join(str(pick(r, k, default="")) for k in (r"^SitusDirec", r"^SitusStreet$")))
    if street:
        want = _norm_street(street)
        if want and st and (want in st or st in want):
            sc -= 1000
    if house_no and hn:
        try:
            a, b = int(house_no), int(hn)
            sc += min(abs(a - b), 400)
            if (a % 2) != (b % 2):
                sc += 300
        except ValueError:
            pass
    elif house_no:
        sc += 200
    use = str(pick(r, r"^UseDescription$", r"^UseDesc", r"^UseType$", default="")).upper()
    if "PARKING" in use or "VACANT" in use:
        sc += 250
    return sc


def parcel(lat, lon, house_no=None, street=None, timeout=4.0):
    rows, ms = arcgis_query(PARCEL_LAYER, lat, lon, timeout=timeout, distance_m=40, max_records=8)
    if not rows:
        raise SourceError("no parcel within 40 m")
    chosen = min(rows, key=lambda r: _score(r, house_no, street))
    r = chosen
    ain = str(pick(r, r"^AIN$", r"^APN$", default="")).strip()
    street = " ".join(str(pick(r, k, default="")).strip() for k in (r"^SitusHouseNo$", r"^SitusFraction$", r"^SitusDirec", r"^SitusStreet$", r"^SitusUnit")).replace("  ", " ").strip()
    out = {
        "ain": ain,
        "apn_dashed": "%s-%s-%s" % (ain[:4], ain[4:7], ain[7:]) if len(ain) == 10 else ain,
        "year_built": _int(pick(r, r"^YearBuilt", r"YearBuilt1", r"^YearBlt")),
        "effective_year": _int(pick(r, r"^EffectiveYear", r"EffYear")),
        "units": _int(pick(r, r"^Units1?$", r"^Units", r"NumUnits")),
        "beds": _int(pick(r, r"^Bedrooms", r"^Beds")),
        "baths": _int(pick(r, r"^Bathrooms", r"^Baths")),
        "sqft": _int(pick(r, r"^SQFTmain", r"^SqFt", r"BldgSqFt")),
        "use": pick(r, r"^UseDescription$", r"^UseDesc", r"^UseType$", r"^UseCode"),
        "situs": street or None,
        "situs_city": pick(r, r"^SitusCity"),
        "situs_zip": pick(r, r"^SitusZIP", r"^SitusZip"),
        "parcels_nearby": len(rows),
        "matched_house_no": bool(house_no) and str(pick(chosen, r"^SitusHouseNo$", default="")).strip() == str(house_no),
        "ms": ms,
    }
    return out


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
