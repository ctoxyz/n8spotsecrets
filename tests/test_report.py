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
from n8spotsecrets.sources import assisted, noise, rentlist, reviews, veterans  # noqa: E402
from fakes import Transport  # noqa: E402

LA = "548 S Spring St, Los Angeles, CA 90013"
SM = "1234 4th St, Santa Monica, CA 90401"


class Base(unittest.TestCase):
    # a small stand-in for the city's rent-stabilized list, so the tests do not change when the real list is refreshed:
    # eleven homes of the 548 S Spring test building, and the Wilshire test parcel
    RSO_LIST = dict({"51490360%02d" % n: 1 for n in range(20, 31)}, **{"4324001010": 684})

    def use(self, **kw):
        self.t = Transport(**kw)
        H.TRANSPORT = self.t
        noise._LAYERS.clear()
        assisted._layer = None
        rentlist._LIST = ({"year": "2026", "read": "2026-11-14"}, dict(self.RSO_LIST))

    def tearDown(self):
        # never fall back to the real network: background threads from a test can outlive it
        H.TRANSPORT = lambda url, params, data: RuntimeError("offline test: network disabled")
        noise._LAYERS.clear()


class FullReport(Base):
    def test_la_city_full_report(self):
        self.use()
        r = build_report(address=LA, census_key="test", asking=2400, beds=1)
        self.assertTrue(r["ok"])
        self.assertEqual(r["address"]["coverage"], "full")
        s = r["sections"]
        for k in ("building", "permits", "complaints", "safety", "hazards", "noise", "price",
                  "transit", "amenities", "connectivity", "street", "people"):
            self.assertEqual(s[k]["status"], "ok", k + ": " + str(s[k].get("error")))

    def test_condo_building_is_one_record_from_its_assessor_address(self):
        self.use()
        b = build_report(address=LA)["sections"]["building"]["data"]
        self.assertEqual(b["match"], "address")
        self.assertEqual(b["parcel_count"], 12, "548 N Spring and 548 Springdale must not be counted")
        self.assertEqual(b["year_built"], 1914, "the store unit's year 0000 must not win")
        self.assertEqual(b["units"], 11, "the store unit is not a home")
        self.assertEqual(b["use"], "Condominium building")
        self.assertEqual(b["situs"], "548 S SPRING ST")
        self.assertEqual(b["sqft"], 300 + 1250 + 1520 + 950 + 1000 + 1240 + 1540 + 1960 + 630 + 830 + 880 + 1510)
        self.assertTrue(b["rso"]["likely"])
        self.assertFalse(any("distance" in u for u in self.t.calls))

    def test_parcel_found_when_the_census_point_is_70_m_from_the_building(self):
        self.use()
        b = build_report(address="453 S Spring St Ste 400, Los Angeles, CA 90013")["sections"]["building"]["data"]
        self.assertEqual((b["ain"], b["apn_dashed"], b["match"]), ("5149024001", "5149-024-001", "address"))
        self.assertEqual((b["year_built"], b["effective_year"], b["units"], b["sqft"], b["use"]), (1914, 1928, 0, 193816, "Office Buildings"))
        self.assertEqual(b["situs"], "453 S SPRING ST")

    def test_address_the_assessor_does_not_list_uses_the_rooftop_point(self):
        self.use()
        r = build_report(address="11740 Wilshire Blvd, Los Angeles, CA")
        b = r["sections"]["building"]["data"]
        self.assertEqual((b["match"], b["ain"], b["situs"], b["units"]), ("rooftop", "4324001010", "11728 WILSHIRE BLVD", 712))
        self.assertFalse(b["matched_house_no"])

    def test_no_parcel_rather_than_a_neighbor(self):
        self.use()
        s = build_report(address="530 S Spring St, Los Angeles, CA")["sections"]
        self.assertEqual(s["building"]["status"], "no_data")
        self.assertIn("no parcel", s["building"]["error"])
        self.assertEqual(s["hazards"]["status"], "ok")

    def test_parcel_with_no_street_number_does_not_show_zero(self):
        from n8spotsecrets.sources.building import _summarize
        b = _summarize([{"AIN": "5148025026", "SitusHouseNo": "0", "SitusStreet": " ", "UseDescription": "Parking Lots (Commercial Use Properties)",
                         "YearBuilt1": "1946", "Units1": 0, "SQFTmain1": 24250}], "rooftop", "601", 5)
        self.assertEqual(b["situs"], "address not on the assessor record")
        self.assertFalse(b["matched_house_no"])

    def test_rso_from_use_when_unit_count_missing(self):
        from n8spotsecrets.report import _rso
        self.assertTrue(_rso({"year_built": 1923, "units": 0, "use": "Five or more apartments"}, True)["likely"])
        self.assertIsNone(_rso({"year_built": 1914, "units": 0, "use": "Office Buildings"}, True)["likely"])

    def test_street_names_are_compared_without_direction_or_type(self):
        from n8spotsecrets.sources.building import _street_parts
        self.assertEqual(_street_parts("S SPRING ST."), ("S", "SPRING"))
        self.assertEqual(_street_parts("WILSHIRE BLVD"), (None, "WILSHIRE"))
        self.assertEqual(_street_parts("W AVENUE 26"), ("W", "AVENUE 26"))
        self.assertEqual(_street_parts("N ST ANDREWS PL"), ("N", "ST ANDREWS"))
        self.assertEqual(_street_parts("E 7TH ST"), ("E", "7TH"))

    def test_crime_says_where_lapd_data_ends(self):
        self.use()
        c = build_report(address=LA)["sections"]["safety"]
        # 2024 is partial in LAPD's old dataset (906 against 1,986 the year before), so 2023 is the year counted
        self.assertIn("2023", c["headline"])
        self.assertEqual(c["data"]["window"], "calendar 2023")
        self.assertEqual(c["data"]["data_ends"], "2024-12-30")
        self.assertIn("partial", c["data"]["note"])

    def test_crime_also_counts_the_block_around_the_address(self):
        self.use()
        r = build_report(address=LA)
        d = r["sections"]["safety"]["data"]
        self.assertEqual(d["near"], {"box_m": 150, "incidents": 506, "violent": 201})
        self.assertEqual((d["incidents"], d["violent"]), (1561, 227))        # the wide box is unchanged
        self.assertNotIn("crime_near", r["incomplete"])

    def test_block_count_that_did_not_answer_is_left_out_and_said_so(self):
        self.use(fail={"near-box"})
        r = build_report(address=LA)
        d = r["sections"]["safety"]["data"]
        self.assertEqual(r["sections"]["safety"]["status"], "ok")
        self.assertNotIn("near", d)                                           # never shown as zero
        self.assertEqual(d["incidents"], 1561)
        self.assertIn("crime_near", r["incomplete"])
        self.assertIn("503", r["incomplete_why"]["crime_near"])

    def test_every_address_is_counted_on_the_same_crime_year(self):
        import fakes
        real = fakes.CRIME_BY_YEAR
        # a place where 2024 looks whole (as at 800 W 1st St): the year must still be 2023
        fakes.CRIME_BY_YEAR = [dict(r, n="1900") if r["yr"].startswith("2024") else r for r in real]
        self.addCleanup(setattr, fakes, "CRIME_BY_YEAR", real)
        self.use()
        self.assertEqual(build_report(address=LA)["sections"]["safety"]["data"]["window"], "calendar 2023")

    def test_report_says_why_a_source_is_missing(self):
        self.use(fail={"CGS_Liquefaction"})
        r = build_report(address=LA)
        self.assertIn("ground", r["incomplete"])
        self.assertIn("503", r["incomplete_why"]["ground"])
        self.use()
        self.assertEqual(build_report(address=LA)["incomplete_why"], {})

    def test_crime_now_is_a_same_months_trend_by_district(self):
        self.use()
        now = build_report(address=LA)["sections"]["safety"]["data"]["now"]
        self.assertEqual(now["district"], "0153")
        self.assertEqual(now["through"], "2026-10")
        self.assertEqual(now["months_counted"], 9)            # October is still filling in, so it is left out
        self.assertEqual(now["count"], 381)
        self.assertEqual(now["prev_same_months"], 503)
        self.assertEqual(now["trend_pct"], -24)

    def test_crime_core_survives_when_the_new_dataset_is_down(self):
        self.use(fail={"k7nn-b2ep"})
        s = build_report(address=LA)["sections"]["safety"]
        self.assertEqual(s["status"], "ok")
        self.assertNotIn("now", s["data"])
        self.assertIn("now_error", s["data"])

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

    def test_census_down_falls_back_to_arcgis_and_still_reports(self):
        self.use(fail={"onelineaddress"})
        r = build_report(address="11740 Wilshire Blvd, Los Angeles, CA")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["address"]["coverage"], "full")
        self.assertEqual(r["sections"]["building"]["data"]["ain"], "4324001010")

    def test_census_slow_does_not_cost_the_report(self):
        self.use(slow={"onelineaddress": 6})
        t0 = time.time()
        r = build_report(address="11740 Wilshire Blvd, Los Angeles, CA")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertLess(time.time() - t0, 6.0)

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


