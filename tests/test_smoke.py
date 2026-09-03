"""Smoke tests: every design must produce a panel-legal image from junk data.

Deliberately offline — no network, no cached shapes, no history. If a renderer
only works when adsb.lol is up and the shape library has been fetched, it will
fail on the frame at 3am and nobody will know why.

    python -m unittest discover tests -v
"""
from __future__ import annotations

import os
import time
import unittest
from datetime import datetime
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

from flightframe import canvas, config, palette, units
from flightframe.render import DESIGNS, flight, liveried, portrait, rose, section
from flightframe.sources import Aircraft
from flightframe.tracking import AIRBORNE, LANDED, OUT_OF_RANGE, SCHEDULED, Flight

M = units.METRIC


class NullShapes:
    """Stands in for the shape library with nothing cached and no network."""

    def get(self, _code):
        return None

    def resolve(self, _code):
        return "Unidentified"


def _aircraft(n: int = 12) -> list[Aircraft]:
    out = []
    for i in range(n):
        out.append(Aircraft(
            hex=f"{i:06x}", callsign=f"TST{i:03d}", type="A20N",
            registration=f"G-TST{i}", lat=51.5 + i * 0.02, lon=-0.1 + i * 0.03,
            altitude_ft=1_200 + i * 3_100, ground_speed_kt=180 + i * 20,
            track_deg=(i * 29) % 360, vertical_fpm=(i % 3 - 1) * 1_400,
            distance_nm=i * 1.9, bearing_deg=(i * 47) % 360, seen_at=time.time(),
        ))
        out[-1].airline = "Test Air"
        out[-1].airline_icao = "BAW"
        out[-1].origin = {"iata": "DUB", "city": "Dublin", "lat": 53.4, "lon": -6.2}
        out[-1].destination = {"iata": "JFK", "city": "New York",
                               "lat": 40.6, "lon": -73.8}
    return out


def _flight(status: str) -> Flight:
    f = Flight(query="BA117", callsign="BAW117", callsign_iata="BA117",
               airline="British Airways",
               origin={"iata": "LHR", "city": "London", "lat": 51.47, "lon": -0.46},
               destination={"iata": "JFK", "city": "New York",
                            "lat": 40.64, "lon": -73.78},
               started_at=time.time(), status=status, type="B788")
    if status != SCHEDULED:
        f.last_seen = time.time() - (2400 if status == OUT_OF_RANGE else 30)
        f.position = {"lat": 52.0, "lon": -20.0, "alt_ft": 37_000,
                      "gs": 480, "track": 280, "vs": 0}
    if status == LANDED:
        f.landed_at = time.time() - 300
    return f


class PanelFormat(unittest.TestCase):
    def test_pack_roundtrip_and_verify(self):
        c = canvas.Canvas()
        c.text(100, 100, "hello")
        packed = palette.pack(palette.quantise(c.rasterise()))
        self.assertEqual(len(packed), palette.PACKED_BYTES)
        palette.verify(packed)

    def test_verify_rejects_illegal_index(self):
        # 0x4 is not a hardware ink; the firmware would drop the whole image.
        bad = bytes([0x44]) * palette.PACKED_BYTES
        with self.assertRaises(ValueError):
            palette.verify(bad)

    def test_verify_rejects_wrong_size(self):
        with self.assertRaises(ValueError):
            palette.verify(b"\x01" * 100)


class Units(unittest.TestCase):
    def test_km_helpers_are_not_interchangeable(self):
        """from_km takes km; distance takes nautical miles. Confusing them put
        Guam 22,275 km from London, beyond any distance on Earth."""
        self.assertAlmostEqual(M.from_km(12_028), 12_028, places=3)
        self.assertAlmostEqual(M.distance(6_495), 12_028, delta=2)
        self.assertAlmostEqual(units.AVIATION.from_km(12_028), 6_495, delta=2)

    def test_no_route_exceeds_earth(self):
        self.assertLess(M.from_km(12_028), 20_015)

    def test_short_city_always_terminates(self):
        """City shorteners must return on every input: the trace design's
        version once looped forever on 'La Rochelle/Île de Ré' and pinned
        the droplet at 100% CPU for an hour. The design is gone; the
        lesson guards its successor."""
        from flightframe.render.next import _short_city
        self.assertTrue(_short_city("La Rochelle/Île de Ré"))
        self.assertTrue(_short_city("Le Grand-Quevilly"))
        self.assertEqual(_short_city("Paisley, Renfrewshire"), "Paisley")


