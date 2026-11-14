"""Transit within 800 m: every stop with its agency, routes and how often it runs, plus city bikeways within 400 m.

Stops come from Esri's World Transit Stops layer (built from the agencies' own GTFS feeds, no key).
esri_trip_count is trips per week at the stop; esri_headway_mean is minutes between trips, all routes together.
Bikeways come from LADOT's City of Los Angeles Bikeways layer.
"""
import re

from ..http import arcgis_query, meters

STOPS = "https://services6.arcgis.com/4J5SL9a8ALg9oNpW/arcgis/rest/services/Transit_Stops_RC1/FeatureServer/0"
BIKEWAYS = "https://services1.arcgis.com/tp9wqSVX1AitKgjd/arcgis/rest/services/LA_City_Bikeways/FeatureServer/0"
STOP_FIELDS = ("stop_name,esri_agency_name,esri_stop_type_desc,esri_route_short_names,esri_route_count,esri_trip_count,"
               "esri_headway_mean,esri_weekend_service,esri_after_2100,stop_lat,stop_lon")


def _street(v):
    """MAIN ST -> Main St, 11TH ST -> 11th St."""
    return re.sub(r"(\d)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), str(v or "").strip().title())


def _routes(v):
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def transit(lat, lon, timeout=4.0):
    rows, ms = arcgis_query(STOPS, lat, lon, timeout=timeout, distance_m=800, out_fields=STOP_FIELDS,
                            max_records=150, order_by="esri_trip_count DESC")
    stops = {}
    for r in rows:
        d = meters(lat, lon, r.get("stop_lat"), r.get("stop_lon"))
        kind = str(r.get("esri_stop_type_desc") or "Bus").strip() or "Bus"
        key = (str(r.get("stop_name") or "").strip(), kind)
        s = stops.get(key)
        if s is None:
            s = stops[key] = {"name": key[0], "type": kind, "agency": str(r.get("esri_agency_name") or "").strip(),
                              "distance_m": d, "routes": [], "trips_week": 0, "headway_min": None,
                              "weekend": False, "late": False}
        if d is not None and (s["distance_m"] is None or d < s["distance_m"]):
            s["distance_m"] = d
        for x in _routes(r.get("esri_route_short_names")):
            if x not in s["routes"]:
                s["routes"].append(x)
        s["trips_week"] = max(s["trips_week"], int(r.get("esri_trip_count") or 0))
        hw = r.get("esri_headway_mean")
        if hw is not None and (s["headway_min"] is None or hw < s["headway_min"]):
            s["headway_min"] = round(float(hw), 1)
        s["weekend"] = s["weekend"] or bool(r.get("esri_weekend_service"))
        s["late"] = s["late"] or bool(r.get("esri_after_2100"))
    allstops = sorted(stops.values(), key=lambda s: s["distance_m"] if s["distance_m"] is not None else 9999)
    rail = [s for s in allstops if s["type"].lower() != "bus"]
    bus = [s for s in allstops if s["type"].lower() == "bus"]
    bus_routes = []
    for s in bus:
        for x in s["routes"]:
            if x not in bus_routes:
                bus_routes.append(x)
    return {
        "radius_m": 800,
        "stops": len(allstops),
        "rail": rail[:6],
        "nearest_bus": bus[0] if bus else None,
        "bus_stops_400m": sum(1 for s in bus if (s["distance_m"] or 9999) <= 400),
        "bus_routes": bus_routes[:40],
        "busiest": sorted(bus, key=lambda s: -s["trips_week"])[:3],
        "agencies": sorted({s["agency"] for s in allstops if s["agency"]}),
        "late_service": any(s["late"] for s in allstops if (s["distance_m"] or 9999) <= 400),
        "weekend_service": any(s["weekend"] for s in allstops if (s["distance_m"] or 9999) <= 400),
        "ms": ms,
    }


def bikeways(lat, lon, timeout=4.0):
    rows, ms = arcgis_query(BIKEWAYS, lat, lon, timeout=timeout, distance_m=400,
                            out_fields="ST_NAME,ST_FROM,ST_TO,Bikeway,Class,Retire", max_records=60)
    seen, out = set(), []
    for r in rows:
        if str(r.get("Retire") or "").strip():      # retired segments are flagged, live ones are blank
            continue
        key = (_street(r.get("ST_NAME")), str(r.get("Bikeway") or "").strip())
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append({"street": key[0], "kind": key[1] or "Bikeway", "protected": int(r.get("Class") or 0) == 4})
    return {"radius_m": 400, "bikeways": out[:10], "protected": any(b["protected"] for b in out), "ms": ms}