class NothingFalse(Base):
    """A source that did not answer must leave a blank, never a zero, and the report must say it is incomplete."""

    def test_complete_report_says_so(self):
        self.use()
        r = build_report(address=LA, census_key="test")
        self.assertEqual(r["incomplete"], [])
        self.assertEqual(r["net"], {})          # the offline transport opens no connections

    def test_permits_since_2020_down_is_not_zero_permits(self):
        self.use(fail={"pi9x-tg5x"})
        r = build_report(address=LA)
        self.assertEqual(r["sections"]["permits"]["status"], "no_data")
        self.assertIn("since 2020", r["sections"]["permits"]["error"])
        self.assertIn("permits", r["incomplete"])

    def test_old_permits_down_keeps_the_five_year_count(self):
        self.use(fail={"dyxf-7hc4"})
        r = build_report(address=LA)
        self.assertEqual(r["sections"]["permits"]["status"], "ok")
        self.assertEqual(r["incomplete"], ["permits_2010s"])

    def test_habitability_down_is_not_zero_violations(self):
        self.use(fail={"cr8f-uc4j"})
        r = build_report(address=LA)
        c = r["sections"]["complaints"]
        self.assertEqual(c["status"], "no_data")
        self.assertNotIn("headline", c)
        self.assertIn("habitability", r["incomplete"])

    def test_business_registry_down_is_not_car_needed(self):
        self.use(fail={"6rrh-rzua"})
        r = build_report(address=LA)
        self.assertEqual(r["sections"]["amenities"]["status"], "no_data")
        self.assertNotIn("data", r["sections"]["amenities"])
        self.assertIn("places", r["incomplete"])

    def test_no_parcel_is_a_fact_not_a_gap(self):
        self.use()
        r = build_report(address="530 S Spring St, Los Angeles, CA")
        self.assertEqual(r["sections"]["building"]["status"], "no_data")
        self.assertEqual(r["incomplete"], [])

    def test_fallback_geocoder_only_takes_a_city_the_address_names(self):
        from n8spotsecrets import geocode as G
        claremont = {"address": "250 Spring St, Claremont, California, 91711", "location": {"x": -117.7166, "y": 34.0911}, "score": 100,
                     "attributes": {"Addr_type": "PointAddress", "Match_addr": "250 Spring St, Claremont, California, 91711", "AddNum": "250",
                                    "StAddr": "250 Spring St", "City": "Claremont", "Subregion": "Los Angeles County", "Postal": "91711"}}
        la = {"address": "250 N Spring St, Los Angeles, California, 90012", "location": {"x": -118.2435, "y": 34.0539}, "score": 98,
              "attributes": {"Addr_type": "PointAddress", "Match_addr": "250 N Spring St, Los Angeles, California, 90012", "AddNum": "250",
                             "StAddr": "250 N Spring St", "Postal": "90012"}}     # no City field: read from Match_addr
        H.TRANSPORT = lambda url, p, d: {"candidates": [claremont, la]}
        self.assertEqual(G._arcgis("250 Spring Street Los Angeles CA", 2)["matched"], "250 N SPRING ST, LOS ANGELES, CALIFORNIA, 90012")
        self.assertEqual(G._arcgis("250 Spring St 91711", 2)["place"], "Claremont city")
        H.TRANSPORT = lambda url, p, d: {"candidates": [claremont]}
        self.assertIsNone(G._arcgis("250 Spring Street Los Angeles CA", 2))


class Connections(Base):
    """The real transport: a connection that never finishes its handshake is cut short and opened again."""

    def setUp(self):
        import socket
        H.TRANSPORT = None
        self._connect_s = H.CONNECT_S
        H.CONNECT_S = 0.4
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]

    def tearDown(self):
        H.CONNECT_S = self._connect_s
        self.srv.close()
        super().tearDown()

    def test_stuck_handshake_is_cut_short_and_retried_inside_the_timeout(self):
        import threading
        held = []
        self.srv.listen(8)

        def hold():
            try:
                while True:
                    held.append(self.srv.accept()[0])      # accept, then say nothing: the TLS handshake hangs
            except OSError:
                pass

        threading.Thread(target=hold, daemon=True).start()
        t0 = time.time()
        with self.assertRaises(H.SourceError) as cm:
            H.get_json("https://127.0.0.1:%d/x" % self.port, timeout=1.6)
        took = time.time() - t0
        self.assertIn("connection failed after 3 tries", str(cm.exception))
        self.assertLess(took, 2.0)
        self.assertEqual(len(held), 3)
        for c in held:
            c.close()

    def test_refused_connection_gives_up_after_three_quick_tries(self):
        self.srv.close()                                    # nothing listens on the port any more
        slow = os.name == "nt"                              # Windows takes about a second to report each refusal
        t0 = time.time()
        with self.assertRaises(H.SourceError) as cm:
            H.get_json("https://127.0.0.1:%d/x" % self.port, timeout=9.0 if slow else 4.0)
        self.assertIn("connection failed after 3 tries", str(cm.exception))
        self.assertLess(time.time() - t0, 6.0 if slow else 1.5)     # it gives up on the count, well before the time limit

    def test_second_try_answers(self):
        calls = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok": 1}'

        def fake_open(req, timeout=None):
            calls.append(timeout)
            if len(calls) == 1:
                raise H._ConnectFailed("_ssl.c:994: The handshake operation timed out")
            return Resp()

        real = H._OPENER.open
        H._OPENER.open = fake_open
        try:
            j, ms = H.get_json("https://example.invalid/x", timeout=4.0)
        finally:
            H._OPENER.open = real
        self.assertEqual((j, len(calls)), ({"ok": 1}, 2))

    def test_an_http_error_is_an_answer_and_is_not_retried(self):
        import urllib.error
        calls = []

        def fake_open(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)

        real = H._OPENER.open
        H._OPENER.open = fake_open
        try:
            with self.assertRaises(H.SourceError) as cm:
                H.get_json("https://example.invalid/x", timeout=4.0)
        finally:
            H._OPENER.open = real
        self.assertIn("HTTP 503", str(cm.exception))
        self.assertEqual(len(calls), 1)


