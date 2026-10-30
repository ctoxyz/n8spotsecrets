"""Hazards at the point: FEMA flood zone, CGS Alquist-Priolo fault traces, LAFD Red Flag hillside area,
and a best-effort CAL FIRE hazard-severity lookup (silently skipped if the service is not reachable).
"""
from ..http import SourceError, arcgis_query

FEMA_ZONES = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28"
CGS_FAULTS = "https://gis.conservation.ca.gov/server/rest/services/CGS_Earthquake_Hazard_Zones/SHP_Fault_Traces/MapServer/0"
LAFD_RED_FLAG = "https://maps.lacity.org/lahub/rest/services/Fire_Department/MapServer/7"
CALFIRE_CANDIDATES = [
    "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/FHSZ_SRA_LRA_Combined/FeatureServer/0",
    "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/FHSZ_LRA_Recommended/FeatureServer/0",
]


def flood(lat, lon, timeout=4.0):
    rows, ms = arcgis_query(FEMA_ZONES, lat, lon, timeout=timeout, out_fields="FLD_ZONE,ZONE_SUBTY,SFHA_TF", max_records=3)
    if not rows:
        return {"zone": None, "subtype": None, "sfha": False, "note": "not in a mapped flood zone", "ms": ms}
    r = rows[0]
    zone = r.get("FLD_ZONE")
    sub = (r.get("ZONE_SUBTY") or "").strip() or None
    sfha = str(r.get("SFHA_TF") or "").upper() == "T"
    if sfha:
        level = "high"
    elif sub and ("0.2 PCT" in sub.upper() or "1 PCT" in sub.upper() or "LEVEE" in sub.upper()):
        level = "moderate"
    else:
        level = "minimal"
    return {"zone": zone, "subtype": sub, "sfha": sfha, "level": level, "ms": ms}


def fault(lat, lon, timeout=4.0):
    n300, ms = arcgis_query(CGS_FAULTS, lat, lon, timeout=timeout, distance_m=300, count_only=True)
    out = {"ap_trace_within_300m": n300 > 0, "count_300m": n300, "ms": ms}
    if n300 == 0:
        n1000, ms2 = arcgis_query(CGS_FAULTS, lat, lon, timeout=timeout, distance_m=1000, count_only=True)
        out["ap_trace_within_1km"] = n1000 > 0
        out["ms"] += ms2
    return out


def red_flag(lat, lon, timeout=4.0):
    n, ms = arcgis_query(LAFD_RED_FLAG, lat, lon, timeout=timeout, count_only=True)
    return {"red_flag_area": n > 0, "ms": ms}


def calfire(lat, lon, timeout=3.0):
    last = None
    for layer in CALFIRE_CANDIDATES:
        try:
            rows, ms = arcgis_query(layer, lat, lon, timeout=timeout, max_records=2)
            if not rows:
                return {"severity": None, "note": "not in a mapped fire hazard severity zone", "ms": ms}
            r = rows[0]
            sev = r.get("HAZ_CLASS") or r.get("FHSZ_Description") or r.get("HAZ_CODE") or next(iter(r.values()), None)
            return {"severity": str(sev) if sev is not None else None, "ms": ms}
        except SourceError as e:
            last = e
    raise SourceError("CAL FIRE service unavailable: %s" % last)
