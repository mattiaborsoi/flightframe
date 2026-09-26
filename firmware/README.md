# FlightPortrait firmware

The open source firmware and device protocol for FlightPortrait, a
battery-powered 13.3" color e-ink frame that draws the aircraft that
flew over your home.

The frame is deliberately simple. It wakes, asks its server over HTTPS
whether there is a new drawing, shows it, and goes back to sleep. It
never listens for incoming connections, so there is no way into your
network through it. Everything clever happens on the server; the frame
displays the result and sleeps.

**And it is yours.** A stock frame can be pointed at any server that
implements the three endpoints in [`docs/PROTOCOL.md`](docs/PROTOCOL.md)
— from the pairing flow, with no toolchain and no reflash (§5, bring
your own server). `examples/byos_server.py` is a complete reference
server in plain stdlib Python: point a frame at it and it will set up,
poll, download, and display whatever panel image you serve.

Hardware: ESP32-S3 (reTerminal E1004 first, then XIAO ESP32-S3 Plus on
the EE02 driver board), 13.3" E Ink Spectra 6, 1200×1600 portrait.

## Map of the repo

- `docs/PROTOCOL.md` — the device API contract and the byte-level
  `.bin` panel format. The ONLY place the protocol is documented; when
  code and doc disagree, fixing the doc is part of the PR.
- `main/` — the shippable ESP-IDF firmware: wake → poll → display →
  deep sleep, BLE provisioning (Security 2), device identity, download
  verification, exponential backoff, error log. Validated end to end on
  physical E1004 glass (Jul 2026): provisioning, authenticated download,
  hash verification, full refresh, deep sleep. `nvs_schema.h` is the
  complete list of what a frame remembers; a file-by-file map is at the
  end of this section.
- `examples/byos_server.py` — the stdlib-only reference server.
- `arduino-sd-demo/` — a no-network demo that blits a rendered `.bin`
  from microSD to the panel.
- `first-flash/` — the box-opening sequence, test images, and the
  one-page failure tree.