def _have_openssl():
    import shutil
    return shutil.which("openssl") is not None


@unittest.skipUnless(_have_openssl(), "needs the openssl command to make a throwaway certificate")
class SharedConnections(Base):
    """Requests to a pooled host reuse a few open connections, against a real local HTTPS server."""

    @classmethod
    def setUpClass(cls):
        import http.server
        import ssl
        import subprocess
        import tempfile
        import threading
        cls.dir = tempfile.mkdtemp()
        crt, key = os.path.join(cls.dir, "c.pem"), os.path.join(cls.dir, "k.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=127.0.0.1",
                        "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", key, "-out", crt],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.connections = []

        class Hdl(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                cls.connections.append(1)

            def log_message(self, *a):
                pass

            def do_GET(self):
                code, body = 200, b'{"ok": 1}'
                if self.path.startswith("/slow"):
                    time.sleep(1.0)
                elif self.path.startswith("/brief"):
                    time.sleep(0.05)
                elif self.path.startswith("/missing"):
                    code, body = 404, b'{"message": "no such dataset"}'
                elif self.path.startswith("/moved"):
                    code, body = 302, b""
                self.send_response(code)
                if code == 302:
                    self.send_header("Location", "/ok")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if self.path.startswith("/drop"):
                    self.close_connection = True          # hang up without saying so, like an idle timeout

        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hdl)
        cls.srv.daemon_threads = True
        sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        sctx.load_cert_chain(crt, key)
        cls.srv.socket = sctx.wrap_socket(cls.srv.socket, server_side=True)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.host = "127.0.0.1:%d" % cls.srv.server_address[1]
        cls.real_ctx = H._CTX
        H._CTX = ssl.create_default_context(cafile=crt)

    @classmethod
    def tearDownClass(cls):
        import shutil
        H._CTX = cls.real_ctx
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        import threading
        t0 = time.time()        # let handshakes left running by the test before finish, so they are not counted here
        while time.time() - t0 < 8 and any(getattr(getattr(t, "_target", None), "__name__", "") in ("attempt", "one") for t in threading.enumerate()):
            time.sleep(0.02)
        H.TRANSPORT = None
        H.POOL_HOSTS[self.host] = 3
        H._POOLS.pop(self.host, None)
        H.net_stats(reset=True)
        del self.connections[:]

    def tearDown(self):
        H.POOL_HOSTS.pop(self.host, None)
        H._POOLS.pop(self.host, None)
        super().tearDown()

    def get(self, path, timeout=4.0):
        return H.get_json("https://%s%s" % (self.host, path), timeout=timeout)[0]

    def stats(self):
        return H.net_stats().get(self.host, {})

    def test_twelve_questions_one_handshake(self):
        for i in range(12):
            self.assertEqual(self.get("/ok?i=%d" % i), {"ok": 1})
        self.assertEqual(len(self.connections), 1)
        st = self.stats()
        self.assertEqual((st["requests"], st["handshakes"], st["reused"]), (12, 1, 11))

    def test_sixteen_at_once_never_open_more_than_the_limit(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=16) as ex:
            got = list(ex.map(lambda i: self.get("/brief?i=%d" % i), range(16)))
        self.assertEqual(got, [{"ok": 1}] * 16)
        self.assertLessEqual(len(self.connections), 3)
        self.assertLessEqual(self.stats()["handshakes"], 3)

    def _handshakes(self, plan):
        """Replace the handshake step: plan(n) -> None for a normal handshake, a number of seconds to make
        handshake n slow, or ("stall", seconds) / ("refuse", 0) to make it fail."""
        import threading
        real = H._handshake
        lock, calls = threading.Lock(), [0]

        def fake(host, timeout):
            with lock:
                calls[0] += 1
                n = calls[0]
            what = plan(n)
            if isinstance(what, tuple):
                if what[0] == "refuse":
                    raise ConnectionRefusedError("refused")
                time.sleep(min(what[1], timeout))
                raise TimeoutError("_ssl.c:1: The handshake operation timed out")
            if what:
                time.sleep(what)
            return real(host, timeout)

        H._handshake = fake
        self.addCleanup(setattr, H, "_handshake", real)
        return calls

    def wait_for(self, key, n, secs=4.0):
        t0 = time.time()
        while self.stats().get(key, 0) < n and time.time() - t0 < secs:
            time.sleep(0.02)
        return self.stats().get(key, 0)

    def test_stuck_handshake_is_raced_by_a_second_one(self):
        self._handshakes(lambda n: ("stall", 2.0) if n == 1 else None)
        t0 = time.time()
        self.assertEqual(self.get("/ok"), {"ok": 1})
        took = time.time() - t0
        self.assertGreaterEqual(took, H.HEDGE_S - 0.05)
        self.assertLess(took, H.HEDGE_S + 0.6)                 # not the 1.5 s to 3 s the stuck one would cost
        st = self.stats()
        self.assertEqual((st["handshakes"], st["extra_tries"]), (1, 1))
        self.assertEqual(self.wait_for("connect_failed", 1), 1)

    def test_two_stuck_handshakes_still_get_an_answer(self):
        self._handshakes(lambda n: ("stall", 3.0) if n <= 2 else None)
        t0 = time.time()
        self.assertEqual(self.get("/ok"), {"ok": 1})
        self.assertLess(time.time() - t0, 2 * H.HEDGE_S + 0.6)
        self.assertEqual(self.stats()["extra_tries"], 2)

    def test_slow_handshake_that_finishes_late_is_kept_as_a_spare(self):
        self._handshakes(lambda n: 0.9 if n == 1 else None)
        t0 = time.time()
        self.assertEqual(self.get("/ok"), {"ok": 1})
        self.assertLess(time.time() - t0, 0.9)
        self.assertEqual(self.wait_for("spares", 1), 1)
        self.assertEqual(H._POOLS[self.host].idle_count(), 2)
        self.assertEqual(self.get("/ok"), {"ok": 1})
        self.assertEqual(self.stats()["handshakes"], 2)        # nothing new had to be opened

    def test_three_stuck_handshakes_fail_on_time_with_a_plain_reason(self):
        self._handshakes(lambda n: ("stall", 5.0))
        t0 = time.time()
        with self.assertRaises(H.SourceError) as cm:
            self.get("/ok", timeout=2.0)
        self.assertLess(time.time() - t0, 2.3)
        self.assertIn("connection failed after 3 tries", str(cm.exception))
        self.assertIn("timed out", str(cm.exception))

    def test_refused_connection_gives_up_after_three_quick_tries(self):
        calls = self._handshakes(lambda n: ("refuse", 0))
        t0 = time.time()
        with self.assertRaises(H.SourceError) as cm:
            self.get("/ok", timeout=4.0)
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(calls[0], 3)
        self.assertIn("connection failed after 3 tries: refused", str(cm.exception))

    def test_eight_connections_are_opened_side_by_side(self):
        from concurrent.futures import ThreadPoolExecutor
        H.POOL_HOSTS[self.host] = 8
        H._POOLS.pop(self.host, None)
        self._handshakes(lambda n: 0.3)
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=8) as ex:
            got = list(ex.map(lambda i: self.get("/brief?i=%d" % i), range(8)))
        self.assertEqual(got, [{"ok": 1}] * 8)
        self.assertLess(time.time() - t0, 0.3 * 8 / 2)         # not queued two at a time as in 0.2.6
        self.assertEqual(self.stats()["handshakes"], 8)

    def test_requests_use_the_connections_that_are_already_on_their_way(self):
        from concurrent.futures import ThreadPoolExecutor
        self._handshakes(lambda n: 0.3)
        self.assertEqual(H.prewarm(self.host), 3)              # one per slot
        self.assertEqual(H.prewarm(self.host), 0)              # those three are already coming
        with ThreadPoolExecutor(max_workers=3) as ex:
            got = list(ex.map(lambda i: self.get("/brief?i=%d" % i), range(3)))
        self.assertEqual(got, [{"ok": 1}] * 3)
        st = self.stats()
        self.assertEqual((st["handshakes"], st["prewarmed"], st["reused"]), (3, 3, 3))
        pool = H._POOLS[self.host]
        self.assertEqual((pool.warming, pool.claimed), (0, 0))

    def test_request_does_not_wait_long_for_a_warm_up_that_is_stuck(self):
        real_wait = H.WARM_WAIT_S
        H.WARM_WAIT_S = 0.3
        self.addCleanup(setattr, H, "WARM_WAIT_S", real_wait)
        self._handshakes(lambda n: ("stall", 3.0) if n == 1 else None)
        self.assertEqual(H.prewarm(self.host, 1), 1)
        time.sleep(0.05)
        t0 = time.time()
        self.assertEqual(self.get("/ok"), {"ok": 1})
        self.assertLess(time.time() - t0, 0.8)
        pool = H._POOLS[self.host]
        self.assertEqual(pool.claimed, 0)
        self.assertEqual(self.wait_for("prewarmed", 1), 1)     # the warm-up got there in the end, by its second try
        self.assertEqual(pool.warming, 0)

    def test_prewarm_opens_connections_before_the_questions_arrive(self):
        from concurrent.futures import ThreadPoolExecutor
        self.assertEqual(H.prewarm(self.host, 3), 3)
        t0 = time.time()
        while self.stats().get("prewarmed", 0) < 3 and time.time() - t0 < 3:
            time.sleep(0.02)
        self.assertEqual(self.stats().get("prewarmed"), 3)
        self.assertEqual(H.prewarm(self.host, 3), 0)          # already warm: nothing more to open
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(lambda i: self.get("/brief?i=%d" % i), range(3)))
        st = self.stats()
        self.assertEqual((st["handshakes"], st["reused"]), (3, 3))

    def test_prewarm_replaces_connections_that_went_stale(self):
        self.get("/ok")
        pool = H._POOLS[self.host]
        conn, since = pool.idle.get_nowait()
        pool.idle.put((conn, since - H.IDLE_MAX_S - 1))        # as if the Lambda had slept
        self.assertEqual(H.prewarm(self.host, 2), 2)
        t0 = time.time()
        while self.stats().get("prewarmed", 0) < 2 and time.time() - t0 < 3:
            time.sleep(0.02)
        self.assertEqual(pool.idle_count(), 2)

    def test_prewarm_is_a_no_op_for_other_hosts_and_offline(self):
        self.assertEqual(H.prewarm("example.invalid"), 0)
        H.TRANSPORT = lambda u, p, d: {}
        self.assertEqual(H.prewarm(self.host, 3), 0)

    def test_connection_the_server_hung_up_on_is_replaced(self):
        self.assertEqual(self.get("/drop"), {"ok": 1})
        time.sleep(0.2)
        self.assertEqual(self.get("/ok"), {"ok": 1})
        self.assertEqual(self.stats()["handshakes"], 2)

    def test_dead_connection_that_looked_alive_is_retried_once(self):
        real = H._usable
        H._usable = lambda conn, since: True              # pretend the hang-up went unnoticed
        try:
            self.assertEqual(self.get("/drop"), {"ok": 1})
            time.sleep(0.2)
            self.assertEqual(self.get("/ok"), {"ok": 1})
        finally:
            H._usable = real
        st = self.stats()
        self.assertEqual((st.get("stale_retried"), st["handshakes"]), (1, 2))

    def _raw(self):
        import socket
        host, port = self.host.split(":")
        c = H._CTX.wrap_socket(socket.create_connection((host, int(port)), timeout=4), server_hostname=host)
        c.settimeout(4)
        return c

    def _ask(self, c, path):
        c.sendall(("GET %s HTTP/1.1\r\nHost: x\r\n\r\n" % path).encode())
        got = b""
        while b'{"ok": 1}' not in got:
            more = c.recv(4096)
            if not more:
                break
            got += more
        return got

    def test_forgetting_a_tls_error_lets_the_next_read_wait(self):
        # On Python 3.13 with OpenSSL 3.5, a read that fails on a dead connection leaves its error behind for the
        # thread, and the next read that has to wait for its answer is reported as that old error ("Broken pipe").
        good, dead = self._raw(), self._raw()
        try:
            self.assertIn(b'{"ok": 1}', self._ask(good, "/ok"))
            self.assertIn(b'{"ok": 1}', self._ask(dead, "/drop"))      # the server hangs up after this answer
            time.sleep(0.2)
            try:
                dead.sendall(b"GET /ok HTTP/1.1\r\nHost: x\r\n\r\n")
                time.sleep(0.05)                                        # let the far end's reset arrive first
                dead.recv(100)
            except OSError:
                pass
            H._forget_tls_error()
            self.assertIn(b'{"ok": 1}', self._ask(good, "/slow"))      # answers after a second
        finally:
            good.close()
            dead.close()

    def test_a_failed_request_cleans_up_after_itself(self):
        real, real_forget, calls = H._usable, H._forget_tls_error, []
        H._usable = lambda conn, since: True              # pretend the hang-up went unnoticed
        H._forget_tls_error = lambda: (calls.append(1), real_forget())[1]
        try:
            self.assertEqual(self.get("/drop"), {"ok": 1})
            time.sleep(0.2)
            self.assertEqual(self.get("/ok"), {"ok": 1})
        finally:
            H._usable, H._forget_tls_error = real, real_forget
        self.assertEqual(len(calls), 1)

    def test_http_error_is_reported_and_the_connection_is_kept(self):
        with self.assertRaises(H.SourceError) as cm:
            self.get("/missing")
        self.assertIn("HTTP 404", str(cm.exception))
        self.assertIn("no such dataset", str(cm.exception))
        self.assertEqual(self.get("/ok"), {"ok": 1})
        self.assertEqual(self.stats()["handshakes"], 1)

    def test_slow_answer_times_out_on_time_and_the_next_one_works(self):
        t0 = time.time()
        with self.assertRaises(H.SourceError) as cm:
            self.get("/slow", timeout=0.6)
        self.assertIn("timed out", str(cm.exception))
        self.assertLess(time.time() - t0, 0.95)
        self.assertEqual(self.get("/ok"), {"ok": 1})

    def test_waiting_for_a_free_connection_is_bounded_by_the_timeout(self):
        import threading
        H.POOL_HOSTS[self.host] = 1
        H._POOLS.pop(self.host, None)
        t = threading.Thread(target=lambda: self.get("/slow"))
        t.start()
        time.sleep(0.2)
        t0 = time.time()
        with self.assertRaises(H.SourceError) as cm:
            self.get("/ok", timeout=0.6)
        self.assertIn("waiting for a free connection", str(cm.exception))
        self.assertLess(time.time() - t0, 0.9)
        t.join()
        self.assertEqual(self.get("/ok"), {"ok": 1})

    def test_redirect_falls_back_to_the_ordinary_path(self):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"via": "urllib"}'

        real = H._OPENER.open
        H._OPENER.open = lambda req, timeout=None: Resp()
        try:
            self.assertEqual(self.get("/moved"), {"via": "urllib"})
        finally:
            H._OPENER.open = real

    def test_other_hosts_are_not_pooled(self):
        H.POOL_HOSTS.pop(self.host)
        with self.assertRaises(H.SourceError):      # the plain path does not trust the throwaway certificate
            self.get("/ok")
        self.assertEqual(self.stats(), {})


