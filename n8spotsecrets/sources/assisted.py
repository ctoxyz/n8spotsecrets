"""HUD-assisted apartment buildings at and near the address: who manages each one and how it scored. Nationwide.

HUD's "Multifamily Properties - Assisted" layer lists buildings where the rent help is tied to the building under a
HUD contract (about 23,800 on 14 Nov 2026), with the management company and the score of the last physical
inspection. Vouchers and most tax-credit buildings are not on it. The layer also names people (contacts, HUD staff);
only the property, the management company and the inspection are ever read out of a row.

A building counts as "here" only when its address on HUD's record has the same house number and street as the
address asked about (or its map point is within 15 m): the map point alone would call the neighbor "this building"."""
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .. import http as _http
from ..http import SourceError

HUD = "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services"
# the layer as HUD publishes it, then the name the same table also goes by; the first that answers with the right columns is kept
ASSISTED = [HUD + "/MULTIFAMILY_PROPERTIES_ASSISTED/FeatureServer/0", HUD + "/HUD_MultiFamily_Assisted/FeatureServer/0"]
INSURED = HUD + "/HUD_Insured_Multifamily_Properties/FeatureServer/0"
NEAR_M = 400
_layer = None           # the assisted layer that answered


def _s(v):
    v = str(v).strip() if v is not None else ""
    return v or None


def score_int(v):
    """'89', '89c*' -> 89; None when there is no number."""
    m = re.match(r"\s*(\d{1,3})", str(v or ""))
    return int(m.group(1)) if m else None


def _date(v):
    if isinstance(v, (int, float)) and v > 1e11:
        return time.strftime("%Y-%m-%d", time.gmtime(v / 1000.0))
    return _s(v)[:10] if _s(v) else None


def same_address(house_no, street, hud_address):
    """Does HUD's address line name the same house number and street?"""
    a = str(hud_address or "").upper()
    m = re.match(r"\s*(\d+)", a)
    if not house_no or not m or m.group(1) != str(house_no).strip():
        return False
    skip = {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST", "ST", "STREET", "AVE", "AVENUE", "BLVD",
            "BOULEVARD", "DR", "DRIVE", "RD", "ROAD", "PL", "PLACE", "WAY", "LN", "LANE", "CT", "COURT", "TER", "TERRACE", "HWY", "PKWY"}
    core = [w for w in re.findall(r"[A-Z0-9]+", str(street or "").upper()) if w not in skip]
    return bool(core) and core[0] in re.findall(r"[A-Z0-9]+", a)


def _near(layer, lat, lon, meters, timeout, n=25):
    j, ms = _http.get_json(layer + "/query", {
        "geometry": "%s,%s" % (lon, lat), "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "distance": str(meters), "units": "esriSRUnit_Meter",
        "where": "1=1", "outFields": "*", "returnGeometry": "true", "outSR": "4326", "resultRecordCount": str(n), "f": "json",
    }, timeout=timeout)
    rows = []
    for f in j.get("features") or []:
        a, g = f.get("attributes") or {}, f.get("geometry") or {}
        d = int(_http.meters(lat, lon, float(g["y"]), float(g["x"]))) if g.get("x") is not None and g.get("y") is not None else None
        rows.append((d, a))
    rows.sort(key=lambda r: (r[0] is None, r[0] or 0))
    return rows, ms


def _assisted_near(lat, lon, timeout):
    global _layer
    last = None
    for layer in ([_layer] if _layer else ASSISTED):
        try:
            rows, ms = _near(layer, lat, lon, NEAR_M, timeout)
        except SourceError as e:
            last = e
            if re.search(r"timed out|connection", str(e), re.I):
                break                                   # the service is slow, not missing: do not spend the time twice
            continue
        if rows and not ("PROPERTY_NAME_TEXT" in rows[0][1] and "MGMT_AGENT_ORG_NAME" in rows[0][1]):
            last = SourceError("HUD's layer no longer has the columns this reads")
            continue
        _layer = layer
        return rows, ms
    raise last or SourceError("HUD's assisted housing layer did not answer")


def _row(d, a):
    return {"name": _s(a.get("PROPERTY_NAME_TEXT")), "manager": _s(a.get("MGMT_AGENT_ORG_NAME")),
            "address": _s(a.get("ADDRESS_LINE1_TEXT") or a.get("STD_ADDR")), "city": _s(a.get("STD_CITY")),
            "assisted_units": a.get("TOTAL_ASSISTED_UNIT_COUNT"), "total_units": a.get("TOTAL_UNIT_COUNT"),
            "category": _s(a.get("PROPERTY_CATEGORY_NAME")), "score": _s(a.get("REAC_LAST_INSPECTION_SCORE")),
            "score_date": _date(a.get("REAC_LAST_INSPCT_RELEASE_DATE")), "distance_m": d}


def portfolio(manager, timeout=4.0):
    """Every building on HUD's list under one management company name: how many, and how they scored."""
    if not _layer or not manager:
        raise SourceError("no management company to look up")
    j, ms = _http.get_json(_layer + "/query", {
        "where": "MGMT_AGENT_ORG_NAME = '%s'" % str(manager).replace("'", "''"), "outFields": "REAC_LAST_INSPECTION_SCORE",
        "returnGeometry": "false", "resultRecordCount": "2000", "f": "json"}, timeout=timeout)
    feats = j.get("features") or []
    scores = [s for s in (score_int((f.get("attributes") or {}).get("REAC_LAST_INSPECTION_SCORE")) for f in feats) if s is not None]
    return {"name": manager, "properties": len(feats), "scored": len(scores), "lowest_score": min(scores) if scores else None,
            "under_60": sum(1 for s in scores if s < 60), "capped": bool(j.get("exceededTransferLimit")), "ms": ms}


def assisted(lat, lon, house_no=None, street=None, timeout=4.5):
    """-> {here: [...], nearby: [...], insured_here: [...], manager: {...} or None, missing: {...}, ms}"""
    t0 = time.time()
    out, missing = {"here": [], "nearby": [], "insured_here": [], "manager": None}, {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        fa = ex.submit(_assisted_near, lat, lon, timeout)
        fi = ex.submit(_near, INSURED, lat, lon, NEAR_M, timeout, 25)
        try:
            rows, _ = fa.result()
            for d, a in rows:
                r = _row(d, a)
                here = (d is not None and d <= 15) or ((d is None or d <= 250) and same_address(house_no, street, r["address"]))
                out["here" if here else "nearby"].append(r)
            out["nearby"] = out["nearby"][:5]
        except SourceError as e:
            missing["assisted"] = str(e)[:120]
        try:
            rows, _ = fi.result()
            for d, a in rows:
                addr = _s(a.get("ADDRESS_LINE1_TEXT"))
                if same_address(house_no, street, addr):
                    out["insured_here"].append({"name": _s(a.get("PROPERTY_NAME_TEXT")), "address": addr, "distance_m": d})
        except SourceError as e:
            missing["insured"] = str(e)[:120]
    if "assisted" in missing:
        raise SourceError(missing["assisted"])
    if out["here"] and out["here"][0].get("manager"):
        left = timeout - (time.time() - t0)
        try:
            if left < 0.8:
                raise SourceError("no time left to count the manager's other buildings")
            out["manager"] = portfolio(out["here"][0]["manager"], timeout=left)
        except SourceError as e:
            missing["manager"] = str(e)[:120]
    out["missing"] = missing
    out["ms"] = int((time.time() - t0) * 1000)
    return out
