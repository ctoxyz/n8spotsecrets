"""Safety: LAPD crime around the address, from both of the city's public datasets.

Old system (2nrs-mtv8, incident points, 2020 into 2024): counts within 500 m. LAPD switched reporting systems
during 2024, so that year is partial in this dataset; the report uses the last complete year and says which.
New system (k7nn-b2ep, NIBRS offenses, 2024 to now): no usable map point, so it is read by LAPD reporting district.
That gives the current picture and a same-months trend against last year.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..http import SourceError, socrata

LAPD = "2nrs-mtv8"
NIBRS = "k7nn-b2ep"
DOMAIN = "data.lacity.org"
DL = 0.0045   # ~500 m in degrees at LA latitude
NEAR = 0.00135   # ~150 m: the block around the address
# The old dataset stops on 2024-12-30 and LAPD moved districts to the new system during 2024, so 2024 is short
# everywhere, by a different amount in each place. Every address is counted on the same last whole year.
LAST_FULL_YEAR = "2023"
VIOLENT_KW = ("ASSAULT", "ROBBERY", "HOMICIDE", "RAPE", "BATTERY", "SHOTS", "WEAPON", "KIDNAP")
NIBRS_VIOLENT = ("09A", "09B", "100", "11A", "11B", "11C", "11D", "120", "13A", "13B")


def _bbox(lat, lon, d=DL):
    return "lat between %s and %s AND lon between %s and %s" % (lat - d, lat + d, lon - d, lon + d)


def _full_year(lat, lon, timeout):
    """Newest year that looks complete: a year under 70% of the one before it is treated as partial."""
    rows, ms = socrata(DOMAIN, LAPD, {"$select": "date_trunc_y(date_occ) as yr, count(*) as n", "$where": _bbox(lat, lon),
                                      "$group": "yr", "$order": "yr"}, timeout=timeout)
    by = [(str(r.get("yr") or "")[:4], int(r.get("n") or 0)) for r in rows if r.get("yr")]
    by = [(y, n) for y, n in by if y.isdigit()]
    if not by:
        return None, [], ms
    year = by[-1][0]
    if len(by) >= 2 and by[-1][1] < 0.7 * by[-2][1]:
        year = by[-2][0]
    return year, [{"year": y, "n": n} for y, n in by], ms


def now(lat, lon, timeout=4.0):
    """Current picture from the NIBRS dataset, by the reporting district the address falls in."""
    rows, ms = socrata(DOMAIN, LAPD, {"$select": "rpt_dist_no, area_name, count(*) as n", "$where": _bbox(lat, lon, 0.0015),
                                      "$group": "rpt_dist_no, area_name", "$order": "n DESC", "$limit": "1"}, timeout=timeout)
    if not rows or not rows[0].get("rpt_dist_no"):
        raise SourceError("no reporting district near the point")
    rd = str(rows[0]["rpt_dist_no"]).strip()
    area = str(rows[0].get("area_name") or "").strip()
    clock_year = str(datetime.now(timezone.utc).year)

    def _months():
        return socrata(DOMAIN, NIBRS, {"$select": "date_trunc_ym(date_occ) as m, count(*) as n",
                                       "$where": "rpt_dist_no = '%s' AND date_occ >= '%d-01-01T00:00:00'" % (rd, int(clock_year) - 1),
                                       "$group": "m", "$order": "m"}, timeout=timeout)

    def _violent():
        return socrata(DOMAIN, NIBRS, {"$select": "count(*) as n",
                                       "$where": "rpt_dist_no = '%s' AND date_occ >= '%s-01-01T00:00:00' AND nibr_code in (%s)" % (
                                           rd, clock_year, ",".join("'%s'" % c for c in NIBRS_VIOLENT))}, timeout=timeout)
    with ThreadPoolExecutor(max_workers=2) as ex:
        fm, fv = ex.submit(_months), ex.submit(_violent)
        months, ms2 = fm.result()
        try:
            v, ms3 = fv.result()
        except SourceError:
            v, ms3 = None, 0
    by = {}
    for r in months:
        m = str(r.get("m") or "")[:7]
        if len(m) == 7:
            by[m] = int(r.get("n") or 0)
    if not by:
        raise SourceError("no NIBRS rows for district %s" % rd)
    last = max(by)
    this_year = last[:4]
    prev_year = str(int(this_year) - 1)
    done = sorted(m for m in by if m[:4] == this_year and m < last)      # complete months only
    ytd = sum(by[m] for m in done)
    same = sum(by.get(prev_year + m[4:], 0) for m in done)
    out = {
        "district": rd, "area": area, "through": last,
        "year": this_year, "months_counted": len(done), "count": ytd,
        "prev_year": prev_year, "prev_same_months": same, "prev_full_year": sum(n for m, n in by.items() if m[:4] == prev_year),
        "trend_pct": int(round(100.0 * (ytd - same) / same)) if same and len(done) >= 3 else None,
        "ms": ms + ms2 + ms3,
    }
    if v and this_year == clock_year:
        out["violent_ytd"] = int(v[0].get("n") or 0)
    return out


def crime(lat, lon, timeout=4.0):
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_new = ex.submit(socrata, DOMAIN, LAPD, {"$select": "max(date_occ) as newest"}, timeout)
        f_yr = ex.submit(_full_year, lat, lon, timeout)
        newest, ms0 = f_new.result()
        try:
            full, years, ms_y = f_yr.result()
        except SourceError:
            full, years, ms_y = None, [], 0
    newest_s = (newest[0].get("newest") or "2024-12-31")[:10]
    year = full or newest_s[:4]
    if year > LAST_FULL_YEAR:
        year = LAST_FULL_YEAR
    start = "%s-01-01T00:00:00" % year
    end = "%s-12-31T23:59:59" % year
    where = "%s AND date_occ between '%s' and '%s'" % (_bbox(lat, lon), start, end)
    q_total = {"$select": "count(*) as n", "$where": where}
    q_types = {"$select": "crm_cd_desc, count(*) as n", "$where": where, "$group": "crm_cd_desc", "$order": "n desc", "$limit": "6"}
    q_violent = {"$select": "count(*) as n",
                 "$where": where + " AND (" + " OR ".join("upper(crm_cd_desc) like '%%%s%%'" % k for k in VIOLENT_KW) + ")"}
    q_night = {"$select": "count(*) as n", "$where": where + " AND (time_occ >= '2000' OR time_occ < '0600')"}
    near_where = where.replace(_bbox(lat, lon), _bbox(lat, lon, NEAR))
    q_near = {"$select": "count(*) as n", "$where": near_where}
    q_near_violent = {"$select": "count(*) as n", "$where": q_violent["$where"].replace(_bbox(lat, lon), _bbox(lat, lon, NEAR))}
    with ThreadPoolExecutor(max_workers=6) as ex:
        ft, fy, fv, fn, fa, fb = [ex.submit(socrata, DOMAIN, LAPD, q, timeout) for q in (q_total, q_types, q_violent, q_night, q_near, q_near_violent)]
        total, ms1 = ft.result()
        types, ms2 = fy.result()
        violent, ms3 = fv.result()
        try:
            night, ms4 = fn.result()
        except SourceError:
            night, ms4 = None, 0
    n = int(total[0].get("n") or 0)
    v = int(violent[0].get("n") or 0)
    partial = year != newest_s[:4]
    out = {
        "window": "calendar %s" % year,
        "radius_m": 500,
        "incidents": n,
        "violent": v,
        "per_month": round(n / 12.0, 1),
        "top_types": [{"type": t.get("crm_cd_desc"), "n": int(t.get("n") or 0)} for t in types],
        "by_year": years,
        "data_ends": newest_s,
        "note": ("LAPD's old incident dataset ends %s and its last year is partial, so %s is the last complete year in it." % (newest_s, year)) if partial
                else "LAPD open data ends %s (NIBRS transition); newer incidents are not published here." % newest_s,
        "ms": ms0 + ms_y + max(ms1, ms2, ms3, ms4),
    }
    try:
        out["near"] = {"box_m": 150, "incidents": int(fa.result()[0][0].get("n") or 0), "violent": int(fb.result()[0][0].get("n") or 0)}
    except SourceError as e:
        out["near_error"] = str(e)          # left out, never shown as zero
    if night:
        out["night"] = int(night[0].get("n") or 0)
        out["night_pct"] = int(round(100.0 * out["night"] / n)) if n else None
    return out
