"""Read the City of Los Angeles housing department's public RSO inventory (its "RSO CPRA Web" dashboard) and save
one line per parcel: parcel number, rent-stabilized units. Read-only. Usage: py -3 -I -B n8spot_rso.py <output folder>

It asks the dashboard's own data service the same kind of question the dashboard asks, one council district at a time,
then checks the result against the dashboard's own totals before it writes anything."""
import csv
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib

VIEW = "https://app.powerbigov.us/view?r=eyJrIjoiM2RiMjU1ZGItNTQ3Ny00YjdjLTg2MjQtN2YzN2YxNzdlZmFjIiwidCI6ImVmNzM5MTQ4LWQ1YWEtNGVkYS1hNzk1LTQ4ZTY0NTgyM2Y2MyJ9"
KEY = "3db255db-5477-4b7c-8624-7f37f177efac"
# two parcels whose 2025 figures are known from City Planning's public map layer (read on 14 Nov 2026)
KNOWN_2025 = {2010004040: (59, "9035 TOPANGA"), 2110001030: (47, "8211 OWENSMOUTH")}
OURS = [4263009030, 5149036019, 5149024001, 5148025026, 5139008021]
SUM = 0                           # the service's code for "add up", as the dashboard itself uses it
WINDOW = 30000                    # the most rows the service returns for one question
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
STAMP = time.strftime("%Y%m%d-%H%M%S")
RAW = os.path.join(OUT, "n8spot-rso-raw-%s" % STAMP)
LOG = os.path.join(OUT, "n8spot-rso-%s.txt" % STAMP)
_n = [0]


def say(msg=""):
    print(msg, flush=True)
    with open(LOG, "a", encoding="ascii", errors="replace") as f:
        f.write(msg + "\n")


class Stop(Exception):
    pass


def http(url, body=None, tries=3):
    headers = {"X-PowerBI-ResourceKey": KEY, "ActivityId": str(uuid.uuid4()), "RequestId": str(uuid.uuid4()),
               "Accept": "application/json", "Accept-Encoding": "gzip, deflate",
               "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) n8spotsecrets (public RSO inventory, read-only)"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=UTF-8"
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=90) as r:
                raw = r.read()
                if not raw:
                    last = "an empty answer (HTTP %s)" % r.status
                    continue
                return text_of(raw, r.headers.get("Content-Encoding"))
        except urllib.error.HTTPError as e:
            last = "HTTP %d %s" % (e.code, e.read()[:200].decode(errors="replace"))
            if e.code < 500 and e.code != 429:
                break
        except Exception as e:                      # timeouts, resets
            last = str(e)[:160]
        time.sleep(1.5 * (i + 1))
    raise Stop("no answer from %s: %s" % (url.split("?")[0], last))


def text_of(raw, encoding=None):
    """The service may compress its answer and may start it with an invisible byte-order mark."""
    enc = (encoding or "").lower()
    try:
        if raw[:2] == b"\x1f\x8b" or "gzip" in enc:
            raw = gzip.decompress(raw)
        elif "deflate" in enc:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, EOFError, zlib.error) as e:
        raise Stop("could not unpack an answer marked '%s': %s; it starts with bytes %s" % (enc, e, raw[:12].hex()))
    return raw.decode("utf-8-sig", errors="replace").lstrip("\ufeff")


def parse(tag, text):
    """Keep the answer on disk, then read it as JSON; say plainly what arrived if it is not JSON."""
    path = keep(tag, text)
    try:
        return json.loads(text), path
    except ValueError:
        raise Stop("the answer to '%s' is not JSON; %d characters, starting %r (saved answer: %s)" % (tag, len(text), text[:60], path))


def keep(name, text):
    _n[0] += 1
    path = os.path.join(RAW, "%02d-%s.json" % (_n[0], name))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def api_host(page):
    m = re.search(r"resolvedClusterUri\s*=\s*'([^']+)'", page)
    if not m:
        raise Stop("the dashboard page does not name its data host")
    u = urllib.parse.urlsplit(m.group(1))
    parts = u.hostname.split(".")
    parts[0] = parts[0].replace("-redirect", "").replace("global-", "") + "-api"     # the page's own rule
    return "%s://%s" % (u.scheme, ".".join(parts))


def col(name):
    return {"Column": {"Expression": {"SourceRef": {"Source": "r"}}, "Property": name}}


def agg(name, fn):
    return {"Aggregation": {"Expression": col(name), "Function": fn}}


def measure(name):
    return {"Measure": {"Expression": {"SourceRef": {"Source": "r"}}, "Property": name}}


def lit(v):
    if v is None:
        return "null"
    return "%dL" % v if isinstance(v, int) else "'%s'" % str(v).replace("'", "''")


def where_in(name, values):
    return {"Condition": {"In": {"Expressions": [col(name)], "Values": [[{"Literal": {"Value": lit(v)}}] for v in values]}}}


