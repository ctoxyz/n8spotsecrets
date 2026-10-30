"""Noise proxies within 250 m: freeways / primary roads and railroads, from Census TIGERweb (ArcGIS, no key).

Layer ids are discovered from the service once per process by name, so a renumbering does not break us.
"""
import math
import threading

from .. import http as _http
from ..http import SourceError

TIGER_TRANSPORT = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Transportation/MapServer"
_LAYERS = {}
_LOCK = threading.Lock()


def _layer_ids(timeout):
    with _LOCK:
        if _LAYERS:
            return _LAYERS
        j, _ = _http.get_json(TIGER_TRANSPORT, {"f": "json"}, timeout=timeout)
        layers = [(l.get("id"), l.get("name") or "") for l in (j.get("layers") or []) if not l.get("subLayerIds")]
        for lid, name in layers:
            n = name.lower()
            if "primary roads" in n and "primary" not in _LAYERS:
                _LAYERS["primary"] = lid
            elif "secondary roads" in n and "secondary" not in _LAYERS:
                _LAYERS["secondary"] = lid
            elif "rail" in n and "rail" not in _LAYERS:
                _LAYERS["rail"] = lid
        if not _LAYERS:
            raise SourceError("TIGERweb layer names not recognised: %s" % [n for _, n in layers][:8])
        return _LAYERS


def _names(rows):
    out = []
    for r in rows:
        for k in ("FULLNAME", "NAME", "RTTYP"):
            if r.get(k):
                out.append(str(r[k]).strip())
                break
    seen = set()
    uniq = []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq[:5]


def _envelope_query(layer_url, lat, lon, meters, timeout):
    """TIGERweb rejects metre buffers on a 4326 point; an envelope in degrees works everywhere."""
    dlat = meters / 111320.0
    dlon = meters / (111320.0 * max(0.2, math.cos(math.radians(lat))))
    env = {
        "geometry": '{"xmin":%s,"ymin":%s,"xmax":%s,"ymax":%s,"spatialReference":{"wkid":4326}}' % (lon - dlon, lat - dlat, lon + dlon, lat + dlat),
        "geometryType": "esriGeometryEnvelope", "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
        "where": "1=1", "outFields": "*", "returnGeometry": "false", "f": "json",
    }
    try:
        j, ms = _http.get_json(layer_url + "/query", env, timeout=timeout)
    except SourceError:
        pt = {
            "geometry": "%s,%s" % (lon, lat), "geometryType": "esriGeometryPoint", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects", "distance": str(meters), "units": "esriSRUnit_Meter",
            "where": "1=1", "outFields": "*", "returnGeometry": "false", "f": "json",
        }
        j, ms = _http.get_json(layer_url + "/query", pt, timeout=timeout)
    return [f.get("attributes", {}) for f in (j.get("features") or [])], ms


def noise(lat, lon, timeout=4.0):
    ids = _layer_ids(timeout)
    ms_total = 0
    out = {"radius_m": 250}
    if "primary" in ids:
        rows, ms = _envelope_query("%s/%s" % (TIGER_TRANSPORT, ids["primary"]), lat, lon, 250, timeout)
        ms_total += ms
        out["freeways"] = _names(rows)
    if "secondary" in ids:
        rows, ms = _envelope_query("%s/%s" % (TIGER_TRANSPORT, ids["secondary"]), lat, lon, 150, timeout)
        ms_total += ms
        out["major_roads_150m"] = _names(rows)
    if "rail" in ids:
        rows, ms = _envelope_query("%s/%s" % (TIGER_TRANSPORT, ids["rail"]), lat, lon, 250, timeout)
        ms_total += ms
        out["rail"] = _names(rows)
    score = 0
    score += 2 if out.get("freeways") else 0
    score += 1 if out.get("rail") else 0
    score += 1 if len(out.get("major_roads_150m") or []) >= 2 else 0
    out["level"] = "loud" if score >= 3 else "moderate" if score >= 1 else "quiet"
    out["ms"] = ms_total
    return out