class HousingFile(Base):
    """0.2.9: the housing department's file on the parcel. 0.2.10: Section 8 from HUD."""

    def test_housing_file_counts_every_part(self):
        self.use()
        r = build_report(address=LA)
        b = r["sections"]["building"]["data"]
        hf = b["housing_file"]
        self.assertEqual(hf["missing"], {})
        e = hf["enforcement"]
        self.assertEqual((e["total"], e["open"], e["latest"], e["first"], e["capped"]), (3, 1, "2026-03-02", "2015-10-29", False))
        self.assertEqual(e["kinds"][:2], [{"kind": "Illegal Eviction", "n": 2}, {"kind": "Reduction of Services", "n": 2}])
        c = hf["complaints"]
        self.assertEqual((c["cases"], c["complaints"], c["open_complaints"], c["latest"]), (3, 5, 2, "2026-11-02"))
        self.assertEqual(c["kinds"][0], {"kind": "Complaint", "n": 2})
        bo = hf["buyouts"]
        self.assertEqual((bo["total"], bo["median_amount"], bo["max_amount"], bo["latest"]), (3, 24050, 31000, "2025-01-20"))
        d = hf["declarations"]
        self.assertEqual((d["total"], d["open"]), (2, 0))
        self.assertIn({"kind": "Notice of Intent to Withdraw", "n": 1}, d["kinds"])
        self.assertEqual(hf["affordable"], {"listed": False, "ms": hf["affordable"]["ms"], "projects": []})
        self.assertEqual(b["rso"]["buyout_filings"], 3)
        self.assertNotIn("housing_file", r["incomplete"])
        self.assertNotIn("housing_file_part", r["incomplete"])

    def test_no_tenant_address_leaves_the_report(self):
        self.use()
        r = build_report(address=LA)
        self.assertNotIn("11728 Wilshire", json.dumps(r["sections"]["building"]["data"]["housing_file"]))

    def test_an_empty_file_is_zero_not_blank(self):
        self.use(variant={"empty-file"})
        b = build_report(address=LA)["sections"]["building"]["data"]
        hf = b["housing_file"]
        self.assertEqual((hf["enforcement"]["total"], hf["complaints"]["cases"], hf["buyouts"]["total"], hf["declarations"]["total"]), (0, 0, 0, 0))
        self.assertIsNone(hf["buyouts"]["median_amount"])
        self.assertIsNone(hf["enforcement"]["latest"])
        self.assertEqual(b["rso"]["buyout_filings"], 0)

    def test_one_part_down_is_blank_and_named(self):
        self.use(fail={"ci3m-f23k"})
        r = build_report(address=LA)
        b = r["sections"]["building"]["data"]
        self.assertIsNone(b["housing_file"]["buyouts"])
        self.assertIn("buyouts", b["housing_file"]["missing"])
        self.assertEqual(b["housing_file"]["enforcement"]["total"], 3)
        self.assertNotIn("buyout_filings", b["rso"])
        self.assertIn("housing_file_part", r["incomplete"])
        self.assertIn("buyouts", r["incomplete_why"]["housing_file_part"])

    def test_whole_file_down_is_blank_and_named(self):
        self.use(fail={"eagk-wq48", "ds2y-sb5t", "ci3m-f23k", "vpax-89xu", "mymu-zi3s"})
        r = build_report(address=LA)
        b = r["sections"]["building"]
        self.assertEqual(b["status"], "ok")
        self.assertIsNone(b["data"]["housing_file"])
        self.assertNotIn("buyout_filings", b["data"]["rso"])
        self.assertIn("housing_file", r["incomplete"])
        self.assertEqual(b["data"]["evictions"]["total"], 2)

    def test_on_the_citys_affordable_list(self):
        self.use(variant={"affordable"})
        r = build_report(address=LA)
        a = r["sections"]["building"]["data"]["housing_file"]["affordable"]
        self.assertTrue(a["listed"])
        self.assertEqual(a["projects"][0], {"name": "The Main", "units": 64, "type": "Special Needs", "stage": "Development"})
        self.assertNotIn("housing_file_part", r["incomplete"])

    def test_housing_file_uses_two_city_connections_at_most(self):
        import threading
        self.use(slow={"eagk-wq48": 0.15, "ds2y-sb5t": 0.15, "ci3m-f23k": 0.15, "vpax-89xu": 0.15, "mymu-zi3s": 0.15})
        inner, live, peak, lock = self.t, [0], [0], threading.Lock()

        def counting(url, p, data):
            mine = any(k in url for k in ("eagk-wq48", "ds2y-sb5t", "ci3m-f23k", "vpax-89xu", "mymu-zi3s"))
            if mine:
                with lock:
                    live[0] += 1
                    peak[0] = max(peak[0], live[0])
            try:
                return inner(url, p, data)
            finally:
                if mine:
                    with lock:
                        live[0] -= 1
        H.TRANSPORT = counting
        r = build_report(address=LA)
        self.assertEqual(r["sections"]["building"]["data"]["housing_file"]["missing"], {})
        self.assertEqual(peak[0], 2)

    def test_section_8_from_hud(self):
        self.use()
        r = build_report(address=LA)
        s = r["sections"]["subsidy"]
        self.assertEqual(s["status"], "ok")
        self.assertEqual(s["data"]["fair_market_rent"], {"area": "Los Angeles-Long Beach-Glendale, CA HUD Metro FMR Area", "studio": 1863,
                                                         "br1": 2085, "br2": 2601, "br3": 3298, "br4": 3672})
        self.assertEqual(s["data"]["tract_vouchers"], {"tract": "06037207307", "households": 107, "pct": 11.8})
        self.assertEqual(s["data"]["tract_vouchers"]["tract"], r["address"]["tract"])
        self.assertEqual(s["headline"], "HUD fair market rent, 1 bedroom: $2,085 \u00b7 107 voucher households in this tract")
        self.assertEqual(s["data"]["missing"], {})
        self.assertNotIn("vouchers", r["incomplete"])
        self.assertNotIn("vouchers_part", r["incomplete"])

    def test_hud_is_never_asked_by_map_point_in_la_county(self):
        self.use()
        build_report(address=LA)
        hud = [(u, p) for u, p in self.t.asked if "Fair_Market_Rents" in u or "Housing_Choice_Vouchers" in u]
        self.assertEqual(len(hud), 2)
        self.assertFalse([p for _, p in hud if "geometry" in p])

    def test_unpublished_tract_count_stays_blank(self):
        self.use(variant={"hcv-null"})
        s = build_report(address=LA)["sections"]["subsidy"]
        self.assertEqual(s["data"]["tract_vouchers"], {"tract": "06037207307", "households": None, "pct": None})
        self.assertEqual(s["headline"], "HUD fair market rent, 1 bedroom: $2,085")

    def test_tract_hud_does_not_list_is_blank_not_zero(self):
        self.use(variant={"hcv-no-row"})
        r = build_report(address=LA)
        self.assertIsNone(r["sections"]["subsidy"]["data"]["tract_vouchers"])
        self.assertNotIn("vouchers_part", r["incomplete"])

    def test_one_hud_layer_down(self):
        self.use(fail={"Housing_Choice_Vouchers_by_Tract"})
        r = build_report(address=LA)
        s = r["sections"]["subsidy"]
        self.assertEqual(s["status"], "ok")
        self.assertNotIn("tract_vouchers", s["data"])
        self.assertEqual(s["data"]["fair_market_rent"]["br2"], 2601)
        self.assertIn("vouchers_part", r["incomplete"])

    def test_hud_down_leaves_a_blank(self):
        self.use(fail={"Fair_Market_Rents", "Housing_Choice_Vouchers_by_Tract"})
        r = build_report(address=LA)
        self.assertEqual(r["sections"]["subsidy"]["status"], "no_data")
        self.assertIn("vouchers", r["incomplete"])
        self.assertEqual(r["sections"]["building"]["data"]["housing_file"]["enforcement"]["total"], 3)

    def test_affordable_building_named_in_section_8(self):
        self.use(variant={"affordable"})
        self.assertIn("on the city's affordable housing list", build_report(address=LA)["sections"]["subsidy"]["headline"])

    def test_sources_list_names_the_new_ones(self):
        self.use()
        names = " ".join(s["name"] if isinstance(s, dict) else s[0] for s in build_report(address=LA)["sources"])
        for dsid in ("eagk-wq48", "ds2y-sb5t", "ci3m-f23k", "vpax-89xu", "mymu-zi3s", "HUD fair market rents"):
            self.assertIn(dsid, names)


