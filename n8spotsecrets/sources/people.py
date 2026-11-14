"""Who lives in the census tract and how they get around: ACS 5-year, same free Census key as the price check.

B25003 tenure, B01002 median age, B08301 means of travel to work, B08013 total minutes of commute,
B28002 internet subscriptions.
"""
from .. import http as _http
from ..http import SourceError

ACS = "https://api.census.gov/data/%s/acs/acs5"
VARS = "NAME,B25003_001E,B25003_003E,B01002_001E,B08301_001E,B08301_010E,B08301_019E,B08301_021E,B08013_001E,B28002_001E,B28002_004E,B28002_013E"


def _n(v):
    try:
        f = float(v)
        return None if f < 0 else f
    except Exception:
        return None


def _pct(a, b):
    return int(round(100.0 * a / b)) if a is not None and b else None


def people(tract, county, key, timeout=4.0):
    if not key:
        raise SourceError("CENSUS_API_KEY not set")
    if not tract:
        raise SourceError("no census tract")
    last = None
    for year in ("2023", "2022"):
        try:
            j, ms = _http.get_json(ACS % year, {"get": VARS, "for": "tract:%s" % tract,
                                                "in": "state:06 county:%s" % (county or "037"), "key": key}, timeout=timeout)
            if len(j) < 2:
                raise SourceError("no ACS row")
            d = dict(zip(j[0], j[1]))
            break
        except SourceError as e:
            last = e
    else:
        raise last or SourceError("ACS unavailable")
    hh, renters = _n(d.get("B25003_001E")), _n(d.get("B25003_003E"))
    workers, transit, walk, wfh = _n(d.get("B08301_001E")), _n(d.get("B08301_010E")), _n(d.get("B08301_019E")), _n(d.get("B08301_021E"))
    minutes = _n(d.get("B08013_001E"))
    commuters = (workers - (wfh or 0)) if workers else None
    nethh, bb, none = _n(d.get("B28002_001E")), _n(d.get("B28002_004E")), _n(d.get("B28002_013E"))
    return {
        "acs_year": year,
        "households": int(hh) if hh is not None else None,
        "renter_pct": _pct(renters, hh),
        "median_age": _n(d.get("B01002_001E")),
        "commute_min": int(round(minutes / commuters)) if minutes and commuters else None,
        "transit_pct": _pct(transit, workers), "walk_pct": _pct(walk, workers), "wfh_pct": _pct(wfh, workers),
        "broadband_pct": _pct(bb, nethh), "no_internet_pct": _pct(none, nethh),
        "ms": ms,
    }


# -- Section 8 (0.2.10), from HUD's open map layers. No key.
# Measured 14 Nov 2026: asking these layers by map point often takes more than 9 s; asking by tract number or by
# area name takes 0.15 to 0.65 s. So the tract is asked by number, and Los Angeles County's rent area by name.
HUD = "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services"
HUD_FMR = HUD + "/Fair_Market_Rents/FeatureServer/0"
HUD_VOUCHERS = HUD + "/Housing_Choice_Vouchers_by_Tract/FeatureServer/0"
_FMR_FIELDS = "FMR_AREANAME,FMR_0BDR,FMR_1BDR,FMR_2BDR,FMR_3BDR,FMR_4BDR"
_FMR_BY_COUNTY = {"06037": "Los Angeles-Long Beach-Glendale, CA HUD Metro FMR Area"}


def _hud_rows(url, where, fields, timeout):
    j, ms = _http.get_json(url + "/query", {"where": where, "outFields": fields, "returnGeometry": "false",
                                            "resultRecordCount": "1", "f": "json"}, timeout=timeout)
    return [f.get("attributes", {}) for f in (j.get("features") or [])], ms


def vouchers(tract_geoid, lat, lon, timeout=3.0):
    """Section 8 at this address: HUD's fair market rent by bedroom for the area (the federal benchmark voucher
    payments are set around; the local housing authority sets the exact figure), and how many households in this
    census tract rent with a voucher. HUD leaves the count empty for some tracts; that stays None.
    A part that did not answer is left out and named in `missing`."""
    from concurrent.futures import ThreadPoolExecutor
    geoid = str(tract_geoid or "")
    geoid = geoid if (len(geoid) == 11 and geoid.isdigit()) else ""
    area = _FMR_BY_COUNTY.get(geoid[:5])

    def rent():
        if area:
            return _hud_rows(HUD_FMR, "FMR_AREANAME = '%s'" % area, _FMR_FIELDS, timeout)
        return _http.arcgis_query(HUD_FMR, lat, lon, timeout=timeout, max_records=1, out_fields=_FMR_FIELDS)

    def tract():
        if not geoid:
            raise SourceError("no census tract number for this address")
        return _hud_rows(HUD_VOUCHERS, "GEOID = '%s'" % geoid, "GEOID,HCV_PUBLIC,HCV_PUBLIC_PCT", timeout)

    out, missing, ms = {}, {}, 0
    with ThreadPoolExecutor(max_workers=2) as ex:
        fa, fb = ex.submit(rent), ex.submit(tract)
        try:
            rows, t = fa.result()
            ms = max(ms, t)
            r = rows[0] if rows else None
            out["fair_market_rent"] = None if not r else {
                "area": r.get("FMR_AREANAME"), "studio": r.get("FMR_0BDR"), "br1": r.get("FMR_1BDR"), "br2": r.get("FMR_2BDR"),
                "br3": r.get("FMR_3BDR"), "br4": r.get("FMR_4BDR")}
        except SourceError as e:
            missing["fair_market_rent"] = str(e)[:120]
        try:
            rows, t = fb.result()
            ms = max(ms, t)
            r = rows[0] if rows else None
            pct = r.get("HCV_PUBLIC_PCT") if r else None
            # no row: HUD's layer has no tract with this number
            out["tract_vouchers"] = None if not r else {
                "tract": r.get("GEOID"), "households": r.get("HCV_PUBLIC"), "pct": round(pct, 1) if isinstance(pct, (int, float)) else None}
        except SourceError as e:
            missing["tract_vouchers"] = str(e)[:120]
    if len(missing) == 2:
        raise SourceError("; ".join("%s: %s" % kv for kv in sorted(missing.items()))[:300])
    out["missing"] = missing
    out["ms"] = ms
    return out
