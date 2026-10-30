"""Offline transport for the tests.

Each response below has the shape the real service returned during the live run of
2026-10-30 (public buildings only, no request ids or IPs). Numbers are illustrative;
the tests check how the report is assembled, not what the city publishes this week.
"""
import time

LA_SPRING = {
    "matchedAddress": "548 S SPRING ST, LOS ANGELES, CA, 90013",
    "coordinates": {"x": -118.2503, "y": 34.0471},
    "geographies": {
        "Census Tracts": [{"GEOID": "06037207307", "TRACT": "207307", "COUNTY": "037"}],
        "Incorporated Places": [{"NAME": "Los Angeles city"}],
        "Counties": [{"NAME": "Los Angeles County"}],
    },
}
SANTA_MONICA = {
    "matchedAddress": "1234 4TH ST, SANTA MONICA, CA, 90401",
    "coordinates": {"x": -118.4962, "y": 34.0186},
    "geographies": {
        "Census Tracts": [{"GEOID": "06037701902", "TRACT": "701902", "COUNTY": "037"}],
        "Incorporated Places": [{"NAME": "Santa Monica city"}],
        "Counties": [{"NAME": "Los Angeles County"}],
    },
}
ACS_HDR = ["NAME", "B25064_001E", "B25031_002E", "B25031_003E", "B25031_004E", "B25031_005E", "B19013_001E", "B01003_001E"]


class Transport:
    """Callable used as n8spotsecrets.http.TRANSPORT. `slow` maps a url fragment to seconds of delay."""

    def __init__(self, slow=None, fail=None, suppress_tract_rent=False):
        self.slow = slow or {}
        self.fail = fail or set()
        self.suppress_tract_rent = suppress_tract_rent
        self.calls = []

    def __call__(self, url, p, data):
        self.calls.append(url)
        for frag, secs in self.slow.items():
            if frag in url:
                time.sleep(secs)
        for frag in self.fail:
            if frag in url:
                return RuntimeError("HTTP 503 from " + frag)
        if "onelineaddress" in url:
            a = p.get("address", "")
            if "Santa Monica" in a:
                return {"result": {"addressMatches": [SANTA_MONICA]}}
            if not any(ch.isdigit() for ch in a):
                return {"result": {"addressMatches": []}}
            return {"result": {"addressMatches": [LA_SPRING]}}
        if "LACounty_Parcel" in url:
            return {"features": [
                {"attributes": {"AIN": "5149035012", "SitusHouseNo": "525", "SitusStreet": "S SPRING ST", "UseDescription": "Parking Lots (Commercial Use Properties)"}},
                {"attributes": {"AIN": "5149035013", "SitusHouseNo": "548", "SitusStreet": "S SPRING ST", "YearBuilt1": 1923, "Units1": 0,
                                "UseDescription": "Five Or More Apartments", "SQFTmain1": 98000}},
            ]}
        if "NFHL" in url:
            return {"features": [{"attributes": {"FLD_ZONE": "X", "ZONE_SUBTY": "1 PCT DEPTH LESS THAN 1 FOOT", "SFHA_TF": "F"}}]}
        if "Fault_Traces" in url or "Fire_Department" in url:
            return {"count": 0}
        if "FHSZ" in url:
            return RuntimeError("HTTP 400")
        if url.endswith("Transportation/MapServer"):
            return {"layers": [{"id": 1, "name": "Primary Roads", "subLayerIds": [2]}, {"id": 2, "name": "Primary Roads"},
                               {"id": 3, "name": "Secondary Roads"}, {"id": 9, "name": "Railroads"}]}
        if "Transportation/MapServer/" in url:
            if url.endswith("/3/query"):
                return {"features": [{"attributes": {"FULLNAME": "S Spring St"}}, {"attributes": {"FULLNAME": "W 5th St"}}]}
            return {"features": []}
        if "/resource/2nrs-mtv8" in url:
            sel = p.get("$select", "")
            if "max(" in sel:
                return [{"newest": "2024-12-30T00:00:00.000"}]
            if "$group" in p:
                return [{"crm_cd_desc": "BURGLARY FROM VEHICLE", "n": "212"}, {"crm_cd_desc": "BATTERY - SIMPLE ASSAULT", "n": "130"}]
            if "like" in p.get("$where", ""):
                return [{"n": "227"}]
            return [{"n": "1561"}]
        if ("/resource/pi9x-tg5x" in url or "/resource/dyxf-7hc4" in url):
            if p.get("$limit") == "1":
                return [{"permit_nbr": "x", "location_1": {"type": "Point", "coordinates": [-118.25, 34.04]}}]
            assert "within_circle(location_1" in p["$where"]
            return [{"permit_nbr": "1", "primary_address": "548 S SPRING ST", "issue_date": "2025-03-27T00:00:00.000",
                     "permit_type": "Bldg-Alter/Repair", "work_desc": "INTERIOR REMODEL", "status_desc": "Permit Finaled"}]
        if "/resource/u82d-eh7z" in url:
            assert " in ('" in p["$where"]
            return []
        if "/resource/rken-a55j" in url:
            return [{"stat": "Closed", "resdttm": "2019-06-01T00:00:00", "csetyp": "Sign"}]
        if "/resource/cr8f-uc4j" in url:
            return []
        if "api.census.gov" in url:
            if "zip code" in p.get("for", ""):
                return [ACS_HDR, ["ZCTA5 90013", "1967", "1500", "1900", "2500", "3000", "40000", "12000"]]
            if self.suppress_tract_rent:
                return [ACS_HDR, ["Census Tract 2073.07", "-666666666", "-666666666", "-666666666", "-666666666", "-666666666", "61000", "4100"]]
            return [ACS_HDR, ["Census Tract", "2445", "1800", "2300", "3100", "4000", "90000", "4000"]]
        return RuntimeError("no fixture for " + url[:80])