class RentStabilizedList(Base):
    """0.2.11: the city's own list of rent-stabilized units, by parcel."""

    def test_one_parcel_on_the_list(self):
        self.use()
        b = build_report(address="11740 Wilshire Blvd, Los Angeles, CA")["sections"]["building"]
        rso = b["data"]["rso"]
        self.assertEqual((rso["likely"], rso["registered_units"], rso["registered_year"], rso["registered_parcels"], rso["parcels_checked"]), (True, 684, 2026, 1, 1))
        self.assertIn("684 rent-stabilized units", rso["reason"])
        self.assertIn("684 rent-stabilized units on the city's 2026 list", b["headline"])
        self.assertNotIn("ains", b["data"])

    def test_condo_building_adds_up_every_parcel(self):
        self.use()
        rso = build_report(address=LA)["sections"]["building"]["data"]["rso"]
        self.assertEqual((rso["registered_units"], rso["registered_parcels"], rso["parcels_checked"]), (11, 11, 12))
        self.assertTrue(rso["likely"])

    def test_meets_the_age_test_but_is_not_on_the_list(self):
        self.use()
        rentlist._LIST = ({"year": "2026", "read": "2026-11-14"}, {})
        b = build_report(address=LA)["sections"]["building"]
        rso = b["data"]["rso"]
        self.assertIsNone(rso["likely"])
        self.assertEqual(rso["registered_units"], 0)
        self.assertIn("no rent-stabilized units at this property", rso["reason"])
        self.assertIn("built 1914", rso["test_reason"])
        self.assertNotIn("rent-stabilized", b["headline"])

    def test_outside_the_city_the_list_is_not_asked(self):
        from n8spotsecrets.report import _rso_listed
        self.use()
        before = {"likely": None, "reason": "RSO is a City of Los Angeles ordinance; this address is outside the city"}
        self.assertEqual(_rso_listed(before, ["4324001010"], False), before)
        self.assertEqual(_rso_listed(before, None, True), before)

    def test_a_damaged_list_is_said_out_loud(self):
        import os
        import tempfile
        self.use()
        d = tempfile.mkdtemp()
        bad = os.path.join(d, "rso_units.csv")
        with open(bad, "w", encoding="ascii") as f:
            f.write("# year: 2026\n# read: 2026-11-14\n# parcels: 3\n# units: 9\n4324001010,684\n")      # says 3 parcels, holds 1
        real = rentlist.PATH
        rentlist.PATH, rentlist._LIST = bad, None
        try:
            rso = build_report(address="11740 Wilshire Blvd, Los Angeles, CA")["sections"]["building"]["data"]["rso"]
        finally:
            rentlist.PATH, rentlist._LIST = real, None
        self.assertIn("damaged", rso["registered_error"])
        self.assertNotIn("registered_units", rso)
        self.assertTrue(rso["likely"])                    # the age test still stands on its own

    def test_the_shipped_list_matches_its_own_totals(self):
        meta, units = rentlist._load(rentlist.PATH)
        self.assertEqual((len(units), sum(units.values())), (int(meta["parcels"]), int(meta["units"])))
        self.assertGreater(len(units), 100000)
        self.assertTrue(all(len(k) == 10 and k.isdigit() and v > 0 for k, v in units.items()))


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


