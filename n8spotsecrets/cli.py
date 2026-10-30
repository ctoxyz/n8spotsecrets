"""Command line: n8spotsecrets "548 S Spring St, Los Angeles, CA"

Prints a readable report, or JSON with --json. The price section runs only when
CENSUS_API_KEY is set (free key: api.census.gov/data/key_signup.html).
"""
import argparse
import json
import os
import sys

from .report import build_report, VERSION

ORDER = [
    ("building", "Building"), ("complaints", "Complaints"), ("permits", "Permits"), ("safety", "Safety"),
    ("hazards", "Hazards"), ("noise", "Noise"), ("price", "Price check"), ("walkability", "Walkability"),
]
MARK = {"ok": "+", "partial": "~", "skipped": ".", "no_data": "-"}


def render(r):
    if not r.get("ok"):
        return "No report: %s\n%s\n" % (r.get("error"), r.get("hint") or "")
    a = r["address"]
    lines = [
        a.get("matched") or a.get("input") or "",
        "%s | coverage %s | tract %s | %.1f s" % (a.get("place"), a.get("coverage"), a.get("tract"), r.get("elapsed_ms", 0) / 1000.0),
        "",
    ]
    for key, label in ORDER:
        s = r["sections"].get(key)
        if not s:
            continue
        lines.append("%s %-12s %s" % (MARK.get(s["status"], "?"), label, s.get("headline") or s.get("error") or ""))
    for w in r.get("warnings", []):
        lines.append("! " + w)
    lines += ["", r.get("disclaimer", "")]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="n8spotsecrets", description="Ask the address, not the listing.")
    ap.add_argument("address", nargs="*", help='street address, e.g. "548 S Spring St, Los Angeles, CA"')
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--asking", type=int, help="asking rent, for the price check")
    ap.add_argument("--beds", type=int, help="bedrooms, for the price check")
    ap.add_argument("--budget", type=float, default=9.0, help="seconds before slow sources are reported as no data")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    ap.add_argument("--version", action="version", version="n8spotsecrets " + VERSION)
    args = ap.parse_args(argv)
    address = " ".join(args.address).strip() or None
    if not address and (args.lat is None or args.lon is None):
        ap.error("give an address, or --lat and --lon")
    r = build_report(address=address, lat=args.lat, lon=args.lon, census_key=os.environ.get("CENSUS_API_KEY", "").strip() or None,
                     budget_s=args.budget, asking=args.asking, beds=args.beds)
    sys.stdout.write(json.dumps(r, indent=1, default=str) + "\n" if args.json else render(r))
    return 0 if r.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
