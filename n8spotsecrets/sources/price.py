"""Price check: ACS 5-year tract medians. Needs a free Census API key (api.census.gov/data/key_signup.html).

Variables: B25064_001E median gross rent, B25031_003E 1-bed, B25031_004E 2-bed, B25031_005E 3-bed,
B19013_001E median household income, B01003_001E population.
"""
from .. import http as _http
from ..http import SourceError

ACS = "https://api.census.gov/data/%s/acs/acs5"
VARS = "NAME,B25064_001E,B25031_002E,B25031_003E,B25031_004E,B25031_005E,B19013_001E,B01003_001E"


def _num(v):
    try:
        f = float(v)
        return None if f < 0 else int(f)
    except Exception:
        return None


def _fetch(year, geo_for, geo_in, key, timeout):
    params = {"get": VARS, "for": geo_for, "key": key}
    if geo_in:
        params["in"] = geo_in
    j, ms = _http.get_json(ACS % year, params, timeout=timeout)
    if len(j) < 2:
        raise SourceError("no ACS row")
    return dict(zip(j[0], j[1])), ms


def price(tract, county, key, asking=None, beds=None, zip_code=None, timeout=4.0):
    if not key:
        raise SourceError("CENSUS_API_KEY not set")
    if not tract:
        raise SourceError("no census tract")
    last = None
    d = None
    for year in ("2023", "2022"):
        try:
            d, ms = _fetch(year, "tract:%s" % tract, "state:06 county:%s" % (county or "037"), key, timeout)
            break
        except SourceError as e:
            last = e
    if d is None:
        raise last or SourceError("ACS unavailable")
    level = "tract"
    if _num(d.get("B25064_001E")) is None and zip_code:
        # small or non-residential tracts have suppressed medians; fall back to the ZIP (ZCTA)
        try:
            dz, msz = _fetch(year, "zip code tabulation area:%s" % zip_code, None, key, timeout)
            if _num(dz.get("B25064_001E")) is not None:
                d, ms, level = dz, ms + msz, "zip"
        except SourceError:
            pass
    out = {
        "acs_year": year,
        "level": level,
        "tract_name": d.get("NAME"),
        "median_rent": _num(d.get("B25064_001E")),
        "median_rent_by_beds": {"0": _num(d.get("B25031_002E")), "1": _num(d.get("B25031_003E")), "2": _num(d.get("B25031_004E")), "3": _num(d.get("B25031_005E"))},
        "median_income": _num(d.get("B19013_001E")),
        "population": _num(d.get("B01003_001E")),
        "ms": ms,
    }
    ref = None
    if beds is not None and str(beds) in out["median_rent_by_beds"]:
        ref = out["median_rent_by_beds"][str(beds)]
    ref = ref or out["median_rent"]
    out["reference_rent"] = ref
    if asking and ref:
        pct = round((float(asking) - ref) / ref * 100)
        out["asking"] = asking
        out["vs_median_pct"] = pct
        out["verdict"] = "above" if pct > 10 else "below" if pct < -10 else "in line"
    out["note"] = ("ACS medians reflect all tenants in the %s, including long-term leases; current asking rents run higher." % ("ZIP code" if level == "zip" else "tract"))
    if level == "zip":
        out["note"] = "Tract median suppressed (too few renters sampled); ZIP-level median shown. " + out["note"]
    return out