class Veterans(Base):
    def test_nearest_va_sites_and_the_tract_count(self):
        self.use()
        r = build_report(address=LA, census_key="test")
        v = r["sections"]["veterans"]
        self.assertEqual(v["status"], "ok")
        names = [f["name"] for f in v["data"]["facilities"]]
        self.assertEqual(names, ["Los Angeles VA Clinic", "East Los Angeles VA Clinic", "West Los Angeles Vet Center"])
        self.assertEqual(v["data"]["facilities"][0]["city"], "Los Angeles")
        self.assertTrue(1000 < v["data"]["facilities"][0]["distance_m"] < 1300)
        self.assertEqual(v["data"]["tract"], {"veterans": 62, "adults": 3150, "pct": 2.0, "acs_year": "2023", "ms": v["data"]["tract"]["ms"]})
        self.assertEqual(v["data"]["missing"], {})
        self.assertTrue(v["headline"].startswith("Los Angeles VA Clinic 1.") and v["headline"].endswith("km \u00b7 62 veterans in this tract"), v["headline"])
        asked = [p for u, p in self.t.asked if "Veterans_Health" in u][0]
        self.assertEqual(asked["outFields"], "STA_NAME,S_ADD1,S_CITY,S_STATE,S_ZIP")       # never the staff contact columns

    def test_without_a_census_key_the_tract_is_left_out_not_failed(self):
        self.use()
        r = build_report(address=LA)
        v = r["sections"]["veterans"]["data"]
        self.assertIsNone(v["tract"])
        self.assertEqual(v["missing"], {})
        self.assertFalse([u for u in self.t.calls if "api.census.gov" in u])
        self.assertNotIn("vets_tract", r["incomplete"])

    def test_a_census_failure_is_named_and_the_sites_still_show(self):
        self.use(variant={"no-vets"})
        r = build_report(address=LA, census_key="test")
        v = r["sections"]["veterans"]
        self.assertEqual(v["status"], "ok")
        self.assertEqual(len(v["data"]["facilities"]), 3)
        self.assertIn("HTTP 503", v["data"]["missing"]["tract"])
        self.assertIn("vets_tract", r["incomplete"])

    def test_no_va_site_in_range_is_said(self):
        self.use(variant={"no-va"})
        v = build_report(address=LA)["sections"]["veterans"]
        self.assertEqual(v["data"]["facilities"], [])
        self.assertEqual(v["headline"], "no VA site within 40 km")

    def test_tract_number_must_be_whole(self):
        self.use()
        with self.assertRaises(H.SourceError):
            veterans.tract("0603720", "test")
        with self.assertRaises(H.SourceError):
            veterans.tract("06037207307", "")