- `tests/` — pure-C contract tests that compile with plain `cc`, no
  hardware or IDF needed (see each file's header).

<details>
<summary><b>main/, file by file</b></summary>

- `app_main.c` — boot → wake-reason dispatch → poll-or-provision →
  always ends in deep sleep; backoff counter persisted in NVS.
- `state_machine.c` — poll, signed re-pair, remote-reset, button wake,
  hash-skip/download/verify/blit, and deep-sleep dispatch.
- `provisioning.c` — BLE Unified Provisioning (Security 2), the
  pre-WiFi `fp-api-base` target/secret endpoint, and the recoverable
  `fp-pair` bundle/ack transaction.
- `identity.c` — per-device factory credentials, runtime P-256
  possession key, exact ECDSA wire encoding, runtime SRP credentials.
- `wifi.c` — STA join from NVS creds with BSSID/channel fast-connect
  hints; radio off before sleep.
- `api_client.c` — the three PROTOCOL.md endpoints (CA bundle, no cert
  pinning): setup/token, display with telemetry headers, streamed
  960 KB download into PSRAM with sha256 + size + oversize checks.
  Never blits an unverified buffer. Base URL resolves at request time
  from the journaled `target_v1` URL+secret blob, else the compiled
  default (BYOS, PROTOCOL.md §5).
- `target_contract.c` — versioned target-blob codec, strict UTF-8/NUL
  boundary, target/reset power-cut barriers, first-party departure
  cleanup policy (pure C, host-tested).
- `api_base.c` — BYOS URL normalization, pure C (host-tested; the
  FlightPortrait cloud's reference simulator mirrors it — change them
  together).
- `panel.c` — dual-CS blit and a deep-sleep-safe ≥180 s refresh guard.
- `backoff.c` — `min(2^n × 5 min, 6 h)`, firmware-owned.
- `errlog.c` — warn/error ring in NVS, drained to `/device/v1/log` on
  the next successful poll (ring codec in `errlog_contract.c`, pure C,
  host-tested).
- `nvs_schema.h` — the complete list of what a frame remembers.
- `Kconfig.projbuild` — `FP_API_BASE` (the compiled-in default
  endpoint), hardware controls, and the DEV-ONLY factory-credential
  seed.
- `partitions.csv` — factory (never OTA'd) + ota_0/ota_1 + nvs +
  nvs_keys.
- `sdkconfig.defaults` — esp32s3, OPI PSRAM, NimBLE, OTA rollback,
  watchdog, and the 12 KiB `app_main` stack (see Building).
- `sdkconfig.production.defaults`, `sdkconfig.factory-prep.defaults`,
  `tools/build_factory_nvs.py` — the controlled factory flow (see
  Building).

</details>

## Building

ESP-IDF ≥ 5.3:

```sh
idf.py set-target esp32s3 && idf.py build
```

No local IDF?

```sh
docker run --rm -v $PWD:/project -w /project espressif/idf:v5.3.1 idf.py build
```

CI compile-checks every push, runs the host tests, and builds the
bench, production, and production-plus-factory-prep profiles separately
so configuration drift cannot hide behind the default build.

Three things to know before you modify it:

- **Secrets never go in configs.** `FP_DEV_PROVISION_SECRET` is a
  bench-only one-time seed; production images leave it empty and get one
  unique credential per unit from the factory jig. Do not put a real
  credential into a shared `sdkconfig`, a command line, or a build log.
  Building without `-DSDKCONFIG` regenerates `sdkconfig` in place; pass
  `-DSDKCONFIG` to a build directory if that file ever carries a bench
  credential.
- **The production profiles are not for bench units.**
  `sdkconfig.production.defaults` (release flash/NVS encryption) and
  `sdkconfig.factory-prep.defaults` (manufacturing-only shipping image)
  belong to the controlled factory flow in `docs/PROTOCOL.md` §6, which
  includes irreversible eFuse and signing steps. A successful compile is
  not authorization to flash them.
- **The main task needs its big stack.** `sdkconfig.defaults` gives the
  synchronous `app_main` state machine 12 KiB because provisioning and
  polling nest JSON buffers inside the ESP-IDF TLS/HTTP client, which is
  not safe on the 3,584-byte default.

Dev bench note: on the dev Mac, IDF v5.3.1 lives at `~/esp/esp-idf` and
only the Python 3.12 env works (`export
IDF_PYTHON_ENV_PATH="$HOME/.espressif/python_env/idf5.3_py3.12_env"`
before `source ~/esp/esp-idf/export.sh`); the pyenv 3.9 env is missing
`ruamel.yaml` and 3.13 is unsupported, and neither failure mentions
Python in its first line. `idf.py --version` answers happily on a broken
env; only a build is evidence the toolchain works.

## arduino-sd-demo (day one, no network)

Art on glass within an hour of opening the box: it blits a rendered
`.bin` from microSD via Seeed_GxEPD2.

1. Arduino IDE: board **XIAO_ESP32S3**, PSRAM **OPI PSRAM**, USB CDC on.
2. Install the pinned Seeed_GxEPD2 commit and Adafruit dependencies
   exactly as specified in `first-flash/README.md`; the registry's plain
   `GxEPD2` package is not the E1004 fork.
3. Copy `GxEPD2_T133A01_1200x1600.{h,cpp}` from that repo's
   `examples/GxEPD2_reTerminal_E1004/` into `arduino-sd-demo/` (the
   13.3" dual-chip driver is GPL, so we fetch it locally rather than
   vendor it).
4. `python3 tools/make_sd.py --src <panel.bin>` with the SD card mounted
   (the calibration patterns in `first-flash/bins/` are ready-made
   sources).
5. Card in, flash the sketch. The circular-arrow Refresh control
   re-reads the card after a swap.

Compile-verified without hardware (Jul 2026, esp32 core 3.3.10, where
`CDCOnBoot=default` is the explicit enabled value):

From the firmware root:

```sh
export ARDUINO_DIRECTORIES_USER="$PWD/.arduino-user"
arduino-cli compile \
  --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi,USBMode=hwcdc,CDCOnBoot=default" \
  arduino-sd-demo
```

To flash, `arduino-cli upload` with the same `--fqbn` and the newly
appearing serial path as `-p`; never leave a wildcard in a flash
command. The full sequence, test buffers, and failure tree live in
`first-flash/README.md` and `first-flash/TRIAGE.md`.

Study (don't copy): `usetrmnl/trmnl-firmware` (GPL) for poll-loop flow;
the Seeed reTerminal E1004 wiki for pinouts (mirrored in the sketch
header).

## License

Apache-2.0 (see `LICENSE`). `docs/PROTOCOL.md` is additionally licensed
CC-BY-4.0 so third-party server and client implementations can reproduce
it. The arduino-sd-demo's panel driver (`GxEPD2_T133A01_1200x1600.*`,
Seeed_GxEPD2, GPL-3.0) is fetched at build time and never distributed
here. FlightPortrait's cloud, mobile app, and the poster renderer are
separate, closed components — this repo is everything the device speaks
and runs.
