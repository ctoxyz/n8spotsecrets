"""Tiny stdlib HTTP layer: JSON GET/POST with a hard timeout, ArcGIS and Socrata helpers.

Every source goes through here so timeouts and the User-Agent are one decision.
No keys live in this file; callers pass them as query params.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "n8spotsecrets/0.1 (+https://github.com/ctoxyz/n8spotsecrets)"


class SourceError(Exception):
    pass


def q(url, params):
    return url + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


# Tests set this to a function(url, params, data) -> parsed JSON to run fully offline.
TRANSPORT = None


def get_json(url, params=None, timeout=5.0, data=None, headers=None):
    """GET (or POST when data is given) and parse JSON. Raises SourceError on any failure."""
    if TRANSPORT is not None:
        t0 = time.time()
        j = TRANSPORT(url, dict(params or {}), data)
        if isinstance(j, Exception):
            raise SourceError(str(j))
        if isinstance(j, dict) and j.get("error"):
            raise SourceError("service error: %s" % str(j["error"])[:160])
        return j, int((time.time() - t0) * 1000)
    if params:
        url = q(url, params)
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=max(0.5, timeout)) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        try:
            detail = e.read()[:200].decode(errors="replace")
        except Exception:
            detail = ""
        raise SourceError("HTTP %d %s" % (e.code, detail)) from None
    except Exception as e:
        raise SourceError(str(e)[:160]) from None
    try:
        j = json.loads(body.decode(errors="replace"))
    except Exception:
        raise SourceError("not json: %r" % body[:80]) from None
    if isinstance(j, dict) and j.get("error"):
        raise SourceError("service error: %s" % str(j["error"])[:160])
    j_ms = int((time.time() - t0) * 1000)
    return j, j_ms


def get_text(url, timeout=5.0, headers=None):
    h = {"User-Agent": UA, "Accept": "text/html,*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=max(0.5, timeout)) as r:
            return r.read().decode(errors="replace")
    except Exception as e:
        raise SourceError(str(e)[:160]) from None


# ---------------------------------------------------------------- ArcGIS REST
def arcgis_query(layer_url, lat, lon, timeout=5.0, distance_m=None, out_fields="*", where="1=1",
                 count_only=False, max_records=10):
    params = {
        "geometry": "%s,%s" % (lon, lat),
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "false",
        "f": "json",
    }
    if distance_m:
        params["distance"] = str(distance_m)
        params["units"] = "esriSRUnit_Meter"
    if count_only:
        params["returnCountOnly"] = "true"
    else:
        params["resultRecordCount"] = str(max_records)
    j, ms = get_json(layer_url + "/query", params, timeout=timeout)
    if count_only:
        return int(j.get("count", 0)), ms
    feats = j.get("features") or []
    return [f.get("attributes", {}) for f in feats], ms


def arcgis_layers(service_url, timeout=5.0):
    """[(id, name)] for a MapServer / FeatureServer."""
    j, _ = get_json(service_url, {"f": "json"}, timeout=timeout)
    return [(l.get("id"), l.get("name") or "") for l in (j.get("layers") or [])]


# ---------------------------------------------------------------- Socrata SODA
def socrata(domain, dataset_id, params, timeout=5.0):
    url = "https://%s/resource/%s.json" % (domain, dataset_id)
    j, ms = get_json(url, params, timeout=timeout)
    if not isinstance(j, list):
        raise SourceError("unexpected shape")
    return j, ms


def pick(d, *patterns, default=None):
    """First value in dict d whose key matches any regex pattern (case-insensitive)."""
    import re
    for p in patterns:
        rx = re.compile(p, re.I)
        for k, v in d.items():
            if rx.search(k) and v not in (None, "", " "):
                return v
    return default