class AssistedHousing(Base):
    def test_a_neighbor_is_nearby_not_this_building(self):
        self.use()
        r = build_report(address=LA)
        a = r["sections"]["assisted"]
        self.assertEqual(a["status"], "ok")
        self.assertEqual(a["data"]["here"], [])
        n = a["data"]["nearby"][0]
        self.assertEqual((n["name"], n["manager"], n["score"], n["assisted_units"], n["city"], n["score_date"]),
                         ("TELACU PLAZA", "TELACU Residential Management, Inc.", "96", 39, "Los Angeles", "2026-02-24"))
        self.assertTrue(30 < n["distance_m"] < 80)
        self.assertIsNone(a["data"]["manager"])
        self.assertEqual(a["headline"], "not on HUD's list of assisted buildings \u00b7 1 within 400 m")
        self.assertNotIn("assisted_part", r["incomplete"])

    def test_the_building_itself_with_its_manager_and_the_manager_s_other_buildings(self):
        self.use(variant={"hud-here"})
        r = build_report(address=LA)
        a = r["sections"]["assisted"]["data"]
        self.assertEqual([h["name"] for h in a["here"]], ["SPRING ARMS"])
        self.assertEqual([h["name"] for h in a["nearby"]], ["TELACU PLAZA"])
        self.assertEqual(a["manager"]["name"], "O'Neil & Sons Management")
        self.assertEqual((a["manager"]["properties"], a["manager"]["scored"], a["manager"]["lowest_score"], a["manager"]["under_60"]), (5, 4, 58, 1))
        where = [p["where"] for u, p in self.t.asked if "MGMT_AGENT_ORG_NAME = " in p.get("where", "")]
        self.assertEqual(where, ["MGMT_AGENT_ORG_NAME = 'O''Neil & Sons Management'"])
        self.assertEqual(r["sections"]["assisted"]["headline"], "HUD-assisted: SPRING ARMS \u00b7 managed by O'Neil & Sons Management \u00b7 inspection score 89c*")

    def test_names_of_people_on_hud_s_rows_never_reach_the_report(self):
        self.use(variant={"hud-here", "insured"})
        text = json.dumps(build_report(address=LA, census_key="test"))
        for private in ("TEST PERSON", "TEST CONTACT", "contact@example.org", "PROJECT_MANAGER", "MGMT_CONTACT"):
            self.assertNotIn(private, text)

    def test_hud_insures_the_mortgage_at_this_address(self):
        self.use(variant={"insured"})
        a = build_report(address=LA)["sections"]["assisted"]["data"]
        self.assertEqual([(i["name"], i["address"]) for i in a["insured_here"]], [("SPRING LOFTS", "548 S Spring Street")])

    def test_the_layer_under_its_other_name_is_used_when_the_first_is_gone(self):
        self.use(variant={"hud-renamed"})
        a = build_report(address=LA)["sections"]["assisted"]
        self.assertEqual(a["status"], "ok")
        self.assertEqual(a["data"]["nearby"][0]["name"], "TELACU PLAZA")
        self.assertTrue(assisted._layer.endswith("/HUD_MultiFamily_Assisted/FeatureServer/0"))

    def test_nothing_nearby(self):
        self.use(variant={"hud-none"})
        a = build_report(address=LA)["sections"]["assisted"]
        self.assertEqual((a["data"]["here"], a["data"]["nearby"]), ([], []))
        self.assertEqual(a["headline"], "not on HUD's list of assisted buildings")

    def test_hud_down_is_said_and_the_report_is_marked_incomplete(self):
        self.use(fail={"MULTIFAMILY_PROPERTIES_ASSISTED", "HUD_MultiFamily_Assisted"})
        r = build_report(address=LA)
        self.assertEqual(r["sections"]["assisted"]["status"], "no_data")
        self.assertIn("assisted", r["incomplete"])

    def test_same_address(self):
        same = assisted.same_address
        self.assertTrue(same("548", "S SPRING ST", "548 South Spring St"))
        self.assertTrue(same("1000", "S HOPE ST", "1000 S Hope Street"))
        self.assertFalse(same("1000", "S HOPE ST", "1033 S Hope Street"))
        self.assertFalse(same("548", "S SPRING ST", "548 S Main St"))
        self.assertFalse(same(None, "S SPRING ST", "548 S Spring St"))
        self.assertFalse(same("548", "", "548 S Spring St"))
        self.assertEqual([assisted.score_int(x) for x in ("89", "89c*", " 100", None, "", "n/a")], [89, 89, 100, None, None, None])

    def test_sources_name_the_new_records(self):
        self.use()
        names = " | ".join(s["name"] for s in build_report(address=LA)["sources"])
        for want in ("LAHD rent-stabilized units by parcel", "HUD Multifamily Properties, Assisted and Insured", "VA medical centers"):
            self.assertIn(want, names)


class GoogleReviews(Base):
    def test_the_report_never_asks_google(self):
        self.use()
        build_report(address=LA, census_key="test")
        self.assertFalse([u for u in self.t.calls if "googleapis" in u])

    def test_the_apartment_building_at_the_point(self):
        self.use()
        out = reviews.lookup(34.0471, -118.2503, "548 S SPRING ST, LOS ANGELES, CA, 90013", "k")
        p = out["place"]
        self.assertEqual((out["how"], out["candidates"], p["name"], p["rating"], p["ratings"]), ("nearby", 1, "Spring Street Lofts", 3.6, 41))
        self.assertTrue(p["distance_m"] < 10)
        self.assertEqual([(r["author"], r["rating"], r["when"]) for r in p["reviews"]], [("Test Author", 2, "3 months ago"), ("Second Author", 5, "a year ago")])
        self.assertEqual(p["reviews"][0]["text"], "test review text one")
        self.assertEqual((len(p["reviews"][1]["text"]), p["reviews"][1]["cut"]), (900, True))
        url, _ = self.t.asked[-1]
        self.assertTrue(url.endswith("/v1/places:searchNearby"))

    def test_the_key_travels_in_a_header_never_in_the_address(self):
        seen = {}
        real = H.get_json
        H.get_json = lambda url, params=None, timeout=5.0, data=None, headers=None: (seen.update(url=url, headers=headers, data=data) or ({"places": []}, 1))
        try:
            reviews.lookup(34.0, -118.0, None, "SECRET-KEY")
        finally:
            H.get_json = real
        self.assertNotIn("SECRET-KEY", seen["url"])
        self.assertEqual(seen["headers"]["X-Goog-Api-Key"], "SECRET-KEY")
        self.assertIn("places.reviews", seen["headers"]["X-Goog-FieldMask"])
        self.assertEqual(json.loads(seen["data"])["locationRestriction"]["circle"]["radius"], 75.0)

    def test_when_google_does_not_know_those_place_types_the_address_is_searched(self):
        self.use(variant={"google-old-types"})
        out = reviews.lookup(34.0471, -118.2503, "548 S SPRING ST, LOS ANGELES, CA, 90013", "k")
        self.assertEqual((out["how"], out["candidates"], out["place"]["id"]), ("text", 1, "by-text"))      # the one 1.4 km away is dropped

    def test_nothing_near_and_nothing_by_text(self):
        self.use(variant={"google-none-near"})
        out = reviews.lookup(34.0471, -118.2503, None, "k")
        self.assertEqual((out["place"], out["candidates"]), (None, 0))

    def test_the_building_across_the_street_is_not_this_building(self):
        self.use(variant={"google-across-street"})
        out = reviews.lookup(34.0471, -118.2503, "548 S SPRING ST, LOS ANGELES, CA, 90013", "k")
        self.assertEqual((out["place"], out["how"], out["candidates"], out["other_address"]), (None, "text", 2, 2))

    def test_the_right_address_is_taken_even_when_another_building_is_nearer(self):
        self.use(variant={"google-second-is-it"})
        out = reviews.lookup(34.0471, -118.2503, "548 S SPRING ST, LOS ANGELES, CA, 90013", "k")
        self.assertEqual((out["place"]["id"], out["how"], out["candidates"], out["other_address"]), ("second", "nearby", 2, 1))

    def test_the_address_search_is_tried_when_only_other_buildings_are_near(self):
        self.use(variant={"google-text-finds-it"})
        out = reviews.lookup(34.0471, -118.2503, "548 S SPRING ST, LOS ANGELES, CA, 90013", "k")
        self.assertEqual((out["place"]["id"], out["how"], out["candidates"], out["other_address"]), ("by-text", "text", 2, 1))

    def test_same_address_goes_by_house_number_and_street(self):
        same = reviews.same_address
        self.assertTrue(same("11740 WILSHIRE BLVD, LOS ANGELES, CA, 90025", "11740 Wilshire Blvd, Los Angeles, CA 90025, USA"))
        self.assertTrue(same("1000 S HOPE ST, LOS ANGELES, CA, 90015", "1000 S Hope St, Los Angeles, CA 90015, USA"))
        self.assertTrue(same("601 S SAN PEDRO ST, LOS ANGELES, CA", "601 South San Pedro Street #2, Los Angeles, CA"))
        self.assertFalse(same("548 S SPRING ST, LOS ANGELES, CA, 90013", "541 S Spring St, Los Angeles, CA 90013, USA"))
        self.assertFalse(same("601 S SAN PEDRO ST, LOS ANGELES, CA, 90014", "600 San Pedro St, Los Angeles, CA 90014, USA"))
        self.assertFalse(same("453 S SPRING ST, LOS ANGELES, CA, 90013", "416 S Spring St, Los Angeles, CA 90013, USA"))
        self.assertFalse(same("548 S SPRING ST, LOS ANGELES", "548 S Broadway, Los Angeles, CA"))
        self.assertFalse(same("548 S SPRING ST", None))
        self.assertFalse(same(None, "548 S Spring St"))
        self.assertFalse(same("SPRING ST, LOS ANGELES", "Spring St, Los Angeles"))

    def test_a_refusal_is_an_error_not_an_empty_answer(self):
        self.use(variant={"google-refuses"})
        with self.assertRaises(H.SourceError) as cm:
            reviews.lookup(34.0471, -118.2503, "548 S SPRING ST", "k")
        self.assertIn("HTTP 403", str(cm.exception))
        with self.assertRaises(H.SourceError):
            reviews.lookup(34.0471, -118.2503, "548 S SPRING ST", "")