class Service:
    def __init__(self, api, model, dataset, report):
        self.api, self.model, self.dataset, self.report = api, model, dataset, report

    def ask(self, tag, select, where=None):
        """select: [(label, expression)]. Returns rows as dicts keyed by label."""
        sel = [dict(e, Name=label) for label, e in select]
        q = {"Version": 2, "From": [{"Name": "r", "Entity": "RSO", "Type": 0}], "Select": sel}
        if where:
            q["Where"] = where
        cmd = {"SemanticQueryDataShapeCommand": {"Query": q, "Binding": {
            "Primary": {"Groupings": [{"Projections": list(range(len(sel)))}]},
            "DataReduction": {"DataVolume": 3, "Primary": {"Window": {"Count": WINDOW}}}, "Version": 1}, "ExecutionMetricsKind": 1}}
        body = {"version": "1.0.0", "queries": [{"Query": {"Commands": [cmd]}, "QueryId": "", "ApplicationContext": {
            "DatasetId": self.dataset, "Sources": [{"ReportId": self.report, "VisualId": ""}]}}], "cancelQueries": [], "modelId": self.model}
        resp, path = parse(tag, http(self.api + "/public/reports/querydata?synchronous=true", body))
        try:
            rows, more = decode(resp, [label for label, _ in select])
        except Stop as e:
            raise Stop("%s (saved answer: %s)" % (e, path))
        except Exception as e:
            raise Stop("could not read the answer to '%s': %r (saved answer: %s)" % (tag, e, path))
        return rows, more