class Config(unittest.TestCase):
    def test_defaults_are_the_public_placeholder(self):
        """A fresh clone must never carry a real home location.

        The environment has to be cleared explicitly. Real variables correctly
        take precedence over the .env file, so inside a container with
        env_file set this test passed on a laptop and failed on the target —
        the code was right and the test was reading the deployment's own
        configuration back to itself.
        """
        drop = [k for k in os.environ if k.startswith(("HOME_", "RADIUS_",
                                                       "UNITS", "SECTION_"))]
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in drop:
                os.environ.pop(k, None)
            with TemporaryDirectory() as tmp:
                s = config.load(env_file=Path(tmp) / "absent.env")
                self.assertTrue(s.is_default_location)
                self.assertEqual(s.label, config.DEFAULT_LABEL)
                self.assertEqual(round(s.lat, 4), config.DEFAULT_LAT)


class Renderers(unittest.TestCase):
    """Every design, rendered offline, packed, and verified."""

    def _check(self, c, name):
        with TemporaryDirectory() as tmp:
            written = canvas.render(c, Path(tmp), name)
            data = written["bin"].read_bytes()
            self.assertEqual(len(data), palette.PACKED_BYTES, name)
            palette.verify(data)

    def test_section(self):
        self._check(section.render(_aircraft(), label="Test", radius_km=25,
                                   units=M), "section")

    def test_section_empty_sky(self):
        self._check(section.render([], label="Test", radius_km=25, units=M), "empty")

    def test_liveried(self):
        self._check(liveried.render(_aircraft(), label="Test", lat=51.5, lon=-0.1,
                                    shapes=NullShapes(), units=M), "liveried")

    def test_portrait(self):
        picked = portrait.choose(_aircraft(), "furthest")
        self.assertIsNotNone(picked)
        ac, heading = picked
        self._check(portrait.render(ac, heading, label="Test",
                                    shapes=NullShapes(), units=M), "portrait")

    def test_rose(self):
        points = [{"city": f"City {i}", "iata": "XXX",
                   "km": 800 + i * 1_400, "bearing": i * 31 % 360}
                  for i in range(14)]
        self._check(rose.render(points, label="Test", units=M), "rose")

    def test_rose_with_nothing_far_enough(self):
        self._check(rose.render([{"city": "Near", "iata": "AAA", "km": 100,
                                  "bearing": 10}], label="Test", units=M), "rose")

    def test_next_board_both_languages(self):
        from datetime import date, timedelta
        from flightframe.render import next as next_design
        flights = [{"flight_no": "BA560",
                    "date": (date.today() + timedelta(days=3)).isoformat(),
                    "dep_time": "09:40", "origin": "LHR",
                    "destination": "FCO", "aircraft": "A320neo",
                    "origin_city": "London", "destination_city": "Rome",
                    "arr_time": "13:10", "dep_terminal": "5",
                    "dep_gate": "A10", "delay_min": 25,
                    "registration": "G-TTNA",
                    "note": None, "status": "upcoming"}]
        for lang in ("en", "it"):
            with self.subTest(lang=lang):
                self._check(next_design.render(flights, name="Test",
                                               lang=lang), f"next-{lang}")
        self._check(next_design.render([], name="Test", lang="it"), "next-0")

    def test_aerodatabox_parsing(self):
        """The provider reads AeroDataBox's leg shape into our row fields,
        including a delay derived from revised-vs-scheduled times."""
        from unittest.mock import patch
        from flightframe import schedule
        leg = [{
            "departure": {"airport": {"iata": "LHR"},
                          "scheduledTime": {"local": "2026-08-22 08:20+01:00"},
                          "revisedTime": {"local": "2026-08-22 08:45+01:00"},
                          "terminal": "5", "gate": "A10"},
            "arrival": {"airport": {"iata": "VCE"},
                        "scheduledTime": {"local": "2026-08-22 11:40+02:00"}},
            "aircraft": {"reg": "G-TTNA", "model": "Airbus A320neo"},
        }]
        with patch.object(schedule.sources, "_get", return_value=leg):
            out = schedule.scheduled_details("BA588", "2026-08-22", "k", "ua",
                                             provider="aerodatabox")
        self.assertEqual(out["dep_time"], "08:45")
        self.assertEqual(out["arr_time"], "11:40")
        self.assertEqual(out["delay_min"], 25)
        self.assertEqual(out["dep_terminal"], "5")
        self.assertEqual(out["dep_gate"], "A10")
        self.assertEqual(out["origin"], "LHR")
        self.assertEqual(out["destination"], "VCE")
        self.assertEqual(out["aircraft"], "Airbus A320neo")
        self.assertEqual(out["registration"], "G-TTNA")
        # airport UTC offsets ride along, per-date so DST is pre-applied
        self.assertEqual(out["dep_offset_min"], 60)
        self.assertEqual(out["arr_offset_min"], 120)
        # Red-eye disambiguation: for a date the flight number serves twice
        # (arriving AND departing), the leg departing that date wins, and
        # landing past midnight sets the day offset.
        legs2 = [
            {"departure": {"airport": {"iata": "CPH"},
                           "scheduledTime": {"local": "2026-11-18 23:20+01:00"}},
             "arrival": {"airport": {"iata": "ICN"},
                         "scheduledTime": {"local": "2026-11-19 19:00+09:00"}}},
            {"departure": {"airport": {"iata": "CPH"},
                           "scheduledTime": {"local": "2026-11-19 23:20+01:00"}},
             "arrival": {"airport": {"iata": "ICN"},
                         "scheduledTime": {"local": "2026-11-20 19:00+09:00"}}},
        ]
        with patch.object(schedule.sources, "_get", return_value=legs2):
            out = schedule.scheduled_details("SK987", "2026-11-19", "k", "ua",
                                             provider="aerodatabox")
        self.assertEqual(out["dep_time"], "23:20")
        self.assertEqual(out["arr_day_offset"], 1)
        with patch.object(schedule.sources, "_get", return_value=None):
            self.assertEqual(schedule.scheduled_details(
                "BA588", "2026-08-22", "k", "ua",
                provider="aerodatabox"), {})

    def test_schedule_line_converts_clocks(self):
        """A 12:31 Tampa departure reads 18:31 on an Italian frame."""
        from flightframe.cli import _schedule_line
        row = {"date": "2026-08-28", "dep_time": "12:31", "arr_time": "15:16",
               "origin": "TPA", "destination": "PHL",
               "dep_offset_min": -240, "arr_offset_min": -240}
        line = _schedule_line(row, {"tz": "Europe/Rome", "lang": "it"})
        self.assertEqual(
            line, "TPA 12:31 – PHL 15:16   ·   in Italia 18:31 – 21:16")
        # and without stored offsets, only the airports' clocks appear
        bare = dict(row, dep_offset_min=None)
        self.assertEqual(_schedule_line(bare, {"tz": "Europe/Rome"}),
                         "TPA 12:31 – PHL 15:16")

    def test_hunt_pick_is_picky(self):
        """The origin-airport hunt must choose the climbing, on-heading
        aircraft with the right callsign prefix — and prefer the expected
        airframe when two candidates qualify."""
        from flightframe.tracking import hunt_pick
        going = {"flight": "THY4XW", "hex": "4bb197", "alt_baro": 8000,
                 "baro_rate": 2200, "track": 110, "t": "A359"}
        wrong_way = {"flight": "THY77J", "hex": "4bb198", "alt_baro": 9000,
                     "baro_rate": 1800, "track": 290, "t": "A359"}
        other_airline = {"flight": "BAW32M", "hex": "406a01",
                         "alt_baro": 7000, "baro_rate": 2400, "track": 112,
                         "t": "A320"}
        cruiser = {"flight": "THY1AB", "hex": "4bb199", "alt_baro": 37000,
                   "baro_rate": 0, "track": 108, "t": "B77W"}
        other_type = {"flight": "THY9CD", "hex": "4bb19a", "alt_baro": 6000,
                      "baro_rate": 2000, "track": 118, "t": "A321"}
        cands = [wrong_way, other_airline, cruiser, other_type, going]
        pick = hunt_pick(cands, 112.0, "THY", "A359")
        self.assertEqual(pick["hex"], "4bb197")
        # no prefix match at all -> refuse rather than guess
        self.assertIsNone(hunt_pick([other_airline], 112.0, "THY", None))

    def test_fa_pick_prefers_the_airborne_instance(self):
        """AeroAPI returns three dated instances of a flight number; the
        one with wheels up and not yet down is this flight."""
        from flightframe.tracking import _fa_pick
        import time as _t
        items = [
            {"ident_icao": "THY1986", "scheduled_off": "2026-08-30T15:55:00Z"},
            {"ident_icao": "THY1986", "scheduled_off": "2026-08-29T15:55:00Z"},
            {"ident_icao": "THY1986", "registration": "TC-LGG",
             "scheduled_off": "2026-08-28T15:55:00Z",
             "actual_off": "2026-08-28T17:34:49Z"},
        ]
        pick = _fa_pick(items, _t.time())
        self.assertEqual(pick["registration"], "TC-LGG")
        # nothing near now and nothing airborne -> refuse
        far = [{"scheduled_off": "2020-01-01T00:00:00Z"}]
        self.assertIsNone(_fa_pick(far, _t.time()))

    def test_flight_blind_estimate(self):
        """Airline says EnRoute, receivers silent: the poster shows a
        clock-estimated flight, and the render is deterministic (no
        footer clock) so the panel does not re-blit needlessly."""
        f = _flight(SCHEDULED)
        c = flight.render(f, label="Test", shapes=NullShapes(), units=M,
                          estimated={"frac": 0.4},
                          schedule_line="LHR 18:34 – IST 22:35")
        self._check(c, "blind")
        svg1 = c.svg()
        self.assertIn("in flight", svg1)
        self.assertIn("waiting for a live signal", svg1)
        from datetime import timedelta
        c2 = flight.render(f, label="Test", shapes=NullShapes(), units=M,
                           estimated={"frac": 0.4},
                           schedule_line="LHR 18:34 – IST 22:35",
                           now=datetime.now() + timedelta(minutes=7))
        self.assertEqual(svg1, c2.svg())

    def test_flight_every_state(self):
        for status in (SCHEDULED, AIRBORNE, OUT_OF_RANGE, LANDED):
            with self.subTest(status=status):
                self._check(flight.render(_flight(status), label="Test",
                                          shapes=NullShapes(), units=M), status)