if __name__ == "__main__":
    unittest.main()


class Neighborhood(Base):
    def test_transit_finds_the_subway_and_the_nearest_bus(self):
        self.use()
        t = build_report(address=LA)["sections"]["transit"]["data"]
        self.assertEqual(t["rail"][0]["name"], "Pershing Square Station")
        self.assertEqual(t["rail"][0]["type"], "Subway")
        self.assertEqual(len(t["rail"]), 1)                    # the two platform rows are one station
        self.assertLess(t["nearest_bus"]["distance_m"], 200)
        self.assertTrue(t["bus_routes"])
        self.assertTrue(all(b["street"] != "Main St" for b in t["bikeways"]))   # retired segments are dropped

    def test_amenities_counts_and_walk_index(self):
        self.use()
        a = build_report(address=LA)["sections"]["amenities"]["data"]
        self.assertEqual(a["counts"]["grocery"], 31)
        self.assertEqual(a["counts"]["restaurants"], 162 + 106)
        self.assertEqual(a["groceries"][0]["name"], "Spring St Market")
        self.assertEqual(a["pharmacies"][0]["name"], "Broadway Pharmacy")
        self.assertEqual(a["parks"][0]["name"], "Pershing Square")
        self.assertGreaterEqual(a["walk_index"]["score"], 85)
        self.assertLessEqual(a["walk_index"]["score"], 100)

    def test_internet_is_measured_speed_plus_provider_names(self):
        self.use()
        c = build_report(address=LA, census_key="test")["sections"]["connectivity"]["data"]
        self.assertEqual(c["fixed"]["down_mbps"], 360)
        self.assertEqual(c["fixed"]["period"], "2026 Q1")
        self.assertIn("Sonic.net", c["wireline"])
        self.assertEqual(c["carriers"].count("T-Mobile"), 1)
        self.assertEqual(c["broadband_pct"], 92)

    def test_street_air_and_311(self):
        self.use()
        st = build_report(address=LA)["sections"]["street"]["data"]
        self.assertEqual(st["air"]["pm25"], 12.5)
        self.assertEqual(st["air"]["pm25_vs_who"], 2.5)
        self.assertEqual(st["s311"]["graffiti"], 384)
        self.assertEqual(st["s311"]["dumping"], 39 + 25)
        self.assertEqual(st["homeless"]["unsheltered"], 868)

    def test_people_and_council(self):
        self.use()
        p = build_report(address=LA, census_key="test")["sections"]["people"]["data"]
        self.assertEqual(p["council"]["name"], "Downtown Los Angeles")
        self.assertEqual(p["renter_pct"], 54)
        self.assertEqual(p["commute_min"], 31)
        self.assertEqual(p["transit_pct"], 4)

    def test_people_skipped_without_key_but_council_still_shows(self):
        self.use()
        p = build_report(address=LA)["sections"]["people"]
        self.assertEqual(p["status"], "ok")
        self.assertIsNone(p["data"].get("renter_pct"))
        self.assertEqual(p["data"]["council"]["name"], "Downtown Los Angeles")

    def test_ground_soft_story_and_evictions(self):
        self.use()
        r = build_report(address=LA)["sections"]
        self.assertEqual(r["hazards"]["data"]["ground"], {"liquefaction_zone": False, "landslide_zone": False, "ms": r["hazards"]["data"]["ground"]["ms"]})
        self.assertFalse(r["hazards"]["data"]["soft_story"]["listed"])
        self.assertEqual(r["building"]["data"]["evictions"]["total"], 2)
        self.assertEqual(r["building"]["data"]["evictions"]["kinds"][0], {"kind": "At-Fault 3 Day", "n": 2})

    def test_soft_story_listed_address(self):
        from n8spotsecrets.sources import hazards
        self.use()
        s = hazards.soft_story("1831", "N VINE ST")
        self.assertTrue(s["listed"])
        self.assertIsNone(s["completed"])
        self.assertEqual(s["deadline"], "2026-08-09")

    def test_one_new_source_down_does_not_touch_the_old_sections(self):
        self.use(fail={"Transit_Stops", "6rrh-rzua", "Speedtest_by_Ookla", "CalEnviroScreen", "h73f-gn57"})
        s = build_report(address=LA, census_key="test")["sections"]
        for k in ("building", "permits", "complaints", "safety", "hazards", "noise", "price"):
            self.assertEqual(s[k]["status"], "ok", k)
        self.assertEqual(s["transit"]["status"], "no_data")
        self.assertEqual(s["amenities"]["status"], "no_data")           # no businesses, no walk index: parks alone would read "car needed"
        self.assertEqual(s["street"]["status"], "partial")              # the homeless count still answers

    def test_outside_la_city_keeps_the_regional_layers(self):
        self.use()
        s = build_report(address=SM, census_key="test")["sections"]
        self.assertEqual(s["transit"]["status"], "ok")
        self.assertEqual(s["connectivity"]["status"], "ok")
        self.assertEqual(s["amenities"]["status"], "skipped")
        self.assertIsNone(s["street"]["data"]["s311"])
