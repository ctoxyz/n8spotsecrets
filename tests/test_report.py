"""Offline tests: python -m unittest discover -s tests -v  (no network, no keys)."""
import io
import json
import os
import sys
import time
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import n8spotsecrets.http as H  # noqa: E402
from n8spotsecrets import build_report  # noqa: E402
from n8spotsecrets.cli import main as cli_main  # noqa: E402
from n8spotsecrets.sources import noise  # noqa: E402
from fakes import Transport  # noqa: E402

LA = "548 S Spring St, Los Angeles, CA 90013"
SM = "1234 4th St, Santa Monica, CA 90401"


class Base(unittest.TestCase):
    def use(self, **kw):
        self.t = Transport(**kw)
        H.TRANSPORT = self.t
        noise._LAYERS.clear()

    def tearDown(self):
        H.TRANSPORT = None
        noise._LAYERS.clear()


class FullReport(Base):
    def test_la_city_full_report(self):
        self.use()
        r = build_report(address=LA, census_key="test", asking=2400, beds=1)
        self.assertTrue(r["ok"])
        self.assertEqual(r["address"]["coverage"], "full")
        s = r["sections"]
        for k in ("building", "permits", "complaints", "safety", "hazards", "noise", "price"):
            self.assertEqual(s[k]["status"], "ok", k + ": " + str(s[k].get("error")))
        self.assertEqual(s["walkability"]["status"], "skipped")

    def test_parcel_prefers_matching_house_number_over_parking_lot(self):
        self.use()
        b = build_report(address=LA)["sections"]["building"]["data"]
        self.assertEqual(b["ain"], "5149035013")
        self.assertEqual(b["year_built"], 1923)

    def test_rso_from_use_when_unit_count_missing(self):
        self.use()
        rso = build_report(address=LA)["sections"]["building"]["data"]["rso"]
        self.assertTrue(rso["likely"])

    def test_crime_says_where_lapd_data_ends(self):
        self.use()
        c = build_report(address=LA)["sections"]["safety"]
        self.assertIn("2024", c["headline"])
        self.assertEqual(c["data"]["data_ends"], "2024-12-30")

    def test_price_vs_asking(self):
        self.use()
        p = build_report(address=LA, census_key="test", asking=2600, beds=1)["sections"]["price"]["data"]
        self.assertEqual(p["reference_rent"], 2300)
        self.assertEqual(p["vs_median_pct"], 13)

    def test_price_falls_back_to_zip_when_tract_suppressed(self):
        self.use(suppress_tract_rent=True)
        p = build_report(address=LA, census_key="test")["sections"]["price"]
        self.assertEqual(p["data"]["level"], "zip")
        self.assertIn("ZIP", p["headline"])

    def test_price_skipped_without_key(self):
        self.use()
        self.assertEqual(build_report(address=LA)["sections"]["price"]["status"], "skipped")

    def test_report_is_json_serialisable(self):
        self.use()
        json.dumps(build_report(address=LA, census_key="test"))


class Coverage(Base):
    def test_outside_la_city_is_partial(self):
        self.use()
        r = build_report(address=SM, census_key="test")
        self.assertEqual(r["address"]["coverage"], "partial")
        for k in ("permits", "complaints", "safety"):
            self.assertEqual(r["sections"][k]["status"], "skipped")
        self.assertTrue(r["warnings"])
        self.assertFalse(any("data.lacity.org" in u for u in self.t.calls), "city datasets must not be queried outside LA City")

    def test_geocoder_down_is_not_blamed_on_the_user(self):
        self.use(fail={"onelineaddress"})
        r = build_report(address=LA)
        self.assertFalse(r["ok"])
        self.assertIn("did not answer", r["error"])
        self.assertNotIn("street number", r["hint"])

    def test_not_an_address(self):
        self.use()
        r = build_report(address="studio near Venice beach")
        self.assertFalse(r["ok"])
        self.assertIn("street", r["hint"])


class Resilience(Base):
    def test_slow_sources_do_not_block_the_report(self):
        self.use(slow={"2nrs-mtv8": 6, "LACounty_Parcel": 6})
        t0 = time.time()
        r = build_report(address=LA, census_key="test", budget_s=2.0)
        self.assertLess(time.time() - t0, 4.0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["sections"]["safety"]["status"], "no_data")
        self.assertEqual(r["sections"]["hazards"]["status"], "ok")

    def test_failed_source_marks_only_its_section(self):
        self.use(fail={"NFHL"})
        s = build_report(address=LA)["sections"]
        self.assertIn(s["hazards"]["status"], ("ok", "partial"))
        self.assertEqual(s["noise"]["status"], "ok")


class Cli(Base):
    def test_cli_prints_report(self):
        self.use()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli_main([LA])
        self.assertEqual(code, 0)
        self.assertIn("548 S SPRING ST", buf.getvalue())

    def test_cli_json(self):
        self.use()
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main([LA, "--json"])
        self.assertTrue(json.loads(buf.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