class Tracking(unittest.TestCase):
    def test_cruise_is_cheaper_than_approach(self):
        """The whole point of the adaptive cadence."""
        cruise = _flight(AIRBORNE)
        approach = _flight(AIRBORNE)
        approach.position.update(lat=41.0, lon=-73.0)
        self.assertGreater(cruise.panel_interval_s, approach.panel_interval_s)
        self.assertLessEqual(approach.panel_interval_s, 300)

    def test_out_of_range_holds_last_position(self):
        f = _flight(OUT_OF_RANGE)
        self.assertIsNotNone(f.position)
        self.assertIsNotNone(f.progress)
        self.assertGreaterEqual(f.panel_interval_s, 900)

    def test_progress_never_exceeds_one(self):
        f = _flight(AIRBORNE)
        f.position.update(lat=10.0, lon=-140.0)     # miles off the great circle
        self.assertLessEqual(f.progress, 1.0)
        self.assertGreaterEqual(f.progress, 0.0)


class DisplaySelection(unittest.TestCase):
    def test_set_get_and_reject(self):
        from flightframe.display import Selection
        with TemporaryDirectory() as tmp:
            sel = Selection(Path(tmp))
            self.assertEqual(sel.current(), "portrait")       # sane default
            self.assertTrue(sel.set("rose")[0])
            self.assertEqual(sel.current(), "rose")
            self.assertFalse(sel.set("nonsense")[0])
            self.assertFalse(sel.set("flight")[0])            # tracker owns this
            self.assertEqual(sel.current(), "rose")           # unchanged by failures

    def test_tracked_flight_overrides(self):
        from flightframe.display import Selection
        with TemporaryDirectory() as tmp:
            sel = Selection(Path(tmp))
            sel.set("section")
            self.assertEqual(sel.effective(tracking_active=False), "section")
            self.assertEqual(sel.effective(tracking_active=True), "flight")

    def test_corrupt_file_falls_back(self):
        from flightframe.display import Selection
        with TemporaryDirectory() as tmp:
            sel = Selection(Path(tmp))
            sel.path.write_text("{ not json")
            self.assertEqual(sel.current(), "portrait")


