"""The city's own list of rent-stabilized (RSO) units by parcel, shipped with the code.

The Los Angeles Housing Department publishes the list only inside a dashboard that has no supported way
to ask about one parcel, so it is read once a year (tools/n8spot_rso.py) and kept in data/rso_units.csv.
The file states its own totals, and they are checked when it is loaded: a damaged or cut-off file is an
error, never a silent "not on the list"."""
import os
import threading

from ..http import SourceError

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "rso_units.csv")
_LOCK = threading.Lock()
_LIST = None            # (meta, {parcel number: units})


def _load(path):
    meta, units = {}, {}
    try:
        with open(path, "r", encoding="ascii") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    k, sep, v = line[1:].partition(":")
                    if sep and k.strip() in ("year", "read", "parcels", "units"):
                        meta[k.strip()] = v.strip()
                    continue
                apn, _, n = line.partition(",")
                units[apn] = int(n)
        want = (int(meta["parcels"]), int(meta["units"]))
        int(meta["year"])
    except (OSError, KeyError, ValueError) as e:
        raise SourceError("the rent-stabilized list could not be read: %s" % str(e)[:100])
    if (len(units), sum(units.values())) != want:
        raise SourceError("the rent-stabilized list is damaged: %d parcels and %d units, but it says %d and %d" % (
            len(units), sum(units.values()), want[0], want[1]))
    return meta, units


def warm():
    """Read the list in the background, so the first report after a cold start does not wait for it."""
    def go():
        global _LIST
        try:
            with _LOCK:
                if _LIST is None:
                    _LIST = _load(PATH)
        except Exception:
            pass                       # registered() reports it
    threading.Thread(target=go, daemon=True).start()


def registered(ains):
    """Rent-stabilized units the city lists for these parcels (every parcel of one building).
    -> {year, read, units, parcels_listed, parcels_checked}"""
    global _LIST
    with _LOCK:
        if _LIST is None:
            _LIST = _load(PATH)
        meta, units = _LIST
    keys = sorted({str(a).strip() for a in ains or [] if str(a or "").strip()})
    hit = [units[k] for k in keys if k in units]
    return {"year": int(meta["year"]), "read": meta.get("read"), "units": sum(hit), "parcels_listed": len(hit), "parcels_checked": len(keys)}