def num(v):
    """The service sends whole numbers as numbers, and sometimes as text like '59' or '59L' or '59.0'."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else v
    m = re.match(r"^\s*(-?\d+)(?:\.0+)?[LDM]?\s*$", str(v))
    return int(m.group(1)) if m else v


def decode(resp, labels):
    """Rows of one answer. Each row lists only the values that changed; 'R' marks repeated ones, a null mask marks empty ones."""
    res = resp["results"][0]["result"]["data"]
    dsr = res["dsr"]
    if "DataShapes" in dsr:                       # the service's way of reporting a refused question
        raise Stop("the data service refused the question: %s" % json.dumps(dsr["DataShapes"])[:300])
    ds = dsr["DS"][0]
    by_code = {s["Value"]: s["Name"] for s in res["descriptor"]["Select"]}       # G0/M0 -> our label
    dicts = ds.get("ValueDicts") or {}
    rows, schema, prev = [], None, None
    for ph in ds.get("PH") or []:
        for key, block in ph.items():
            if not key.startswith("DM"):
                continue
            for r in block:
                if "S" in r:
                    schema, prev = r["S"], [None] * len(r["S"])
                if "C" not in r and schema is None:                # a single line of totals: {"M0": 1, "M1": 2}
                    rows.append({by_code[k]: num(v) for k, v in r.items() if k in by_code})
                    continue
                c, rep, nul, vals, ci = r.get("C") or [], r.get("R") or 0, r.get("\u00d8") or 0, [], 0
                for i, s in enumerate(schema):
                    if rep & (1 << i):
                        v = prev[i]
                    elif nul & (1 << i):
                        v = None
                    else:
                        v = c[ci]
                        ci += 1
                        if "DN" in s and isinstance(v, int):
                            v = dicts[s["DN"]][v]
                    vals.append(v)
                if ci != len(c):
                    raise Stop("a row has %d values where %d were expected" % (len(c), ci))
                prev = vals
                rows.append({by_code[s["N"]]: num(v) if by_code[s["N"]] != "address" else v for s, v in zip(schema, vals)})
    for r in rows:
        for label in labels:
            r.setdefault(label, None)
    return rows, bool(ds.get("RT")) or ds.get("IC") is False


def merge(rows):
    """One entry per parcel: a parcel with two address lines has its units added up."""
    out = {}
    for r in rows:
        if r.get("apn") is None:
            continue
        p = out.setdefault(r["apn"], {"units": 0, "rows": 0, "address": r.get("address")})
        p["units"] += r.get("units") or 0
        p["rows"] += 1
    return out


def main():
    os.makedirs(RAW, exist_ok=True)
    say("n8spot RSO list, %s (read-only: the housing department's public dashboard)" % STAMP)
    api = api_host(http(VIEW))
    say("data host: %s" % api)
    m, _ = parse("dashboard", http("%s/public/reports/%s/modelsAndExploration?preferReadOnlySession=true" % (api, KEY)))
    model, rep = m["models"][0], m["exploration"]["report"]
    svc = Service(api, model["id"], model["dbName"], rep["objectId"])
    say("dashboard: %s, data last refreshed %s" % (rep.get("displayName"), str(model.get("LastRefreshTime"))[:10]))

    say("")
    say("== 1. which years the list holds ==")
    count = [("properties", measure("PropertyCnt"))]            # the dashboard's own property count, if the service gives it
    try:
        years, _ = svc.ask("years", [("year", col("BillingYear")), ("units", agg("RSO_Units", SUM))] + count)
    except Stop as e:
        say("   (the dashboard's property count is not available this way: %s)" % str(e)[:120])
        count = []
        years, _ = svc.ask("years-units-only", [("year", col("BillingYear")), ("units", agg("RSO_Units", SUM))])
    years = sorted((y for y in years if isinstance(y.get("year"), int)), key=lambda y: y["year"])
    if not years:
        raise Stop("no years came back")
    for y in years:
        say("   %s: %s units%s" % (y["year"], y["units"], ", %s properties" % y["properties"] if count else ""))
    latest = years[-1]["year"]

    say("")
    say("== 2. two parcels with known 2025 figures (proves I read the answers correctly) ==")
    sel = [("apn", col("APN")), ("address", col("Address")), ("units", agg("RSO_Units", SUM))]
    got, _ = svc.ask("known-2025", sel, [where_in("BillingYear", [2025]), where_in("APN", sorted(KNOWN_2025))])
    seen = merge(got)
    bad = 0
    for apn, (units, addr) in sorted(KNOWN_2025.items()):
        r = seen.get(apn)
        ok = bool(r) and r["units"] == units and str(r.get("address") or "").upper().startswith(addr)
        bad += 0 if ok else 1
        say("   %s: expected %s units at %s..., got %s  %s" % (apn, units, addr, "%s units at %s" % (r["units"], r.get("address")) if r else "nothing", "OK" if ok else "DIFFERENT"))
    if bad:
        raise Stop("the known parcels did not come back as expected, so I do not trust my reading. Nothing written.")

    say("")
    say("== 3. our five test parcels, %s ==" % latest)
    got, _ = svc.ask("ours-%s" % latest, sel, [where_in("BillingYear", [latest]), where_in("APN", OURS)])
    seen = merge(got)
    for apn in OURS:
        r = seen.get(apn)
        say("   %s: %s" % (apn, "%s RSO units, %s" % (r["units"], r.get("address")) if r else "not on the list"))

    say("")
    say("== 4. the whole list for %s, one council district at a time ==" % latest)
    totals, _ = svc.ask("totals-%s" % latest, [("cd", col("CouncilDistrict")), ("units", agg("RSO_Units", SUM))] + count, [where_in("BillingYear", [latest])])
    want_units = sum(t["units"] or 0 for t in totals)
    yr = [y for y in years if y["year"] == latest][0]
    say("   dashboard total: %s units in %d districts%s" % (want_units, len(totals), ", %s properties" % yr["properties"] if count else ""))
    if want_units != yr["units"]:
        raise Stop("the district totals (%s units) do not add up to the year total (%s). Nothing written." % (want_units, yr["units"]))
    parcels, no_apn_units = {}, 0
    for t in sorted(totals, key=lambda t: (t["cd"] is None, t["cd"] or 0)):
        rows, more = svc.ask("cd-%s" % t["cd"], sel, [where_in("BillingYear", [latest]), where_in("CouncilDistrict", [t["cd"]])])
        got_units = sum(r["units"] or 0 for r in rows)
        here = merge(rows)
        ok = got_units == (t["units"] or 0) and not more
        say("   district %s: %d parcels, %d units%s  %s" % (t["cd"], len(here), got_units, " (dashboard says %s properties)" % t["properties"] if count else "",
                                                          "OK" if ok else "DOES NOT MATCH the dashboard (%s units)%s" % (t["units"], ", cut short" if more else "")))
        if not ok:
            raise Stop("district %s came back incomplete. Nothing written." % t["cd"])
        no_apn_units += sum(r["units"] or 0 for r in rows if r["apn"] is None)
        for apn, p in here.items():
            q = parcels.setdefault(apn, {"units": 0, "rows": 0, "address": p["address"]})
            q["units"] += p["units"]
            q["rows"] += p["rows"]

    say("")
    say("== 5. result ==")
    units = sum(p["units"] for p in parcels.values())
    say("   parcels: %d, units: %d, parcels with more than one address line: %d" % (len(parcels), units, sum(1 for p in parcels.values() if p["rows"] > 1)))
    if no_apn_units:
        say("   note: %d units on the dashboard sit on lines with no parcel number; they are left out" % no_apn_units)
    if not parcels or units + no_apn_units != want_units:
        raise Stop("the list (%d units) does not add up to the dashboard total (%d). Nothing written." % (units + no_apn_units, want_units))
    out = os.path.join(OUT, "n8spot-rso-%s-%s.csv" % (latest, STAMP))
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["apn", "rso_units", "address_lines", "address"])
        for apn in sorted(parcels):
            p = parcels[apn]
            w.writerow([apn, p["units"], p["rows"], p["address"] or ""])
    say("   written: %s (%d lines)" % (out, len(parcels)))
    say("")
    say("DONE. Send me the log and the .csv file.")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Stop as e:
        say("")
        say("STOP: %s" % e)
        say("The answers I received are in %s" % RAW)
        code = 2
    except Exception as e:                          # anything I did not foresee: say it in one line, write nothing
        say("")
        say("STOP: unexpected problem: %r" % (e,))
        say("The answers I received are in %s" % RAW)
        code = 2
    say("log: %s" % LOG)
    sys.exit(code)