class DeviceTokens(unittest.TestCase):
    def test_token_is_64_lowercase_hex(self):
        """The firmware validates the shape (fp_device_token_valid) and
        silently discards anything else, then retries setup forever."""
        from flightframe.registry import Registry as Reg
        with TemporaryDirectory() as tmp:
            reg = Reg(Path(tmp) / "app.sqlite")
            reg.tenant_add("t1", "T", 51.5, -0.1, "L")
            token = reg.device_register("t1", "aa:bb:cc:dd:ee:ff", "test")
            self.assertRegex(token, r"^[0-9a-f]{64}$")

    def test_reregister_moves_and_rotates(self):
        """A frame re-provisioned with another tenant's secret must move,
        and its old bearer token must die with the move."""
        from flightframe.registry import Registry as Reg
        with TemporaryDirectory() as tmp:
            reg = Reg(Path(tmp) / "app.sqlite")
            reg.tenant_add("t1", "A", 51.5, -0.1, "L1")
            reg.tenant_add("t2", "B", 45.0, 9.0, "L2")
            tok1 = reg.device_register("t1", "aa:bb:cc:dd:ee:ff", "hw")
            tok2 = reg.device_register("t2", "aa:bb:cc:dd:ee:ff", "hw")
            self.assertIsNone(reg.device_by_token(tok1))
            self.assertEqual(reg.device_by_token(tok2)["tenant_id"], "t2")


