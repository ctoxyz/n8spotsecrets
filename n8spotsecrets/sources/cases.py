"""Complaints at the parcel: LADBS code-enforcement cases (open + closed) and LAHD habitability violations.

These datasets carry no coordinates; they are joined by parcel number (AIN / APN) from the assessor.
Both the bare 10-digit and the dashed 4-3-3 form are tried, since the city is inconsistent.
"""
from datetime import datetime, timedelta, timezone

from ..http import SourceError, pick, socrata

LADBS_OPEN = "u82d-eh7z"
LADBS_CLOSED = "rken-a55j"
LAHD_VIOLATIONS = "cr8f-uc4j"


def _by_parcel(dsid, col, ain, apn_dashed, timeout, limit=50):
    vals = [v for v in (ain, apn_dashed) if v]
    if not vals:
        raise SourceError("no parcel number")
    where = "%s in (%s)" % (col, ",".join("'%s'" % v for v in vals))
    return socrata("data.lacity.org", dsid, {"$where": where, "$limit": str(limit)}, timeout=timeout)


def _date(v):
    try:
        return datetime.fromisoformat(str(v)[:19])
    except Exception:
        return None


def open_cases(ain, apn_dashed, timeout=4.0):
    rows, ms = _by_parcel(LADBS_OPEN, "prclid", ain, apn_dashed, timeout)
    out = {"open": [], "ms": ms}
    for r in rows:
        d = _date(pick(r, r"adddttm", r"date"))
        out["open"].append({
            "opened": d.date().isoformat() if d else None,
            "type": pick(r, r"^csetyp", r"case_type", r"type"),
            "status": pick(r, r"^stat$", r"status"),
            "description": (str(pick(r, r"desc", r"problem", default="")) or "")[:140],
            "case": pick(r, r"^csenbr", r"case_no", r"case"),
        })
    return out


def closed_cases(ain, apn_dashed, timeout=4.0):
    rows, ms = _by_parcel(LADBS_CLOSED, "prclid", ain, apn_dashed, timeout, limit=200)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365 * 3)
    rows.sort(key=lambda r: _date(pick(r, r"resdttm", r"adddttm", r"date")) or datetime.min, reverse=True)
    out = {
        "closed_total": len(rows),
        "closed_3y": sum(1 for r in rows if (_date(pick(r, r"resdttm", r"adddttm", r"date")) or datetime.min) >= cutoff),
        "closed_recent": [],
        "ms": ms,
    }
    for r in rows[:4]:
        d = _date(pick(r, r"resdttm", r"adddttm", r"date"))
        out["closed_recent"].append({
            "closed": d.date().isoformat() if d else None,
            "type": pick(r, r"^csetyp", r"case_type", r"type"),
            "description": (str(pick(r, r"desc", r"problem", default="")) or "")[:120],
        })
    return out


def habitability(ain, apn_dashed, timeout=4.0):
    rows, ms = _by_parcel(LAHD_VIOLATIONS, "apn", ain, apn_dashed, timeout, limit=100)
    items = []
    for r in rows:
        d = _date(pick(r, r"date", r"created", r"issued"))
        items.append({
            "date": d.date().isoformat() if d else None,
            "violation": (str(pick(r, r"^violation", r"description", r"category", default="")) or "")[:140],
            "cleared": pick(r, r"cleared", r"status"),
            "_d": d,
        })
    items.sort(key=lambda x: x["_d"] or datetime.min, reverse=True)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365 * 3)
    return {
        "count_total": len(items),
        "count_3y": sum(1 for i in items if i["_d"] and i["_d"] >= cutoff),
        "open": sum(1 for i in items if str(i.get("cleared") or "").strip().lower() in ("n", "no", "false", "0", "open")),
        "recent": [{k: v for k, v in i.items() if k != "_d"} for i in items[:5]],
        "ms": ms,
    }
