"""Internet at the address: measured speeds and who serves the block.

Speeds are Ookla Speedtest open data: real tests run by people in the ~600 m map tile, latest quarter published.
Providers are the California PUC broadband availability layers as hosted by LA County (wireline providers
serving businesses, and mobile carriers). No public key-free source lists home-internet plans by address,
so the report shows measured speed and provider names, and says so.
"""
from ..http import arcgis_query

OOKLA = "https://services.arcgis.com/jIL9msH9OI208GCb/arcgis/rest/services/Speedtest_by_Ookla_Global_Fixed_and_Mobile_Network_Performance_Map_Tiles/FeatureServer"
LACO = "https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services"
FIXED = LACO + "/Fixed_Business_Optimized/FeatureServer/0"
MOBILE = LACO + "/CA_Broadband_Availability_Mobile_Deployment_LACO/FeatureServer/1"


def _tile(layer, lat, lon, timeout):
    rows, ms = arcgis_query(layer, lat, lon, timeout=timeout, out_fields="CalYear,CalQuarter,AvgDown,AvgUp,AvgLat,Tests,Devices",
                            max_records=1, order_by="CalYear DESC,CalQuarter DESC")
    if not rows:
        return None, ms
    r = rows[0]
    return {"down_mbps": round(float(r.get("AvgDown") or 0)), "up_mbps": round(float(r.get("AvgUp") or 0)),
            "latency_ms": r.get("AvgLat"), "tests": r.get("Tests"), "devices": r.get("Devices"),
            "period": "%s Q%s" % (r.get("CalYear"), r.get("CalQuarter"))}, ms


def speeds(lat, lon, timeout=4.0):
    fixed, ms = _tile(OOKLA + "/0", lat, lon, timeout)
    mobile, ms2 = _tile(OOKLA + "/1", lat, lon, timeout)
    return {"fixed": fixed, "mobile": mobile, "ms": ms + ms2}


def _names(rows):
    out = []
    for r in rows:
        n = str(r.get("DBA") or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def providers(lat, lon, timeout=4.0):
    f, ms = arcgis_query(FIXED, lat, lon, timeout=timeout, out_fields="DBA", max_records=40)
    m, ms2 = arcgis_query(MOBILE, lat, lon, timeout=timeout, out_fields="DBA", max_records=40)
    return {"wireline": _names(f)[:10], "mobile": _names(m)[:8], "ms": ms + ms2}
