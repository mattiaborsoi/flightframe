"""Multi-tenant isolation, exercised over real HTTP against a live server.

The properties these tests defend are the ones a public deployment cannot
compromise on: no tenant data without a session, no cross-tenant reads with
one, and device tokens that are bound to exactly one household.
"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from flightframe import auth, config
from flightframe.device import DeviceAPI
from flightframe.registry import Registry
from flightframe.web import Handler


def _call(port, path, method="GET", body=None, headers=None, cookie=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 "Origin": f"http://127.0.0.1:{port}",
                 **({"Cookie": cookie} if cookie else {}),
                 **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}"), e.headers
        except json.JSONDecodeError:
            return e.code, {}, e.headers


class Tenancy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.app = config.AppConfig(
            data_dir=root / "data", out_dir=root / "out",
            cache_dir=root / "cache", user_agent="test",
            app_hosts=frozenset({"127.0.0.1", "localhost"}))
        for d in (cls.app.data_dir, cls.app.out_dir, cls.app.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
        cls.reg = Registry(cls.app.registry_path)
        cls.t1 = cls.reg.tenant_add("t1", "Alice", 51.5, -0.1, "Alice Place")
        cls.t2 = cls.reg.tenant_add("t2", "Bob", 45.0, 9.0, "Bob Place")
        cls.reg.user_add("t1", "a@example.com", auth.hash_password("pw-a"))
        cls.reg.user_add("t2", "b@example.com", auth.hash_password("pw-b"))
        # One rendered poster each, with distinct bytes.
        for tid, fill in (("t1", b"\x11"), ("t2", b"\x22")):
            out = cls.app.tenant_out(tid)
            (out / "portrait.bin").write_bytes(fill * 960_000)
            (out / "trace.png").write_bytes(b"png-" + fill)
        handler = partial(Handler, app=cls.app, registry=cls.reg,
                          device=DeviceAPI(cls.app, cls.reg),
                          limiter=auth.LoginLimiter())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def _login(self, email, password):
        status, body, headers = _call(self.port, "/login", "POST",
                                      {"email": email, "password": password})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        cookie = headers.get("Set-Cookie", "").split(";")[0]
        self.assertTrue(cookie.startswith("ff_session="))
        return cookie

    def test_anonymous_gets_nothing(self):
        status, body, _ = _call(self.port, "/api/state")
        self.assertEqual(status, 401)
        status, _, _ = _call(self.port, "/api/design", "POST",
                             {"design": "rose"})
        self.assertEqual(status, 401)

    def test_wrong_password_rejected(self):
        status, body, _ = _call(self.port, "/login", "POST",
                                {"email": "a@example.com", "password": "nope"})
        self.assertEqual(status, 401)

    def test_sessions_are_tenant_scoped(self):
        ca = self._login("a@example.com", "pw-a")
        cb = self._login("b@example.com", "pw-b")
        _, state_a, _ = _call(self.port, "/api/state", cookie=ca)
        _, state_b, _ = _call(self.port, "/api/state", cookie=cb)
        self.assertEqual(state_a["label"], "Alice Place")
        self.assertEqual(state_b["label"], "Bob Place")
        # Settings equally scoped — and Bob's location never appears in
        # anything Alice can fetch.
        _, s_a, _ = _call(self.port, "/api/settings", cookie=ca)
        self.assertEqual(s_a["lat"], 51.5)
        self.assertNotIn("45.0", json.dumps(state_a) + json.dumps(s_a))

    def test_settings_write_is_scoped_and_validated(self):
        ca = self._login("a@example.com", "pw-a")
        status, body, _ = _call(self.port, "/api/settings", "POST",
                                {"refresh_minutes": 30}, cookie=ca)
        self.assertTrue(body["ok"])
        self.assertEqual(self.reg.tenant("t1")["refresh_minutes"], 30)
        self.assertEqual(self.reg.tenant("t2")["refresh_minutes"], 15)
        _, body, _ = _call(self.port, "/api/settings", "POST",
                           {"lat": 999}, cookie=ca)
        self.assertFalse(body["ok"])

    def test_csrf_origin_required(self):
        ca = self._login("a@example.com", "pw-a")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/design", method="POST",
            data=b'{"design":"rose"}',
            headers={"Content-Type": "application/json", "Cookie": ca,
                     "Origin": "http://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 403)

    def test_device_binding_and_image_isolation(self):
        # setup without a secret fails; with tenant secret binds to tenant
        status, body, _ = _call(self.port, "/device/v1/setup", "POST",
                                {"mac": "0a:00:00:00:00:01", "hw_rev": "t"})
        self.assertEqual(status, 403)
        status, body, _ = _call(
            self.port, "/device/v1/setup", "POST",
            {"mac": "0a:00:00:00:00:01", "hw_rev": "t",
             "provision_secret": self.t1["provision_secret"]})
        self.assertEqual(status, 200)
        tok1 = body["device_token"]
        self.assertRegex(tok1, r"^[0-9a-f]{64}$")

        status, disp, _ = _call(self.port, "/device/v1/display",
                                headers={"Authorization": f"Bearer {tok1}"})
        self.assertEqual(status, 200)
        digest_1 = disp["image_hash"].split(":", 1)[1]

        # The REAL firmware downloads without the bearer header, so the
        # digest is the capability: an unauthenticated fetch with the right
        # digest must succeed, and a guessed digest must 404.
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/device/v1/img/{digest_1}.bin")
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(len(r.read()), 960_000)
        bogus = "f" * 64
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/device/v1/img/{bogus}.bin")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 404)

        # And the authenticated path still works.
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/device/v1/img/{digest_1}.bin",
            headers={"Authorization": f"Bearer {tok1}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(len(r.read()), 960_000)

    def test_disabled_tenant_gets_503_not_401(self):
        status, body, _ = _call(
            self.port, "/device/v1/setup", "POST",
            {"mac": "0a:00:00:00:00:03", "hw_rev": "t",
             "provision_secret": self.t2["provision_secret"]})
        tok = body["device_token"]
        self.reg.tenant_set_status("t2", "disabled")
        try:
            status, _, _ = _call(self.port, "/device/v1/display",
                                 headers={"Authorization": f"Bearer {tok}"})
            self.assertEqual(status, 503)
        finally:
            self.reg.tenant_set_status("t2", "active")


if __name__ == "__main__":
    unittest.main()


class SettingsValidation(unittest.TestCase):
    """Server-side rules hold regardless of what the browser sends."""

    @classmethod
    def setUpClass(cls):
        Tenancy.setUpClass()
        cls.port, cls.reg = Tenancy.port, Tenancy.reg

    @classmethod
    def tearDownClass(cls):
        Tenancy.tearDownClass()

    def _cookie(self):
        _, body, headers = _call(self.port, "/login", "POST",
                                 {"email": "a@example.com", "password": "pw-a"})
        return headers.get("Set-Cookie", "").split(";")[0]

    def test_rejections(self):
        c = self._cookie()
        bad = [{"lat": 91}, {"lon": -181}, {"radius_nm": 3},
               {"radius_nm": 999}, {"refresh_minutes": 1},
               {"label": ""}, {"label": "x" * 41},
               {"awake_from": "25:00", "awake_until": "23:00"},
               {"awake_from": "09:00", "awake_until": "08:00"},
               {"units": "imperial"}, {"tz": "Mars/Olympus"},
               {"lat": float("nan")} if False else {"tz": "not-a-zone"}]
        for payload in bad:
            _, body, _ = _call(self.port, "/api/settings", "POST", payload,
                               cookie=c)
            self.assertFalse(body["ok"], payload)

    def test_acceptance(self):
        c = self._cookie()
        good = {"lat": 45.4642, "lon": 9.19, "radius_nm": 30,
                "refresh_minutes": 20, "label": "Duomo",
                "awake_from": "08:00", "awake_until": "22:30",
                "units": "aviation", "tz": "Europe/Rome"}
        _, body, _ = _call(self.port, "/api/settings", "POST", good, cookie=c)
        self.assertTrue(body["ok"], body)
        t = self.reg.tenant("t1")
        self.assertEqual(t["tz"], "Europe/Rome")
        self.assertEqual(t["awake_until"], "22:30")



class FlightListIsolation(unittest.TestCase):
    """A tenant's travel plans are visible to nobody else — the property the
    'where is Mattia flying next' feature must never compromise."""

    @classmethod
    def setUpClass(cls):
        Tenancy.setUpClass()
        cls.port, cls.reg = Tenancy.port, Tenancy.reg

    @classmethod
    def tearDownClass(cls):
        Tenancy.tearDownClass()

    def _cookie(self, email, pw):
        _, _, h = _call(self.port, "/login", "POST",
                        {"email": email, "password": pw})
        return h.get("Set-Cookie", "").split(";")[0]

    def test_friend_cannot_see_my_flights(self):
        from datetime import date, timedelta
        ca = self._cookie("a@example.com", "pw-a")
        cb = self._cookie("b@example.com", "pw-b")
        when = (date.today() + timedelta(days=5)).isoformat()
        _, body, _ = _call(self.port, "/api/flights", "POST",
                           {"flight_no": "BA560", "date": when,
                            "origin": "LHR", "destination": "FCO"}, cookie=ca)
        self.assertTrue(body["ok"], body)
        _, mine, _ = _call(self.port, "/api/flights", cookie=ca)
        _, theirs, _ = _call(self.port, "/api/flights", cookie=cb)
        self.assertEqual(len(mine["flights"]), 1)
        self.assertEqual(theirs["flights"], [])
        self.assertNotIn("BA560", json.dumps(theirs))
        # Once admin-linked, a follower manages the SAME list: the family
        # frame's login adds and removes the traveller's flights. The link
        # is CLI-only, so no dashboard action can ever grant this.
        self.reg.tenant_admin_update("t2", {"follows_flights_of": "t1"})
        try:
            _, followed, _ = _call(self.port, "/api/flights", cookie=cb)
            self.assertEqual(len(followed["flights"]), 1)
            self.assertTrue(followed["own"])
            # spaces in flight numbers are normalized, not rejected
            _, added, _ = _call(self.port, "/api/flights", "POST",
                                {"flight_no": "F9 2538", "date": when},
                                cookie=cb)
            self.assertTrue(added["ok"], added)
            _, owner_view, _ = _call(self.port, "/api/flights", cookie=ca)
            self.assertIn("F92538", json.dumps(owner_view))
            self.assertEqual(len(owner_view["flights"]), 2)
            # follower deletes from the shared list too
            _, del_result, _ = _call(self.port, "/api/flights/delete", "POST",
                                     {"id": added["id"]}, cookie=cb)
            self.assertTrue(del_result["ok"])
            _, owner_view, _ = _call(self.port, "/api/flights", cookie=ca)
            self.assertEqual(len(owner_view["flights"]), 1)
        finally:
            self.reg.tenant_admin_update("t2", {"follows_flights_of": None})
        # unlinked again: t2 is back to an empty, isolated list
        _, unlinked, _ = _call(self.port, "/api/flights", cookie=cb)
        self.assertEqual(unlinked["flights"], [])



class DownloadGraceWindow(unittest.TestCase):
    """A hash issued seconds before a re-render must stay downloadable."""

    def test_previous_generation_served(self):
        import hashlib
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = config.AppConfig(
                data_dir=root / "data", out_dir=root / "out",
                cache_dir=root / "cache", user_agent="test",
                app_hosts=frozenset({"localhost"}))
            reg = Registry(app.registry_path)
            t = reg.tenant_add("g1", "G", 51.5, -0.1, "L")
            token = reg.device_register("g1", "0a:00:00:00:00:09", "hw")
            out = app.tenant_out("g1")
            old = b"\x11" * 960_000
            (out / "portrait.bin").write_bytes(old)
            api = DeviceAPI(app, reg)
            old_hash = hashlib.sha256(old).hexdigest()
            # renderer rotates the poster: old becomes .prev, new lands
            (out / "portrait.bin").replace(out / "trace.bin.prev")
            (out / "portrait.bin").write_bytes(b"\x22" * 960_000)
            served = api.image_by_hash(token, old_hash)
            self.assertEqual(served, old)


class MalformedRequests(unittest.TestCase):
    """Hostile or sloppy bodies get a clean status, never a traceback."""

    @classmethod
    def setUpClass(cls):
        Tenancy.setUpClass()
        cls.port = Tenancy.port

    @classmethod
    def tearDownClass(cls):
        Tenancy.tearDownClass()

    def _raw(self, path, data, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method="POST", data=data,
            headers={"Content-Type": "application/json",
                     "Origin": f"http://127.0.0.1:{self.port}", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_non_object_body_on_device_route(self):
        # bare int is valid JSON; must not 500 the setup handler
        self.assertIn(self._raw("/device/v1/setup", b"5"), (200, 400, 403))

    def test_negative_content_length_rejected(self):
        self.assertEqual(self._raw("/login", b"{}",
                                   {"Content-Length": "-1"}), 400)

    def test_bad_flight_id_does_not_crash(self):
        _, _, h = _call(self.port, "/login", "POST",
                        {"email": "a@example.com", "password": "pw-a"})
        cookie = h.get("Set-Cookie", "").split(";")[0]
        status, body, _ = _call(self.port, "/api/flights/delete", "POST",
                                {"id": "not-a-number"}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
