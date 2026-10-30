"""Assemble one address report from the sources, inside a hard time budget.

build_report(address=..., or lat=, lon=, census_key=None, budget_s=8.0, asking=None, beds=None)

Stage 1  geocode (everything needs the point; tract/city decide coverage)
Stage 2  in parallel: parcel, permits, crime, flood, fault, red_flag, calfire, noise, price
Stage 3  cases + habitability (need the parcel number from stage 2)
Any source that misses its slot is reported as status "no_data" with the reason; the report still prints.
"""
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone

from . import geocode as geo
from .http import SourceError
from .sources import building, cases, crime, hazards, noise, price

VERSION = "0.1.0"
RSO_CUTOFF_YEAR = 1978   # LA RSO: built on or before Oct 1, 1978, 2+ units, in the City of LA

SOURCES = [
    ("US Census Bureau geocoder", "https://geocoding.geo.census.gov/"),
    ("LA County Assessor parcels (GIS)", "https://egis-lacounty.hub.arcgis.com/"),
    ("LADBS building permits (data.lacity.org pi9x-tg5x, dyxf-7hc4)", "https://data.lacity.org/"),
    ("LADBS code enforcement cases (u82d-eh7z, rken-a55j)", "https://data.lacity.org/"),
    ("LAHD habitability violations (cr8f-uc4j)", "https://data.lacity.org/"),
    ("LAPD crime data 2020-2024 (2nrs-mtv8)", "https://data.lacity.org/"),
    ("FEMA National Flood Hazard Layer", "https://hazards.fema.gov/"),
    ("California Geological Survey Alquist-Priolo fault traces", "https://gis.conservation.ca.gov/"),
    ("LAFD Red Flag restricted areas (LA GeoHub)", "https://geohub.lacity.org/"),
    ("US Census TIGERweb roads and rail", "https://tigerweb.geo.census.gov/"),
    ("US Census ACS 5-year estimates", "https://api.census.gov/"),
]

DISCLAIMER = ("Public records, pulled live. Not legal advice, not a tenant screen, not a substitute for seeing the unit. "
              "Datasets lag and have gaps; a blank section means the source did not answer in time, not that nothing happened.")


class _Budget:
    def __init__(self, seconds):
        self.deadline = time.time() + seconds

    def left(self, floor=0.5, cap=None):
        r = self.deadline - time.time()
        if cap is not None:
            r = min(r, cap)
        return max(floor, r)


def _section(status, ms=None, data=None, headline=None, error=None, **extra):
    s = {"status": status}
    if ms is not None:
        s["ms"] = int(ms)
    if headline:
        s["headline"] = headline
    if data is not None:
        s["data"] = data
    if error:
        s["error"] = str(error)[:200]
    s.update(extra)
    return s


def _run(fn, *args, **kw):
    """Run a source; return ('ok', data) or ('no_data', error)."""
    try:
        return "ok", fn(*args, **kw)
    except SourceError as e:
        return "no_data", str(e)
    except Exception as e:  # a bug in a source must not take the report down
        return "no_data", "internal: %s" % str(e)[:120]


def _headline_building(p, rso):
    bits = []
    if p.get("year_built"):
        bits.append("Built %s" % p["year_built"])
    if p.get("units") and p["units"] > 0:
        bits.append("%s unit%s" % (p["units"], "" if p["units"] == 1 else "s"))
    if p.get("use"):
        bits.append(str(p["use"]).title())
    if rso.get("likely") is True:
        bits.append("likely rent-stabilized (RSO)")
    elif rso.get("likely") is False and rso.get("reason"):
        bits.append("not RSO (%s)" % rso["reason"])
    return " · ".join(bits) or "Parcel found"


def _rso(p, la_city):
    if not la_city:
        return {"likely": None, "reason": "RSO is a City of Los Angeles ordinance; this address is outside the city"}
    yb, units = p.get("year_built"), p.get("units")
    use = str(p.get("use") or "").lower()
    multi_by_use = any(k in use for k in ("apartment", "five or more", "four units", "three units", "two units", "duplex", "triplex", "fourplex", "condominium", "rooming", "residential income"))
    single_by_use = any(k in use for k in ("single family", "single residence", "sfr"))
    if not yb:
        return {"likely": None, "reason": "year built not on the parcel record"}
    if yb > RSO_CUTOFF_YEAR:
        return {"likely": False, "reason": "built after 1978"}
    if (units is not None and units >= 2) or multi_by_use:
        pass
    elif single_by_use or (units is not None and units == 1):
        return {"likely": False, "reason": "single unit"}
    elif units in (None, 0):
        return {"likely": None, "reason": "built %s but the unit count is not on the parcel record; check ZIMAS" % yb}
    return {"likely": True, "reason": "built %s, %s units, City of LA — meets the RSO test; confirm on ZIMAS or with LAHD" % (yb, units if units is not None else "2+"),
            "derived": True}


