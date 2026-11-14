"""Complaints at the parcel: LADBS code-enforcement cases (open + closed) and LAHD habitability violations.

These datasets carry no coordinates; they are joined by parcel number (AIN / APN) from the assessor.
Both the bare 10-digit and the dashed 4-3-3 form are tried, since the city is inconsistent.
"""
from datetime import datetime, timedelta, timezone

from ..http import SourceError, pick, socrata

LADBS_OPEN = "u82d-eh7z"
LADBS_CLOSED = "rken-a55j"
LAHD_VIOLATIONS = "cr8f-uc4j"
LAHD_EVICTIONS = "2u8b-eyuu"
LAHD_ENFORCEMENT = "eagk-wq48"
LAHD_COMPLAINTS = "ds2y-sb5t"
LAHD_BUYOUTS = "ci3m-f23k"
LAHD_DECLARATIONS = "vpax-89xu"
LAHD_AFFORDABLE = "mymu-zi3s"
FILE_LIMIT = 500


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


def evictions(ain, apn_dashed, timeout=4.0):
    """Eviction notices landlords filed with LAHD for this parcel. Counts only; the dataset carries no tenant names."""
    rows, ms = _by_parcel(LAHD_EVICTIONS, "apn", ain, apn_dashed, timeout, limit=500)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365)
    dates = [d for d in (_date(r.get("notice_date")) for r in rows) if d]
    kinds = {}
    for r in rows:
        k = ("%s %s" % (str(r.get("eviction_category") or "").strip(), str(r.get("notice_type") or "").strip())).strip() or "Notice"
        kinds[k] = kinds.get(k, 0) + 1
    return {
        "total": len(rows),
        "last_12_months": sum(1 for d in dates if d >= cutoff),
        "latest": max(dates).date().isoformat() if dates else None,
        "first": min(dates).date().isoformat() if dates else None,
        "kinds": [{"kind": k, "n": n} for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])[:4]],
        "ms": ms,
    }


# ── the housing department's file on the parcel (0.2.9). Every dataset here is keyed by `apn`, ten digits.

def _span(dates):
    return {"latest": max(dates).date().isoformat() if dates else None, "first": min(dates).date().isoformat() if dates else None}


def _kinds(rows, col, split=False, top=5):
    kinds = {}
    for r in rows:
        raw = str(r.get(col) or "").strip()
        for k in ([x.strip() for x in raw.split(",")] if split else [raw]):
            if k:
                kinds[k] = kinds.get(k, 0) + 1
    return [{"kind": k, "n": n} for k, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]


def _int(v):
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def enforcement(ain, apn_dashed, timeout=4.0):
    """Investigation and enforcement cases (illegal eviction, reduction of services, unit not registered...)."""
    rows, ms = _by_parcel(LAHD_ENFORCEMENT, "apn", ain, apn_dashed, timeout, limit=FILE_LIMIT)
    out = {"total": len(rows), "open": sum(1 for r in rows if not r.get("closed_date")), "kinds": _kinds(rows, "casetype", split=True),
           "capped": len(rows) >= FILE_LIMIT, "ms": ms}
    out.update(_span([d for d in (_date(r.get("case_filed_date")) for r in rows) if d]))
    return out


def complaints(ain, apn_dashed, timeout=4.0):
    """Code-enforcement complaint and inspection cases."""
    rows, ms = _by_parcel(LAHD_COMPLAINTS, "apn", ain, apn_dashed, timeout, limit=FILE_LIMIT)
    out = {"cases": len(rows), "complaints": sum(_int(r.get("totalcomplaintscount")) or 0 for r in rows),
           "open_complaints": sum(_int(r.get("opencomplaintscount")) or 0 for r in rows), "kinds": _kinds(rows, "casetype"),
           "capped": len(rows) >= FILE_LIMIT, "ms": ms}
    out.update(_span([d for d in (_date(r.get("start_date")) for r in rows) if d]))
    return out


def buyouts(ain, apn_dashed, timeout=4.0):
    """Tenant buyout ("cash for keys") agreements the landlord filed. The city takes these for rent-stabilized units only.
    Amounts and dates only; the tenant's address column is not read."""
    rows, ms = _by_parcel(LAHD_BUYOUTS, "apn", ain, apn_dashed, timeout, limit=FILE_LIMIT)
    amounts = sorted(a for a in (_int(str(r.get("compensation_amount") or "").replace("$", "")) for r in rows) if a is not None)
    out = {"total": len(rows), "median_amount": amounts[len(amounts) // 2] if amounts else None, "max_amount": amounts[-1] if amounts else None,
           "capped": len(rows) >= FILE_LIMIT, "ms": ms}
    out.update(_span([d for d in (_date(r.get("disclosure_fileddate")) for r in rows) if d]))
    return out


def declarations(ain, apn_dashed, timeout=4.0):
    """Landlord declarations of intent to evict for a no-fault reason (withdraw units, owner move-in, government order...)."""
    rows, ms = _by_parcel(LAHD_DECLARATIONS, "apn", ain, apn_dashed, timeout, limit=FILE_LIMIT)
    out = {"total": len(rows), "open": sum(1 for r in rows if not r.get("closed_date")), "kinds": _kinds(rows, "casetype"),
           "capped": len(rows) >= FILE_LIMIT, "ms": ms}
    out.update(_span([d for d in (_date(r.get("case_filed_date")) for r in rows) if d]))
    return out


def affordable(ain, apn_dashed, timeout=4.0):
    """Is this parcel on the city's list of affordable housing projects it has funded (2003 to now)?"""
    num = "".join(ch for ch in str(ain or apn_dashed or "") if ch.isdigit())
    if not num:
        raise SourceError("no parcel number")
    # unlike the look-up datasets, this list keeps the parcel number as a number: a quoted value is refused (HTTP 400)
    rows, ms = socrata("data.lacity.org", LAHD_AFFORDABLE, {"$where": "apn = %s" % num, "$limit": "20"}, timeout=timeout)
    return {"listed": bool(rows), "ms": ms, "projects": [
        {"name": str(r.get("name") or "").strip().title(), "units": _int(r.get("site_units")), "type": str(r.get("housing_type") or "").strip().title() or None,
         "stage": r.get("development_stage")} for r in rows[:5]]}


_FILE_PARTS = (("enforcement", enforcement), ("complaints", complaints), ("buyouts", buyouts), ("declarations", declarations), ("affordable", affordable))


def housing_file(ain, apn_dashed, timeout=4.0):
    """Everything above in one go, two lookups at a time so the city connections stay free for the rest of the report.
    A part that did not answer is None and named in `missing`; it is never shown as zero."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    deadline = time.time() + max(0.5, timeout - 0.3)
    t0 = time.time()

    def one(fn):
        left = deadline - time.time()
        if left < 0.3:
            raise SourceError("timed out before this lookup could start")
        return fn(ain, apn_dashed, left)

    out, missing = {}, {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [(name, ex.submit(one, fn)) for name, fn in _FILE_PARTS]
        for name, f in futs:
            try:
                out[name] = f.result()
            except SourceError as e:
                out[name] = None
                missing[name] = str(e)[:120]
    if len(missing) == len(_FILE_PARTS):
        raise SourceError("; ".join("%s: %s" % kv for kv in sorted(missing.items()))[:300])
    out["missing"] = missing
    out["ms"] = int((time.time() - t0) * 1000)
    return out
