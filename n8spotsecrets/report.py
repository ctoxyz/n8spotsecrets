"""Assemble one address report from the sources, inside a hard time budget.

build_report(address=..., or lat=, lon=, census_key=None, budget_s=8.0, asking=None, beds=None)

Stage 1  geocode (everything needs the point; tract/city decide coverage)
Stage 2  in parallel: parcel, permits, crime, flood, fault, red_flag, calfire, noise, price,
         and the neighborhood layers: transit, bikeways, places, parks, internet, 311, air, people
Stage 3  cases + habitability + evictions (need the parcel number from stage 2)
Any source that misses its slot is reported as status "no_data" with the reason; the report still prints.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone

from . import geocode as geo
from . import http as _http
from .http import SourceError
from .sources import amenities, assisted, building, cases, connectivity, crime, hazards, noise, people, price, rentlist, street, transit, veterans

VERSION = "0.2.12"
RSO_CUTOFF_YEAR = 1978   # LA RSO: built on or before Oct 1, 1978, 2+ units, in the City of LA

SOURCES = [
    ("US Census Bureau geocoder", "https://geocoding.geo.census.gov/"),
    ("LA County Assessor parcels (GIS)", "https://egis-lacounty.hub.arcgis.com/"),
    ("LADBS building permits (data.lacity.org pi9x-tg5x, dyxf-7hc4)", "https://data.lacity.org/"),
    ("LADBS code enforcement cases (u82d-eh7z, rken-a55j)", "https://data.lacity.org/"),
    ("LAHD habitability violations (cr8f-uc4j)", "https://data.lacity.org/"),
    ("LAPD crime data 2020-2024 (2nrs-mtv8)", "https://data.lacity.org/"),
    ("LAPD NIBRS offenses, 2024 to now (k7nn-b2ep)", "https://data.lacity.org/"),
    ("FEMA National Flood Hazard Layer", "https://hazards.fema.gov/"),
    ("California Geological Survey Alquist-Priolo fault traces", "https://gis.conservation.ca.gov/"),
    ("LAFD Red Flag restricted areas (LA GeoHub)", "https://geohub.lacity.org/"),
    ("US Census TIGERweb roads and rail", "https://tigerweb.geo.census.gov/"),
    ("US Census ACS 5-year estimates", "https://api.census.gov/"),
    ("Esri World Transit Stops (agency GTFS feeds)", "https://www.arcgis.com/home/search.html?q=World%20Transit%20Stops"),
    ("LADOT City of Los Angeles Bikeways", "https://geohub.lacity.org/"),
    ("City of LA Listing of Active Businesses (6rrh-rzua)", "https://data.lacity.org/"),
    ("LA Recreation and Parks boundaries", "https://geohub.lacity.org/"),
    ("Ookla Speedtest open data, fixed and mobile", "https://www.ookla.com/ookla-for-good/open-data"),
    ("California PUC broadband availability (LA County)", "https://egis-lacounty.hub.arcgis.com/"),
    ("MyLA311 service requests (h73f-gn57)", "https://data.lacity.org/"),
    ("CalEnviroScreen 4.0 (OEHHA)", "https://oehha.ca.gov/calenviroscreen"),
    ("LAHSA homeless count by community, 2020 (LA County Planning)", "https://egis-lacounty.hub.arcgis.com/"),
    ("Certified Neighborhood Councils (City of LA)", "https://geohub.lacity.org/"),
    ("CGS Seismic Hazard Zones: liquefaction and landslide", "https://maps.conservation.ca.gov/"),
    ("LADBS Soft-Story Compliance (dqq4-799r)", "https://data.lacity.org/"),
    ("LAHD eviction notices by property (2u8b-eyuu)", "https://data.lacity.org/"),
    ("LAHD investigation and enforcement cases by property (eagk-wq48)", "https://data.lacity.org/"),
    ("LAHD complaint and inspection cases by property (ds2y-sb5t)", "https://data.lacity.org/"),
    ("LAHD tenant buyout filings by property (ci3m-f23k)", "https://data.lacity.org/"),
    ("LAHD landlord declarations by property (vpax-89xu)", "https://data.lacity.org/"),
    ("LAHD affordable housing projects list (mymu-zi3s)", "https://data.lacity.org/"),
    ("LAHD rent-stabilized units by parcel (RSO dashboard, read once a year)", "https://housing.lacity.gov/rso"),
    ("HUD fair market rents and housing choice vouchers by tract", "https://hudgis-hud.opendata.arcgis.com/"),
    ("LA County airport noise contours", "https://egis-lacounty.hub.arcgis.com/"),
    ("HUD Multifamily Properties, Assisted and Insured: manager and inspection score", "https://hudgis-hud.opendata.arcgis.com/"),
    ("VA medical centers, clinics and Vet Centers (VHA)", "https://www.va.gov/find-locations/"),
]

DISCLAIMER = ("Public records, pulled live. Not legal advice, not a tenant screen, not a substitute for seeing the unit. "
              "Datasets lag and have gaps; a blank section means the source did not answer in time, not that nothing happened.")


# A source that failed like this may answer next time; the report is then marked incomplete and cached only briefly.
_TRANSIENT = re.compile(r"timed out|timeout|urlopen error|HTTP 5\d\d|HTTP 429|connection|reset by peer|temporar|EOF occurred", re.I)


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
    if rso.get("registered_units"):
        bits.append("%s rent-stabilized unit%s on the city's %s list" % (rso["registered_units"], "" if rso["registered_units"] == 1 else "s", rso.get("registered_year")))
    elif rso.get("likely") is True:
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


def _rso_listed(rso, ains, la_city):
    """Add what the city's own list says. The list is the city's record; the age test is only our reading of the rule."""
    if not la_city or not ains:
        return rso
    try:
        reg = rentlist.registered(ains)
    except Exception as e:                       # a damaged list is said out loud, never read as "not listed"
        return dict(rso, registered_error=str(e)[:160])
    out = dict(rso, registered_units=reg["units"], registered_year=reg["year"], registered_read=reg["read"],
               registered_parcels=reg["parcels_listed"], parcels_checked=reg["parcels_checked"])
    if reg["units"]:
        out.update(likely=True, derived=False, test_reason=rso.get("reason"),
                   reason="the city's %s list has %d rent-stabilized unit%s here" % (reg["year"], reg["units"], "" if reg["units"] == 1 else "s"))
    elif rso.get("likely") is True:
        out.update(likely=None, test_reason=rso.get("reason"),
                   reason="meets the age test, but the city's %s list has no rent-stabilized units at this property; ask LAHD" % reg["year"])
    return out


def build_report(address=None, lat=None, lon=None, census_key=None, budget_s=9.0, asking=None, beds=None):
    t_start = time.time()
    budget = _Budget(budget_s)
    _http.net_stats(reset=True)
    _http.prewarm("data.lacity.org")     # the address lookup takes about half a second; open city connections meanwhile
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
    rentlist.warm()
    pool = ThreadPoolExecutor(max_workers=34)
    futs = {
        "parcel": pool.submit(_run, building.parcel, g["lat"], g["lon"], g.get("house_no"), g.get("street"), budget.left(cap=6.0)),
        "flood": pool.submit(_run, hazards.flood, g["lat"], g["lon"], budget.left(cap=4.0)),
        "fault": pool.submit(_run, hazards.fault, g["lat"], g["lon"], budget.left(cap=4.0)),
        "calfire": pool.submit(_run, hazards.calfire, g["lat"], g["lon"], budget.left(cap=3.0)),
        "noise": pool.submit(_run, noise.noise, g["lat"], g["lon"], budget.left(cap=4.5)),
    }
    la_county = la_city or bool(g.get("la_county"))
    futs["transit"] = pool.submit(_run, transit.transit, g["lat"], g["lon"], budget.left(cap=4.5))
    futs["speeds"] = pool.submit(_run, connectivity.speeds, g["lat"], g["lon"], budget.left(cap=4.5))
    futs["air"] = pool.submit(_run, street.air, g["lat"], g["lon"], budget.left(cap=4.5))
    futs["ground"] = pool.submit(_run, hazards.ground, g["lat"], g["lon"], budget.left(cap=4.5))
    futs["vouchers"] = pool.submit(_run, people.vouchers, g.get("tract_geoid"), g["lat"], g["lon"], budget.left(cap=3.0))
    futs["assisted"] = pool.submit(_run, assisted.assisted, g["lat"], g["lon"], g.get("house_no"), g.get("street"), budget.left(cap=4.5))
    futs["va"] = pool.submit(_run, veterans.facilities, g["lat"], g["lon"], budget.left(cap=4.0))
    if census_key:
        futs["vets_tract"] = pool.submit(_run, veterans.tract, g.get("tract_geoid"), census_key, budget.left(cap=4.0))
    if la_county:
        futs["providers"] = pool.submit(_run, connectivity.providers, g["lat"], g["lon"], budget.left(cap=4.5))
        futs["homeless"] = pool.submit(_run, street.homeless, g["lat"], g["lon"], budget.left(cap=4.5))
        futs["airport"] = pool.submit(_run, street.airport, g["lat"], g["lon"], budget.left(cap=4.0))
    if la_city:
        futs["bikeways"] = pool.submit(_run, transit.bikeways, g["lat"], g["lon"], budget.left(cap=4.5))
        futs["places"] = pool.submit(_run, amenities.places, g["lat"], g["lon"], budget.left(cap=4.5))
        futs["parks"] = pool.submit(_run, amenities.parks, g["lat"], g["lon"], budget.left(cap=4.5))
        futs["s311"] = pool.submit(_run, street.s311, g["lat"], g["lon"], budget.left(cap=4.5))
        futs["council"] = pool.submit(_run, street.council, g["lat"], g["lon"], budget.left(cap=4.0))
        futs["soft_story"] = pool.submit(_run, hazards.soft_story, g.get("house_no"), g.get("street"), budget.left(cap=4.0))
        futs["crime_now"] = pool.submit(_run, crime.now, g["lat"], g["lon"], budget.left(cap=4.5))
    if census_key:
        futs["people"] = pool.submit(_run, people.people, g.get("tract"), g.get("county"), census_key, budget.left(cap=4.5))
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
                stage3["evictions"] = pool.submit(_run, cases.evictions, p.get("ain"), p.get("apn_dashed"), budget.left(cap=4.5))
                stage3["housing_file"] = pool.submit(_run, cases.housing_file, p.get("ain"), p.get("apn_dashed"), budget.left(cap=4.5))
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
    ains = None
    if p:
        p = dict(p)
        ains = p.pop("ains", None) or ([p["ain"]] if p.get("ain") else None)
    rso = _rso_listed(_rso(p, la_city), ains, la_city) if p else {"likely": None, "reason": "no parcel"}
    ev, everr = got("evictions")
    hf, hferr = got("housing_file")
    if hf and hf.get("buyouts"):
        # the city takes buyout filings for rent-stabilized units only (LAMC 151.31), so a filing here is the city's own record
        rso = dict(rso, buyout_filings=hf["buyouts"]["total"])
    if p:
        sections["building"] = _section("ok", p.get("ms"), data=dict(p, rso=rso, evictions=ev, housing_file=hf), headline=_headline_building(p, rso))
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
    if not la_city:
        sections["complaints"] = _section("skipped", error="LADBS/LAHD records cover the City of Los Angeles only")
    elif co and cc and hb:
        # all three record sets answered; anything less would print "0" for records nobody read
        cs = {"open": co.get("open", []), "ms": co.get("ms", 0) + cc.get("ms", 0)}
        cs.update({k: v for k, v in cc.items() if k != "ms"})
        n_open = len(cs["open"])
        n_hab = hb["count_3y"]
        hl = "%d open code case%s · %d habitability violation%s in 3 years" % (n_open, "" if n_open == 1 else "s", n_hab, "" if n_hab == 1 else "s")
        sections["complaints"] = _section("ok", cs["ms"] + hb.get("ms", 0), data={"code_cases": cs, "habitability": hb}, headline=hl)
    else:
        errs = [e for e in (coerr, ccerr, hberr) if e and e != "not run"]
        if errs:
            why = "the city's code-case and habitability records did not all answer (%s)" % errs[0][:90]
        else:
            why = "needs the parcel number; assessor lookup %s" % (perr or "did not return")
        sections["complaints"] = _section("no_data", error=why)

    cr, crerr = got("crime")
    if not la_city:
        sections["safety"] = _section("skipped", error="LAPD data covers the City of Los Angeles only; %s police data is not published in this form" % (g.get("place") or "local"))
    elif cr:
        cn, cnerr = got("crime_now")
        if cn:
            cr = dict(cr, now=cn)
        elif cnerr:
            cr = dict(cr, now_error=cnerr)
        hl = "%d incidents within 500 m in %s (%d violent)" % (cr["incidents"], cr["window"].replace("calendar ", ""), cr["violent"])
        if cn and cn.get("trend_pct") is not None:
            hl += " · district %s %+d%% in %s vs the same months of %s" % (cn["district"], cn["trend_pct"], cn["year"], cn["prev_year"])
        sections["safety"] = _section("ok", cr.get("ms"), data=cr, headline=hl)
    else:
        sections["safety"] = _section("no_data", error=crerr)

    fl, flerr = got("flood")
    fa, faerr = got("fault")
    rf, rferr = got("red_flag")
    cf, cferr = got("calfire")
    gr, grerr = got("ground")
    ss, sserr = got("soft_story")
    ap, aperr = got("airport")
    hz = {"flood": fl, "fault": fa, "red_flag": rf, "calfire": cf}
    if any(hz.values()) or gr:
        hz.update({"ground": gr, "soft_story": ss, "airport_noise": ap})
        bits = []
        if fl:
            bits.append("flood: %s" % (fl.get("level") or "none"))
        if fa:
            bits.append("fault trace within 300 m" if fa.get("ap_trace_within_300m") else "no AP fault trace within 300 m")
        if rf is not None and la_city:
            bits.append("hillside red-flag area" if rf.get("red_flag_area") else "not a red-flag hillside area")
        if cf and cf.get("severity"):
            bits.append("fire severity: %s" % cf["severity"])
        if gr:
            bits.append("liquefaction zone" if gr.get("liquefaction_zone") else "not a liquefaction zone")
            if gr.get("landslide_zone"):
                bits.append("landslide zone")
        if ss and ss.get("listed"):
            bits.append("soft-story retrofit %s" % ("signed off" if ss.get("completed") else "required, not signed off"))
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

    # ── the neighborhood layers
    tr, trerr = got("transit")
    bk, bkerr = got("bikeways")
    if tr:
        bits = []
        if tr.get("rail"):
            bits.append("%s %d m (%s)" % (tr["rail"][0]["name"], tr["rail"][0].get("distance_m") or 0, tr["rail"][0]["type"].lower()))
        if tr.get("nearest_bus"):
            bits.append("bus %d m · %d routes within 800 m" % (tr["nearest_bus"].get("distance_m") or 0, len(tr.get("bus_routes") or [])))
        if not bits:
            bits.append("no transit stop within 800 m")
        sections["transit"] = _section("ok", tr.get("ms"), data=dict(tr, bikeways=(bk or {}).get("bikeways"), bike_protected=(bk or {}).get("protected")),
                                       headline=" · ".join(bits))
    else:
        sections["transit"] = _section("no_data", error=trerr)

    pl, plerr = got("places")
    pk, pkerr = got("parks")
    if not la_city:
        sections["amenities"] = _section("skipped", error="the business registry and parks layer cover the City of Los Angeles only")
    elif pl:
        wi = amenities.walk_index(pl, pk, tr, bk)
        c = pl.get("counts") or {}
        hl = "walk index %d (%s) · %d grocery · %d places to eat within 800 m" % (wi["score"], wi["label"], c.get("grocery", 0), c.get("restaurants", 0) + c.get("coffee_snack", 0))
        sections["amenities"] = _section("ok" if not pkerr else "partial", pl.get("ms", 0) + (pk or {}).get("ms", 0),
                                         data=dict(pl, parks=(pk or {}).get("parks"), parks_400m=(pk or {}).get("within_400m"), walk_index=wi),
                                         headline=hl, error=pkerr or None)
    else:
        # parks alone would give a walk index of "car needed" for any address whose business lookup timed out
        sections["amenities"] = _section("no_data", error="; ".join(e for e in (plerr, pkerr) if e))

    sp, sperr = got("speeds")
    pv, pverr = got("providers")
    pe, peerr = got("people")
    if (sp and (sp.get("fixed") or sp.get("mobile"))) or pv:
        bits = []
        if sp and sp.get("fixed"):
            bits.append("home internet tests average %d down / %d up Mbps" % (sp["fixed"]["down_mbps"], sp["fixed"]["up_mbps"]))
        if sp and sp.get("mobile"):
            bits.append("mobile %d Mbps" % sp["mobile"]["down_mbps"])
        if pv and pv.get("wireline"):
            bits.append("%d wireline providers" % len(pv["wireline"]))
        errs = [e for e in (sperr, pverr) if e]
        sections["connectivity"] = _section("ok" if not errs else "partial", (sp or {}).get("ms", 0) + (pv or {}).get("ms", 0),
                                            data={"fixed": (sp or {}).get("fixed"), "mobile": (sp or {}).get("mobile"),
                                                  "wireline": (pv or {}).get("wireline"), "carriers": (pv or {}).get("mobile"),
                                                  "broadband_pct": (pe or {}).get("broadband_pct"), "no_internet_pct": (pe or {}).get("no_internet_pct")},
                                            headline=" · ".join(bits), error="; ".join(errs) if errs else None)
    else:
        sections["connectivity"] = _section("no_data", error="; ".join(e for e in (sperr, pverr) if e) or "no speed tests published for this map tile")

    s3, s3err = got("s311")
    ar, arerr = got("air")
    hm, hmerr = got("homeless")
    if s3 or ar or hm:
        bits = []
        if ar and ar.get("pm25") is not None:
            bits.append("PM2.5 %s, %sx the WHO guideline" % (ar["pm25"], ar["pm25_vs_who"]))
        if s3:
            bits.append("%d 311 requests within 250 m in %s" % (s3["total"], s3["year"]))
        if hm and hm.get("unsheltered") is not None:
            bits.append("%s unsheltered in %s (%s count)" % (hm["unsheltered"], hm.get("community"), hm.get("count_year")))
        errs = [e for e in (s3err if la_city else None, arerr) if e]
        sections["street"] = _section("ok" if not errs else "partial", sum((x or {}).get("ms", 0) for x in (s3, ar, hm)),
                                      data={"s311": s3, "air": ar, "homeless": hm}, headline=" · ".join(bits), error="; ".join(errs) if errs else None)
    else:
        sections["street"] = _section("no_data", error="; ".join(e for e in (s3err, arerr, hmerr) if e))

    nc, ncerr = got("council")
    if pe or nc:
        bits = []
        if nc and nc.get("name"):
            bits.append("%s Neighborhood Council" % nc["name"])
        if pe and pe.get("renter_pct") is not None:
            bits.append("%d%% renters" % pe["renter_pct"])
        if pe and pe.get("commute_min"):
            bits.append("%d min average commute" % pe["commute_min"])
        sections["people"] = _section("ok", (pe or {}).get("ms", 0) + (nc or {}).get("ms", 0), data=dict(pe or {}, council=nc), headline=" · ".join(bits))
    else:
        sections["people"] = _section("skipped" if not census_key else "no_data", error=peerr or ncerr or "needs CENSUS_API_KEY")

    vo, voerr = got("vouchers")
    if vo:
        bits = []
        fmr, tv = vo.get("fair_market_rent"), vo.get("tract_vouchers")
        if fmr and fmr.get("br1"):
            bits.append("HUD fair market rent, 1 bedroom: $%s" % format(int(fmr["br1"]), ","))
        if tv and tv.get("households") is not None:
            bits.append("%d voucher household%s in this tract" % (tv["households"], "" if tv["households"] == 1 else "s"))
        if hf and hf.get("affordable") and hf["affordable"]["listed"]:
            bits.append("on the city's affordable housing list")
        sections["subsidy"] = _section("ok", vo.get("ms"), data=vo, headline=" \u00b7 ".join(bits))
    else:
        sections["subsidy"] = _section("no_data", error=voerr)

    hud, huderr = got("assisted")
    if hud:
        here, near = hud.get("here") or [], hud.get("nearby") or []
        bits = []
        if here:
            bits.append("HUD-assisted: %s" % (here[0].get("name") or "this building"))
            if here[0].get("manager"):
                bits.append("managed by %s" % here[0]["manager"])
            if here[0].get("score"):
                bits.append("inspection score %s" % here[0]["score"])
        else:
            bits.append("not on HUD's list of assisted buildings")
            if near:
                bits.append("%d within %d m" % (len(near), assisted.NEAR_M))
        sections["assisted"] = _section("ok", hud.get("ms"), data=hud, headline=" \u00b7 ".join(bits))
    else:
        sections["assisted"] = _section("no_data", error=huderr)

    va, vaerr = got("va")
    vt, vterr = got("vets_tract")
    if va or vt:
        bits = []
        fac = (va or {}).get("facilities") or []
        if fac:
            d = fac[0]["distance_m"]
            bits.append("%s %s" % (fac[0]["name"], ("%d m" % d) if d < 1000 else ("%.1f km" % (d / 1000.0))))
        elif va:
            bits.append("no VA site within %d km" % va["within_km"])
        if vt:
            bits.append("%d veteran%s in this tract" % (vt["veterans"], "" if vt["veterans"] == 1 else "s"))
        vmiss = {}
        if not va:
            vmiss["facilities"] = str(vaerr)[:120]
        if not vt and census_key:
            vmiss["tract"] = str(vterr)[:120]
        sections["veterans"] = _section("ok", max((va or {}).get("ms", 0), (vt or {}).get("ms", 0)), headline=" \u00b7 ".join(bits), data={
            "facilities": fac, "count": (va or {}).get("count"), "within_km": (va or {}).get("within_km", veterans.WITHIN_KM), "tract": vt, "missing": vmiss})
    else:
        sections["veterans"] = _section("no_data", error=vaerr)

    if coverage == "partial":
        warnings.append("%s keeps its own permit, code-case and police data; this report shows the county and federal layers only." % (g.get("place") or "This city"))
    elif coverage == "minimal":
        warnings.append("Outside Los Angeles County: only federal layers apply.")

    reasons = {n: str(val)[:160] for n, (st, val) in results.items() if st != "ok" and _TRANSIENT.search(str(val))}
    if pl and pl.get("named_error"):
        reasons["places_named"] = str(pl["named_error"])[:160]
    if pm and pm.get("partial"):
        reasons["permits_2010s"] = "; ".join(str(e) for e in pm["partial"])[:160]
    if cr and cr.get("near_error"):
        reasons["crime_near"] = str(cr["near_error"])[:160]
    if hf and hf.get("missing"):
        reasons["housing_file_part"] = "; ".join("%s: %s" % kv for kv in sorted(hf["missing"].items()))[:160]
    if vo and vo.get("missing"):
        reasons["vouchers_part"] = "; ".join("%s: %s" % kv for kv in sorted(vo["missing"].items()))[:160]
    if hud and hud.get("missing"):
        reasons["assisted_part"] = "; ".join("%s: %s" % kv for kv in sorted(hud["missing"].items()))[:160]
    _parts = ("places_named", "permits_2010s", "crime_near", "housing_file_part", "vouchers_part", "assisted_part")
    incomplete = sorted(n for n in reasons if n not in _parts) + [n for n in _parts if n in reasons]

    return {
        "version": VERSION,
        "ok": True,
        "incomplete": incomplete,
        "incomplete_why": reasons,
        "net": _http.net_stats(),
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