def build_report(address=None, lat=None, lon=None, census_key=None, budget_s=9.0, asking=None, beds=None):
    t_start = time.time()
    budget = _Budget(budget_s)
    sections = {}
    warnings = []

    # ── stage 1: where is it
    try:
        if address:
            g = geo.geocode(address, timeout=budget.left(cap=4.0))
        elif lat is not None and lon is not None:
            g = geo.reverse(lat, lon, timeout=budget.left(cap=4.0))
        else:
            raise SourceError("address or lat/lon required")
    except SourceError as e:
        not_found = str(e) in ("address not found", "address or lat/lon required")
        return {
            "version": VERSION, "ok": False, "error": str(e) if not_found else "the address lookup service did not answer",
            "hint": ("Use a street number and street name, e.g. '548 S Spring St, Los Angeles'. Cross streets and neighborhoods are not addresses."
                     if not_found else "The US Census address service is slow or down. Try again in a minute."),
            "elapsed_ms": int((time.time() - t_start) * 1000),
        }
    la_city = bool(g.get("la_city"))
    coverage = "full" if la_city else ("partial" if g.get("la_county") else "minimal")

    # ── stage 2: everything that only needs the point
    pool = ThreadPoolExecutor(max_workers=10)
    futs = {
        "parcel": pool.submit(_run, building.parcel, g["lat"], g["lon"], g.get("house_no"), g.get("street"), budget.left(cap=6.0)),
        "flood": pool.submit(_run, hazards.flood, g["lat"], g["lon"], budget.left(cap=4.0)),
        "fault": pool.submit(_run, hazards.fault, g["lat"], g["lon"], budget.left(cap=4.0)),
        "calfire": pool.submit(_run, hazards.calfire, g["lat"], g["lon"], budget.left(cap=3.0)),
        "noise": pool.submit(_run, noise.noise, g["lat"], g["lon"], budget.left(cap=4.5)),
    }
    if la_city:
        futs["crime"] = pool.submit(_run, crime.crime, g["lat"], g["lon"], budget.left(cap=5.0))
        futs["permits"] = pool.submit(_run, building.permits, g["lat"], g["lon"], budget.left(cap=5.0))
        futs["red_flag"] = pool.submit(_run, hazards.red_flag, g["lat"], g["lon"], budget.left(cap=3.5))
    if census_key:
        futs["price"] = pool.submit(_run, price.price, g.get("tract"), g.get("county"), census_key, asking, beds, g.get("zip"), budget.left(cap=5.0))

    # ── stage 3: parcel-keyed sources, started the moment the parcel arrives
    stage3 = {}
    results = {}
    pending = set(futs.values())
    while pending:
        done, pending = wait(pending, timeout=max(0.05, budget.left(floor=0.05)), return_when=FIRST_COMPLETED)
        for f in done:
            name = next(k for k, v in futs.items() if v is f)
            results[name] = f.result()
            if name == "parcel" and results[name][0] == "ok" and la_city and not stage3:
                p = results[name][1]
                stage3["cases_open"] = pool.submit(_run, cases.open_cases, p.get("ain"), p.get("apn_dashed"), budget.left(cap=4.5))
                stage3["cases_closed"] = pool.submit(_run, cases.closed_cases, p.get("ain"), p.get("apn_dashed"), budget.left(cap=4.5))
                stage3["habitability"] = pool.submit(_run, cases.habitability, p.get("ain"), p.get("apn_dashed"), budget.left(cap=4.5))
                futs.update(stage3)
                pending |= set(stage3.values())
        if not done and time.time() >= budget.deadline:
            break
    for name, f in futs.items():
        if name not in results:
            results[name] = ("no_data", "timed out (%.0f s budget)" % budget_s)
    pool.shutdown(wait=False)

    # ── assemble
    def got(name):
        st, val = results.get(name, ("no_data", "not run"))
        return (val if st == "ok" else None), (None if st == "ok" else val)

    p, perr = got("parcel")
    rso = _rso(p, la_city) if p else {"likely": None, "reason": "no parcel"}
    if p:
        sections["building"] = _section("ok", p.get("ms"), data=dict(p, rso=rso), headline=_headline_building(p, rso))
    else:
        sections["building"] = _section("no_data", error=perr)

    pm, pmerr = got("permits")
    if not la_city:
        sections["permits"] = _section("skipped", error="LADBS covers the City of Los Angeles only")
    elif pm:
        hl = "%d permit%s within 60 m in 5 years" % (pm["count_5y"], "" if pm["count_5y"] == 1 else "s")
        if pm["recent"] and pm["recent"][0].get("date"):
            hl += " · latest %s" % pm["recent"][0]["date"]
        sections["permits"] = _section("ok", pm.get("ms"), data=pm, headline=hl)
    else:
        sections["permits"] = _section("no_data", error=pmerr)

    co, coerr = got("cases_open")
    cc, ccerr = got("cases_closed")
    hb, hberr = got("habitability")
    cs = None
    cserr = coerr if coerr and ccerr else None
    if co or cc:
        cs = {"open": (co or {}).get("open", []), "ms": (co or {}).get("ms", 0) + (cc or {}).get("ms", 0)}
        cs.update({k: v for k, v in (cc or {}).items() if k != "ms"})
        part = [e for e in (coerr, ccerr) if e]
        if part:
            cs["partial"] = part
    if not la_city:
        sections["complaints"] = _section("skipped", error="LADBS/LAHD records cover the City of Los Angeles only")
    elif cs or hb:
        n_open = len(cs["open"]) if cs else 0
        n_hab = hb["count_3y"] if hb else 0
        hl = "%d open code case%s · %d habitability violation%s in 3 years" % (n_open, "" if n_open == 1 else "s", n_hab, "" if n_hab == 1 else "s")
        data = {"code_cases": cs, "habitability": hb}
        errs = [e for e in (coerr, ccerr, hberr) if e]
        sections["complaints"] = _section("ok" if not errs else "partial", (cs or {}).get("ms", 0) + (hb or {}).get("ms", 0), data=data, headline=hl, error="; ".join(errs) if errs else None)
    else:
        why = cserr or hberr
        if not why or why == "not run":
            why = "needs the parcel number; assessor lookup %s" % (perr or "did not return")
        sections["complaints"] = _section("no_data", error=why)

    cr, crerr = got("crime")
    if not la_city:
        sections["safety"] = _section("skipped", error="LAPD data covers the City of Los Angeles only; %s police data is not published in this form" % (g.get("place") or "local"))
    elif cr:
        hl = "%d incidents within 500 m in %s (%d violent)" % (cr["incidents"], cr["window"].replace("calendar ", ""), cr["violent"])
        sections["safety"] = _section("ok", cr.get("ms"), data=cr, headline=hl)
    else:
        sections["safety"] = _section("no_data", error=crerr)

    fl, flerr = got("flood")
    fa, faerr = got("fault")
    rf, rferr = got("red_flag")
    cf, cferr = got("calfire")
    hz = {"flood": fl, "fault": fa, "red_flag": rf, "calfire": cf}
    if any(hz.values()):
        bits = []
        if fl:
            bits.append("flood: %s" % (fl.get("level") or "none"))
        if fa:
            bits.append("fault trace within 300 m" if fa.get("ap_trace_within_300m") else "no AP fault trace within 300 m")
        if rf is not None and la_city:
            bits.append("hillside red-flag area" if rf.get("red_flag_area") else "not a red-flag hillside area")
        if cf and cf.get("severity"):
            bits.append("fire severity: %s" % cf["severity"])
        errs = [e for e in (flerr, faerr, rferr if la_city else None) if e]
        if cferr:
            hz["calfire"] = {"severity": None, "note": "CAL FIRE severity layer not reachable; hillside status from LAFD used instead", "error": cferr}
        sections["hazards"] = _section("ok" if not errs else "partial", sum((x or {}).get("ms", 0) for x in hz.values()), data=hz, headline=" · ".join(bits),
                                       error="; ".join(errs) if errs else None)
    else:
        sections["hazards"] = _section("no_data", error="; ".join(e for e in (flerr, faerr, rferr, cferr) if e))

    nz, nzerr = got("noise")
    if nz:
        bits = []
        if nz.get("freeways"):
            bits.append("freeway within 250 m: " + ", ".join(nz["freeways"][:2]))
        if nz.get("rail"):
            bits.append("rail within 250 m")
        if not bits:
            bits.append("no freeway or rail within 250 m")
        sections["noise"] = _section("ok", nz.get("ms"), data=nz, headline="%s · %s" % (nz.get("level", "").title(), "; ".join(bits)))
    else:
        sections["noise"] = _section("no_data", error=nzerr)

    pr, prerr = got("price")
    if not census_key:
        sections["price"] = _section("skipped", error="price check needs CENSUS_API_KEY")
    elif pr:
        lvl = "ZIP" if pr.get("level") == "zip" else "tract"
        if pr.get("vs_median_pct") is not None:
            hl = "asking $%s is %+d%% vs the %s median $%s" % (pr["asking"], pr["vs_median_pct"], lvl, pr["reference_rent"])
        else:
            hl = "%s median rent $%s" % (lvl, pr["median_rent"]) if pr.get("median_rent") else "median rent suppressed for this tract and ZIP"
        sections["price"] = _section("ok", pr.get("ms"), data=pr, headline=hl)
    else:
        sections["price"] = _section("no_data", error=prerr)

    sections["walkability"] = _section("skipped", error="v1.1 — OpenStreetMap public servers were not reliable enough to ship")

    if coverage == "partial":
        warnings.append("%s keeps its own permit, code-case and police data; this report shows the county and federal layers only." % (g.get("place") or "This city"))
    elif coverage == "minimal":
        warnings.append("Outside Los Angeles County: only federal layers apply.")

    return {
        "version": VERSION,
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_ms": int((time.time() - t_start) * 1000),
        "address": {
            "input": address, "matched": g.get("matched"), "lat": g["lat"], "lon": g["lon"],
            "tract": g.get("tract_geoid"), "place": g.get("place"), "la_city": la_city, "coverage": coverage,
        },
        "sections": sections,
        "warnings": warnings,
        "sources": [{"name": n, "url": u} for n, u in SOURCES],
        "disclaimer": DISCLAIMER,
    }