class Registry(unittest.TestCase):
    def test_every_design_is_importable(self):
        import flightframe.render as r
        for d in DESIGNS:
            self.assertTrue(hasattr(r, d.name), d.name)

    def test_cli_and_web_agree(self):
        """These drifted once and the gallery silently lost a design."""
        from flightframe.cli import DESIGNS as cli_names
        from flightframe.web import DESIGNS as web_rows
        self.assertEqual(list(cli_names), [row[0] for row in web_rows])


class Names(unittest.TestCase):
    """One spelling per city and per aircraft, whichever source answered."""

    def test_city_normalisation(self):
        from flightframe import names
        cases = {
            ("Seoul-si", "GMP"): "Seoul", ("Seoul", "ICN"): "Seoul",
            ("Taipei City", "TSA"): "Taipei", ("Taoyuan City", "TPE"): "Taipei",
            (None, "TSA"): "Taipei",              # an override needs no text
            ("Roissy-en-France", "CDG"): "Paris",
            ("Bâle/Mulhouse", "BSL"): "Basel",
            ("Frankfurt-am-Main", None): "Frankfurt",
            ("Newcastle upon Tyne", None): "Newcastle",
            ("Kingston upon Hull", None): "Hull",
            ("Busan Metropolitan City", None): "Busan",
            ("Seoul Special City", None): "Seoul",
            ("Sapporo-shi", None): "Sapporo",
            ("Shanghai Shi", None): "Shanghai",
            ("Mexico City", None): "Mexico City",
            ("Ho Chi Minh City", None): "Ho Chi Minh City",
            ("Thành phố Hồ Chí Minh", None): "Ho Chi Minh City",
            ("Rio de Janeiro", None): "Rio de Janeiro",
            ("Santa Cruz de Tenerife", None): "Santa Cruz",
            ("Paisley, Renfrewshire", None): "Paisley",
            ("Milano", None): "Milan", ("Genève", "GVA"): "Geneva",
            ("Bergamo", "BGY"): "Bergamo",        # honest town, not "Milan"
            ("London", None): "London",
            (None, None): "", ("", "XXX"): "",
        }
        for (raw, iata), want in cases.items():
            with self.subTest(raw=raw, iata=iata):
                self.assertEqual(names.city(raw, iata), want)

    def test_same_city_from_two_sources(self):
        """The bug that motivated the module: the schedule API's municipality
        for Gimpo ("Seoul-si") and the route database's for Incheon
        ("Seoul") printed as two different cities on one board."""
        from flightframe import names
        self.assertEqual(names.city("Seoul-si", "GMP"),
                         names.city("Seoul", "ICN"))
        self.assertEqual(names.city("Taipei City", "TSA"),
                         names.city("Taipei", "TPE"))

    def test_short_city_bounded(self):
        from flightframe import names
        self.assertEqual(names.short_city("Santa Cruz de Tenerife"), "Santa Cruz")
        self.assertEqual(names.short_city("Frankfurt-am-Main"), "Frankfurt")
        self.assertTrue(names.short_city("La Rochelle/Île de Ré"))
        self.assertLessEqual(len(names.short_city("Antananarivo-Renivohitra", 10)), 10)

    def test_aircraft_styling(self):
        from flightframe import names
        self.assertEqual(names.aircraft("Airbus A320 NEO"), "Airbus A320neo")
        self.assertEqual(names.aircraft("Airbus A321 NEO"), "Airbus A321neo")
        self.assertEqual(names.aircraft("Airbus A330-900 NEO"), "Airbus A330-900")
        self.assertEqual(names.aircraft("Boeing 787-9 Dreamliner"), "Boeing 787-9")
        self.assertEqual(names.aircraft("Boeing 737 MAX 8"), "Boeing 737 MAX 8")
        self.assertEqual(names.aircraft("Boeing 737 MAX8"), "Boeing 737 MAX 8")
        self.assertEqual(names.aircraft("Airbus A320 (sharklets)"), "Airbus A320")
        self.assertIsNone(names.aircraft(None))

    def test_type_codes_for_shapes(self):
        """Schedule model names must reach the right silhouette."""
        from flightframe.render.next import _type_code
        for model, code in (("Airbus A320 NEO", "A20N"), ("Embraer 190", "E190"),
                            ("Embraer 175", "E75L"), ("Embraer E195-E2", "E295"),
                            ("Boeing 737-8", "B38M"), ("Boeing 737-800", "B738"),
                            ("Boeing 737-900", "B739"), ("Boeing 737 MAX 8", "B38M"),
                            ("Airbus A319neo", "A19N"), ("Airbus A321-200", "A321"),
                            ("Boeing 777-9", "B779"), ("Boeing 777-300ER", "B77W"),
                            ("Airbus A350-1000", "A35K"), ("Airbus A330-900", "A339"),
                            ("ATR 72-600", "AT76"), ("Bombardier CRJ-900", "CRJ9"),
                            ("De Havilland Dash 8-400", "DH8D"), ("A20N", "A20N")):
            with self.subTest(model=model):
                self.assertEqual(_type_code(model), code)

    def test_airline_table_knows_new_carriers(self):
        from flightframe import airlines
        self.assertEqual(airlines.lookup("CAL").name, "China Airlines")
        self.assertEqual(airlines.lookup("SZS").body, airlines.lookup("SAS").body)
        self.assertEqual(airlines.lookup(None, "Unknown Air").body, "white")

    def test_board_prints_one_spelling(self):
        """A Gimpo row and an Incheon row on the same board both say Seoul,
        and the API's "A320 NEO" prints as Airbus writes it."""
        from datetime import date, timedelta
        from flightframe.render import next as next_design
        def row(no, days, o, d, oc, dc, craft):
            return {"flight_no": no, "status": "upcoming",
                    "date": (date.today() + timedelta(days=days)).isoformat(),
                    "dep_time": "10:00", "arr_time": "12:00",
                    "origin": o, "destination": d,
                    "origin_city": oc, "destination_city": dc,
                    "aircraft": craft}
        flights = [row("SK1516", 2, "LHR", "CPH", "London", "Copenhagen",
                       "Airbus A320 NEO"),
                   row("SK987", 3, "CPH", "ICN", "Copenhagen", "Seoul",
                       "Airbus A350-900"),
                   row("CI261", 5, "GMP", "TSA", "Seoul-si", "Taipei City",
                       "Boeing 737-800"),
                   row("CI260", 9, "TSA", "GMP", "Taipei City", "Seoul-si",
                       "Boeing 737-800")]
        svg = next_design.render(flights, name="Test", lang="it").svg()
        self.assertNotIn("Seoul-si", svg)
        self.assertNotIn("Taipei City", svg)
        self.assertNotIn("A320 NEO", svg)
        self.assertIn("Seoul → Taipei", svg)
        self.assertIn("Taipei → Seoul", svg)
        self.assertIn("Copenhagen → Seoul", svg)
        self.assertIn("Airbus A320neo", svg)


if __name__ == "__main__":
    unittest.main()
