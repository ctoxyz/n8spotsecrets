"""Safety: LAPD crime within 500 m. The open dataset (2nrs-mtv8) ends 2024-12-30 (NIBRS transition),
so this reports calendar-2024 counts and says so. The window is recomputed if the dataset gains newer rows.
"""
from ..http import SourceError, socrata

LAPD = "2nrs-mtv8"
DOMAIN = "data.lacity.org"
DL = 0.0045   # ~500 m in degrees at LA latitude


def _bbox(lat, lon):
    return "lat between %s and %s AND lon between %s and %s" % (lat - DL, lat + DL, lon - DL, lon + DL)


def crime(lat, lon, timeout=4.0):
    newest, ms0 = socrata(DOMAIN, LAPD, {"$select": "max(date_occ) as newest"}, timeout=timeout)
    newest_s = (newest[0].get("newest") or "2024-12-31")[:10]
    year = newest_s[:4]
    start = "%s-01-01T00:00:00" % year
    end = "%s-12-31T23:59:59" % year
    where = "%s AND date_occ between '%s' and '%s'" % (_bbox(lat, lon), start, end)
    total, ms1 = socrata(DOMAIN, LAPD, {"$select": "count(*) as n", "$where": where}, timeout=timeout)
    types, ms2 = socrata(DOMAIN, LAPD, {
        "$select": "crm_cd_desc, count(*) as n", "$where": where, "$group": "crm_cd_desc", "$order": "n desc", "$limit": "6",
    }, timeout=timeout)
    violent_kw = ("ASSAULT", "ROBBERY", "HOMICIDE", "RAPE", "BATTERY", "SHOTS", "WEAPON", "KIDNAP")
    violent, ms3 = socrata(DOMAIN, LAPD, {
        "$select": "count(*) as n",
        "$where": where + " AND (" + " OR ".join("upper(crm_cd_desc) like '%%%s%%'" % k for k in violent_kw) + ")",
    }, timeout=timeout)
    n = int(total[0].get("n") or 0)
    v = int(violent[0].get("n") or 0)
    return {
        "window": "calendar %s" % year,
        "radius_m": 500,
        "incidents": n,
        "violent": v,
        "per_month": round(n / 12.0, 1),
        "top_types": [{"type": t.get("crm_cd_desc"), "n": int(t.get("n") or 0)} for t in types],
        "data_ends": newest_s,
        "note": "LAPD open data ends %s (NIBRS transition); newer incidents are not published here." % newest_s,
        "ms": ms0 + ms1 + ms2 + ms3,
    }
