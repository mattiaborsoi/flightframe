"""Over-the-air firmware: the properties a frame nobody can reach depends on.

A frame not opted in must never see an offer. An offer must always be the
complete object the firmware parses, because anything else makes the frame
fail the whole poll. And a version must mean exactly one build.
"""
from __future__ import annotations

import hashlib
import json
import struct
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from flightframe import auth, config
from flightframe import firmware as fw
from flightframe.device import DeviceAPI
from flightframe.registry import Registry
from flightframe.web import Handler


def fake_image(version: str, filler: bytes = b"\x00") -> bytes:
    """Minimal bytes shaped like an ESP-IDF app image: header magic, then an
    esp_app_desc_t at offset 32 carrying the version."""
    head = bytes([fw.ESP_IMAGE_MAGIC]) + b"\x00" * (fw.APP_DESC_OFFSET - 1)
    desc = struct.pack("<I", fw.APP_DESC_MAGIC) + b"\x00" * 12
    desc += version.encode().ljust(32, b"\x00")
    return head + desc + filler * 4096


class Parsing(unittest.TestCase):
    def test_reads_the_version_compiled_into_the_image(self):
        self.assertEqual(fw.image_version(fake_image("1.0.1")), "1.0.1")

    def test_rejects_things_that_are_not_app_images(self):
        self.assertIsNone(fw.image_version(b"not firmware at all" * 10))
        bad = bytearray(fake_image("1.0.1"))
        bad[fw.APP_DESC_OFFSET] ^= 0xFF               # corrupt the desc magic
        self.assertIsNone(fw.image_version(bytes(bad)))
        self.assertIsNone(fw.image_version(b""))

    def test_versions_compare_numerically_not_alphabetically(self):
        self.assertGreater(fw.parse_version("1.0.10"), fw.parse_version("1.0.9"))
        self.assertIsNone(fw.parse_version("1.0"))
        self.assertIsNone(fw.parse_version("1.0.1-beta"))
        self.assertIsNone(fw.parse_version(None))


class StoreRules(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = fw.Store(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _file(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_add_names_the_image_by_its_digest(self):
        data = fake_image("1.0.1")
        info = self.store.add(self._file("a.bin", data))
        self.assertEqual(info["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(self.store.path_for(info["sha256"]).read_bytes(), data)

    def test_refuses_a_version_that_already_names_a_build(self):
        self.store.add(self._file("a.bin", fake_image("1.0.1", b"\x01")))
        with self.assertRaises(ValueError):
            self.store.add(self._file("b.bin", fake_image("1.0.1", b"\x02")))

    def test_refuses_non_images_and_uncomparable_versions(self):
        with self.assertRaises(ValueError):
            self.store.add(self._file("x.bin", b"hello" * 100))
        with self.assertRaises(ValueError):
            self.store.add(self._file("y.bin", fake_image("next")))

    def test_latest_is_the_highest_version(self):
        for v in ("1.0.9", "1.0.10", "1.0.2"):
            self.store.add(self._file(f"{v}.bin", fake_image(v, v.encode()[-1:])))
        self.assertEqual(self.store.latest()["version"], "1.0.10")

    def test_path_lookup_only_accepts_digests(self):
        self.assertIsNone(self.store.path_for("../registry.sqlite"))
        self.assertIsNone(self.store.path_for("a" * 63))


class Offers(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = fw.Store(self.root)
        src = self.root / "img.bin"
        src.write_bytes(fake_image("1.0.1"))
        self.info = self.store.add(src)

    def tearDown(self):
        self.tmp.cleanup()

    def offer(self, device, reported):
        return fw.offer(self.store, device, reported, "https://h", "/device/v1")

    def test_a_frame_not_opted_in_never_sees_an_offer(self):
        """The frame in Italy: older firmware, newer image in the store,
        and it must still get null."""
        self.assertIsNone(self.offer({"fw_auto": 0}, "0.2.4"))
        self.assertIsNone(self.offer({}, "0.2.4"))

    def test_an_opted_in_frame_gets_the_complete_object(self):
        o = self.offer({"fw_auto": 1}, "1.0.0")
        self.assertEqual(set(o), {"url", "sha256", "version"})
        self.assertEqual(o["version"], "1.0.1")
        self.assertRegex(o["sha256"], r"^[0-9a-f]{64}$")   # what the frame accepts
        self.assertTrue(o["url"].endswith(f"/device/v1/fw/{o['sha256']}.bin"))

    def test_no_offer_unless_strictly_newer(self):
        self.assertIsNone(self.offer({"fw_auto": 1}, "1.0.1"))
        self.assertIsNone(self.offer({"fw_auto": 1}, "1.0.2"))

    def test_no_offer_when_the_running_version_is_unknown(self):
        self.assertIsNone(self.offer({"fw_auto": 1}, None))
        self.assertIsNone(self.offer({"fw_auto": 1}, "garbage"))

    def test_no_offer_from_an_empty_store(self):
        empty = fw.Store(self.root / "nothing")
        self.assertIsNone(fw.offer(empty, {"fw_auto": 1}, "1.0.0", "h", "/d"))


class OverHttp(unittest.TestCase):
    """End to end through the real handler, as a frame sees it."""

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
        cls.tenant = cls.reg.tenant_add("t1", "Alice", 51.5, -0.1, "Place")
        (cls.app.tenant_out("t1") / "next.bin").write_bytes(b"\x11" * 960_000)
        src = root / "img.bin"
        src.write_bytes(fake_image("1.0.1"))
        cls.image = fw.Store(cls.app.data_dir).add(src)
        handler = partial(Handler, app=cls.app, registry=cls.reg,
                          device=DeviceAPI(cls.app, cls.reg),
                          limiter=auth.LoginLimiter())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.tmp.cleanup()

    def _request(self, path, method="GET", body=None, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _register(self, mac):
        status, body = self._request(
            "/device/v1/setup", "POST",
            {"mac": mac, "hw_rev": "t",
             "provision_secret": self.tenant["provision_secret"]})
        self.assertEqual(status, 200)
        return json.loads(body)["device_token"]

    def _poll(self, token, version):
        status, body = self._request(
            "/device/v1/display",
            headers={"Authorization": f"Bearer {token}",
                     "X-Fw-Version": version})
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_opt_in_is_per_frame_and_the_offer_downloads(self):
        london = self._register("0a:00:00:00:00:01")
        italy = self._register("0a:00:00:00:00:02")
        self.assertTrue(self.reg.device_set_fw_auto("0a:00:00:00:00:01", True))

        # The held frame gets exactly what it always got: null.
        self.assertIsNone(self._poll(italy, "0.2.4")["firmware"])

        offer = self._poll(london, "1.0.0")["firmware"]
        self.assertIsInstance(offer, dict)              # never a bare string
        self.assertEqual(offer["version"], "1.0.1")

        path = offer["url"].split(f":{self.port}", 1)[-1]
        status, data = self._request(path)
        self.assertEqual(status, 200)
        self.assertEqual(hashlib.sha256(data).hexdigest(), offer["sha256"])

        # Up to date: the offer stops.
        self.assertIsNone(self._poll(london, "1.0.1")["firmware"])

    def test_unknown_firmware_is_404_without_a_token(self):
        status, _ = self._request(f"/device/v1/fw/{'0' * 64}.bin")
        self.assertEqual(status, 404)
        status, _ = self._request("/device/v1/fw/../registry.sqlite")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
