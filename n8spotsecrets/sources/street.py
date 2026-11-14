"""Street level: what neighbors report to the city, the air, who is unhoused nearby, and who speaks for the block.

311     MyLA311 service requests within 250 m (data.lacity.org, one dataset per year)
air     CalEnviroScreen 4.0 by census tract (OEHHA): PM2.5, diesel soot, traffic, each with a statewide percentile
count   LAHSA homeless count by community, as published by LA County Planning (2020, the newest in that layer)
council the certified Neighborhood Council for the point
airport LA County airport noise contours (CNEL) when the point sits inside one
"""
from ..http import arcgis_query, socrata

S311_BY_YEAR = [("2025", "h73f-gn57"), ("2024", "b7dx-7gc3")]
CES = "https://services1.arcgis.com/PCHfdHz4GlDNAhBb/arcgis/rest/services/CalEnviroScreen_4_0_Results_/FeatureServer/0"
HOMELESS = "https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/Homeless_Counts_2020/FeatureServer/0"
COUNCILS = "https://services1.arcgis.com/PTh9WC0Sf2WS7AAq/arcgis/rest/services/Neighborhood_Councils/FeatureServer/0"
AIRPORT_NOISE = "https://arcgis.gis.lacounty.gov/arcgis/rest/services/DRP/Open_Data/MapServer/47"
WHO_PM25 = 5.0   # WHO 2021 annual guideline, micrograms per cubic metre

DLAT, DLON = 0.00225, 0.0027     # ~250 m at LA latitude


def s311(lat, lon, timeout=4.0):
    year, dsid = S311_BY_YEAR[0]
    box = "latitude between %s and %s AND longitude between %s and %s" % (lat - DLAT, lat + DLAT, lon - DLON, lon + DLON)
    rows, ms = socrata("data.lacity.org", dsid, {
        "$select": "requesttype, count(*) as n", "$where": box, "$group": "requesttype", "$order": "n DESC", "$limit": "30",
    }, timeout=timeout)
    types = [{"type": str(r.get("requesttype") or "").strip(), "n": int(r.get("n") or 0)} for r in rows if r.get("requesttype")]
    by = {t["type"].lower(): t["n"] for t in types}

    def grab(*words):
        return sum(n for k, n in by.items() if any(w in k for w in words))
    return {
        "year": year, "radius_m": 250, "total": sum(t["n"] for t in types), "types": types[:10],
        "graffiti": grab("graffiti"), "dumping": grab("dumping", "bulky"), "encampment": grab("encampment"),
        "streetlight": grab("streetlight"), "dead_animal": grab("dead animal"), "ms": ms,
    }


def air(lat, lon, timeout=4.0):
    rows, ms = arcgis_query(CES, lat, lon, timeout=timeout, max_records=1,
                            out_fields="tract,pm,pmP,diesel,dieselP,traffic,trafficP,CIscoreP")
    if not rows:
        return {"pm25": None, "note": "no CalEnviroScreen tract at this point", "ms": ms}
    r = rows[0]

    def f(k, nd=1):
        v = r.get(k)
        return round(float(v), nd) if v not in (None, "") else None
    pm = f("pm", 1)
    return {
        "pm25": pm, "pm25_pct": f("pmP", 0), "pm25_vs_who": round(pm / WHO_PM25, 1) if pm else None,
        "diesel_pct": f("dieselP", 0), "traffic_pct": f("trafficP", 0), "burden_pct": f("CIscoreP", 0),
        "source_year": "CalEnviroScreen 4.0 (2021)", "ms": ms,
    }


def homeless(lat, lon, timeout=4.0):
    rows, ms = arcgis_query(HOMELESS, lat, lon, timeout=timeout, max_records=1,
                            out_fields="CSA_Label,Total_Unsheltered_Pop,Total_Sheltered_Pop,Total_Pop,Density_Unsheltered")
    if not rows:
        return {"community": None, "ms": ms}
    r = rows[0]
    d = r.get("Density_Unsheltered")
    return {"community": r.get("CSA_Label"), "unsheltered": r.get("Total_Unsheltered_Pop"), "sheltered": r.get("Total_Sheltered_Pop"),
            "total": r.get("Total_Pop"), "unsheltered_per_sq_mi": round(float(d)) if d not in (None, "") else None,
            "count_year": "2020", "ms": ms}


def council(lat, lon, timeout=4.0):
    rows, ms = arcgis_query(COUNCILS, lat, lon, timeout=timeout, max_records=1, out_fields="NAME,NC_Name,DWEBSITE")
    if not rows:
        return {"name": None, "ms": ms}
    r = rows[0]
    name = str(r.get("NC_Name") or r.get("NAME") or "").strip()
    return {"name": name.title() if name.isupper() else name, "website": (r.get("DWEBSITE") or "").strip() or None, "ms": ms}


def airport(lat, lon, timeout=4.0):
    rows, ms = arcgis_query(AIRPORT_NOISE, lat, lon, timeout=timeout, max_records=3, out_fields="CLASS,AIRPORT_NAME")
    if not rows:
        return {"inside_contour": False, "ms": ms}
    r = rows[0]
    return {"inside_contour": True, "contour": str(r.get("CLASS") or "").strip() or None,
            "airport": str(r.get("AIRPORT_NAME") or "").strip() or None, "ms": ms}
