# FlightPortrait Device Protocol

Canonical contract between frames and the cloud — the ONLY place it is
documented. When code and this doc disagree, fixing the doc is part of the PR.
The authoritative server implementation is the FlightPortrait cloud
(closed source; its reference simulator is the permanent contract test —
any protocol change must keep it green). A minimal open server example
lives in `examples/byos_server.py`.

This document is licensed CC-BY-4.0 so independent server and client
implementations can reproduce it.

Transport: device-initiated only; there is no push channel of any kind.
The compiled-in default endpoint is strict HTTPS against the public CA
bundle. A hand-set BYOS base may be plain HTTP (§5). Versioned path
`/device/v1/` — frames in the field poll for years.

## 1. Panel image format (`.bin`)

The payload a frame downloads and blits. Verified against the Waveshare
`EPD_13in3e` driver (E-paper_Separate_Program/13.3inch_e-Paper_E, Jul 2026).

- Panel: 13.3" E Ink Spectra 6 (E), **1200×1600, portrait-native**. The frame
  is mounted portrait; the image is authored portrait; no rotation anywhere.
- **Size: exactly 960,000 bytes.** No header, no compression. Integrity is
  the `image_hash` (sha256 of these bytes) from `GET /device/v1/display`.
- Layout: row-major, top row of the portrait image first. Each row is
  600 bytes = 1200 pixels, 2 px/byte, 4 bits each.
- **Nibble order: LEFT pixel in the HIGH nibble.**
  `byte = (left_px << 4) | right_px`.
- Pixel codes (4-bit): `0x0` black · `0x1` white · `0x2` yellow · `0x3` red ·
  `0x5` blue · `0x6` green. `0x4` and `0x7`–`0xF` are invalid — firmware may
  render them as white but the server never emits them.

### Master/slave split (firmware concern, not file format)

The panel has two controllers, each driving a 600×1600 half. The split is
per-row at SPI-blit time, exactly as the Waveshare driver does it:

```
for each of the 1600 rows (each 600 bytes):
    bytes   0..299  → master controller (CS_M) — left  600 px
    bytes 300..599  → slave  controller (CS_S) — right 600 px
```

Both controllers receive `DTM (0x10)` then their half of every row in row
order. The `.bin` stays one flat buffer; firmware streams it into PSRAM,
verifies sha256, then blits.

## 2. Endpoints

### `POST /device/v1/setup` — first connected setup

Request body (JSON):

```json
{ "mac": "aa:bb:cc:dd:ee:ff", "hw_rev": "proto-e1004",
  "provision_secret": "<this unit's factory setup credential>",
  "pairing_public_key": "<base64url-no-pad SEC1 P-256 public key>",
  "pairing_counter": 1,
  "pairing_nonce_hash": "<sha256 hex of the 64-char ASCII nonce>" }
```

Response `200` when the request carried the pairing fields:

```json
{ "device_token": "<64 hex chars — random 256-bit bearer token>",
  "device_ref": "<32 lowercase hex — opaque public frame reference>",
  "pairing": { "counter": 1,
               "expires_at": "2026-07-24T12:34:56+00:00" } }
```

`device_ref` and `pairing` appear **only** when the request included the
pairing fields; a pairing-free setup (a BYOS server without the extension,
or the fixed-host departure-cleanup call) returns `{"device_token": …}`
alone.

- Production has **no shared setup secret**. Every MAC is pre-enrolled with
  its own high-entropy credential in the protected `fp_factory/setup_cred`
  NVS key. `FP_DEV_PROVISION_SECRET` is a bench-only seed and must be empty in
  production images.
- `pairing_public_key` is the 65-byte uncompressed SEC1 point (`0x04 || X ||
  Y`) encoded base64url without padding. `pairing_nonce_hash` is lowercase
  SHA-256 hex over the 64 lowercase-hex nonce **as ASCII**, not over the 32
  random bytes. The three pairing fields are additive: a BYOS server may omit
  support and return only `device_token`.
- `401` — unknown MAC or wrong per-device `provision_secret`. `400` —
  well-formed JSON with invalid pairing registration fields. `422` —
  missing or ill-typed body fields. `429` — per-IP rate limit. Firmware
  treats every non-200 identically: log the status, back off (§3).
- Each successful response returns a fresh bearer plaintext; the server stores
  only its SHA-256. Firmware writes it to NVS and sends it as
  `Authorization: Bearer …` forever after. An exact retry of the same
  public-key/counter/nonce-hash tuple is accepted only while that proof is
  unconsumed and unexpired: the server replaces the device bearer without
  erasing personal/account/control state or extending the proof. This is the
  narrow response-loss recovery case.
- Any other accepted setup for the same MAC rotates the token (the old one
  stops working immediately) and is the re-provision / factory-reset flow.
- Firmware validates the complete response before the token can reach NVS:
  `device_token` is exactly 64 lowercase hex, first-party `device_ref` is
  exactly 32 lowercase hex, `pairing.counter` is an exact positive JSON
  integer equal to the pending registration counter, and `expires_at` is a
  fitting parseable UTC RFC3339 string. Fractional numbers, decoded NUL,
  oversized values, partial pairing acknowledgements, and silent truncation
  are protocol failures. The fixed-host departure-cleanup call applies the
  same token-shape check even though it discards that token.
- **A re-setup is a factory reset server-side too**: it removes the device's
  account link, control grants, and personal state while preserving
  non-personal telemetry history and immutable factory enrollment. This is a
  deliberate product choice: possession is ownership, and whoever sets the
  frame up next starts fresh. Reset also rotates the runtime
  possession-signing identity and revokes control tokens, so no prior owner's
  scan or saved code still works. Same-cloud Wi-Fi re-provisioning does not call
  setup and therefore retains the existing device token. There is no shared
  pairing secret.

### `GET /device/v1/display` — every wake

Request headers:

```
Authorization: Bearer <device_token>          required
X-Battery-Mv:  3941                           telemetry, optional but expected
X-Rssi:        -61
X-Fw-Version:  0.1.0
X-Boot-Reason: rtc | power-on | button | pairing
X-Power-Source: battery | usb                 reserved, v1.1 (desk mode)
```

On a left-arrow/KEY1 re-pair registration the frame reports
`X-Boot-Reason: pairing` and adds:

```
X-Pairing-Nonce-Hash: <64 lowercase hex>
X-Pairing-Counter: <strictly increasing decimal integer>
X-Pairing-Signature: <base64url-no-pad 64-byte r||s>
```

The frame's counter is a u32: it generates values in `1..2^32-1` and rejects
any acknowledgement whose counter does not exactly match the pending
registration. A server must ack with the registered counter, never a larger
one.

The signature is P-256 ECDSA/SHA-256 over these exact UTF-8 bytes, with no
trailing newline:

```
flightportrait-pair-v1\n{device_ref}\n{counter}\n{nonce_hash}
```

`r` and `s` are fixed-width 32-byte big-endian integers concatenated in that
order. They are not ASN.1 DER.

- **`X-Boot-Reason: button` is the "show someone" moment**: the server
  opens a live window (fast `sleep_s` for the next ~15 min) and, if app
  changes are pending, re-renders inline before answering — one button
  press delivers the fresh poster in a single wake. Firmware needs no
  special handling beyond reporting the boot reason truthfully.
  What a press cannot do is beat the panel: the redraw itself is ~12–30 s,
  and inside the minimum refresh spacing the frame waits it out before
  blitting (§3). Press-to-glass is therefore tens of seconds, and up to the
  spacing window on top — it is never instant, and a press during the wake
  it already caused is not seen at all, because the buttons are a
  deep-sleep wake source rather than a live interrupt.
- **`X-Power-Source`** (additive, v1.1): when a frame reports `usb`, the
  server may serve ~60 s polls for as long as that holds — app edits land
  within a minute while plugged in. On `battery` (or when absent) the
  editions schedule applies. Firmware must never fake `usb`.

Response `200`:

```json
{
  "image_url":  "https://…/img/<sha256-hex>.bin",
  "image_hash": "sha256:<hex of the 960,000 .bin bytes>",
  "sleep_s":    43873,
  "firmware":   null,
  "reset":      false,
  "pairing":    { "counter": 2,
                  "expires_at": "2026-07-24T12:39:56+00:00" }
}
```

`pairing` is present only when the server accepted the optional signed
registration headers. Equal counter/hash is an idempotent retry; a lower,
expired, consumed, or mismatched registration is rejected. A BYOS response
may omit `pairing`; the frame keeps polling normally and reports account
pairing as unavailable.

Firmware rejects the whole display response before copying it when a required
field would truncate or change type: image and firmware URLs must be
non-empty fitting `http://`/`https://` strings, image hashes are exactly
`sha256:` plus 64 lowercase hex, OTA hashes are exactly 64 lowercase hex,
`sleep_s` is an integer in `1..2^32-1`, `reset` is Boolean, and a present
pairing acknowledgement must exactly match the pending counter with a fitting
parseable UTC expiry. HTTP-client allocation failure is handled as an ordinary
wake failure rather than dereferencing a null handle.

- `401` — unknown/rotated token. `400` — malformed pairing headers.
  `429` — per-token rate limit. `503` — render temporarily unavailable.
  Firmware treats every non-200 identically — backoff (§3) — so a frame
  whose token was rotated server-side keeps backing off until a human
  re-provisions it (the right-arrow/KEY0 hold); there is no automatic
  re-setup on 401.
- **`sleep_s` is authoritative** — server-owned battery knob. It is
  "seconds until the next edition": the server schedules wakes at the
  owner's chosen edition moments (dawn/dusk sun times by default),
  plus a small anti-stampede spread. Firmware never needs to
  know any of this — it just sleeps the number. Low battery
  (`X-Battery-Mv` below server threshold) collapses the schedule to one
  edition a day.
- **Hash-skip:** if `image_hash` equals the NVS copy, do not download —
  sleep. A no-change wake costs ~5 s awake instead of ~45 s.
- After download: verify sha256 against `image_hash` AND size == 960,000
  before blitting; on mismatch drop the image and treat the wake as failed.
- `firmware`: `null`, or `{ "url": …, "sha256": …, "version": … }`.
  Firmware evaluates the offer **before** the hash-skip, so an update
  lands even on a wake where the art is unchanged. OTA flow: stream to
  the inactive partition while computing sha256;
  reject on any mismatch (never boot an unverified image); set boot flag,
  restart. The new image confirms by completing ONE successful poll
  (which reports its version via `X-Fw-Version` — that report IS the
  adoption signal the server tracks); otherwise the bootloader rolls
  back, and the server infers the revert from the old version reappearing.
  Auto-rollback requires `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`
  (set in `sdkconfig.defaults`); a build without it boots the new image
  unconditionally. OTA integrity is the transport (HTTPS on the default
  target) plus the sha256 the *server* supplied — there is no
  application-level image signature. Secure Boot v2, which makes the
  bootloader refuse any image not signed with the project key, is not
  enabled yet; it is burned in during manufacturing and will be in place on
  shipping units.
  Server-side guards: offers only when battery ≥ 3600 mV, deterministic
  staged rollout (pins → percentage), kill switch, auto-halt on
  revert storms or on repeated offers a device never adopts, and an
  owner opt-out (app pref `auto_update: false` — outranks everything,
  including pins; the frame then never sees a `firmware` offer).
  A build profile is also matched: the images built from
  `sdkconfig.production.defaults` expect encrypted flash and encrypted NVS,
  so they abort in `nvs_flash_init()` on a bench-flashed unit — before the
  poll that would confirm them, which means the bootloader rolls back and
  the offer repeats forever. Every release records the profile its bytes
  came from and every device records the profile it last booted, and an
  offer needs the two to agree on storage. A unit with
  no recorded provenance is offered open-storage images only; units flashed
  at the jig must have theirs recorded there, since their first image never
  arrived over OTA. This is server-side only — nothing on the wire changes,
  and the frame is told nothing it did not already receive.
- `reset`: `true` → use the same factory-reset path as a ten-second
  right-arrow/KEY0 hold.
  First commit the protected `fp_factory/reset_clean` erase bit: state 1 for a
  local-only reset, or state 3 when an official-cloud cleanup is also owed.
  Only then restore ESP-IDF's persistent Wi-Fi driver state and erase the
  `flightportrait` app namespace. After the local erase, commit state 0 or 2
  respectively, preserve `fp_factory`, and reboot immediately. Boot resumes
  any state with the erase bit before provisioning or poll. If a checked step
  fails, its prior durable state is retained for retry. No old-token sleep
  window is allowed.
- `image_url` may be a time-limited presigned URL — fetch it on the same
  wake, never store it. Store only `image_hash`.

### `POST /device/v1/log` — optional, batched error/crash reports

Same auth + telemetry headers as `/display`. Body:

```json
{ "logs": [ { "message": "wifi connect failed", "level": "warn",
              "ts": 1789700000 } ] }
```

`level` defaults to `"error"`; `ts` (unix seconds) defaults to server time.
Response `200 {"ok": true}`. Fire-and-forget: a failed log post is never
retried at the cost of battery.

Firmware records warn/error events (failed Wi-Fi joins, failed polls,
failed downloads, failed OTA applies) into a small NVS ring that keeps the
newest 8 entries, then drains it here on the next wake whose `/display`
poll succeeds — one extra request only when there is something to report.
The ring is cleared after ONE post attempt, success or not, so a server
without `/log` costs nothing. Messages are fixed firmware strings; `ts`
is omitted for events recorded before SNTP established trusted time.

## 3. Failure handling (firmware-owned)

On ANY failure — no WiFi, DNS, TLS, non-200, corrupt download:
sleep `min(2^n × 5 min, 6 h)` where n is the consecutive-failure count
(persisted in NVS), then retry. A success resets n to 0. The frame must
never hot-loop and never blit an unverified buffer.

Firmware defends the glass regardless of what the server says:

- **Minimum refresh spacing** — `CONFIG_FP_MIN_REFRESH_SPACING_S`, default
  **60 s** (loosened from 180 s so a demo table answers a press).
  Provenance before changing it: the panel vendor
  suggests ~one update/day and their own reliability testing ran at 150 s
  intervals, so anything under 150 s is off the tested envelope. The 2×/day
  art cadence is aligned; fast dashboard cadences are off-label for this
  glass.
- **Never two blits at once.** A blit takes tens of seconds and drives the
  panel's rails through a strict power sequence; a second one started over
  it is refused outright.
- **Spacing waits, it does not discard.** By the time the spacing is
  checked the image is already in PSRAM, and PSRAM does not survive deep
  sleep — dropping it would cost a second 960 KB download for the same
  picture. The frame drops the radio, waits out the remainder (up to
  `CONFIG_FP_MAX_GUARD_WAIT_S`, default 90 s) and then blits. So a button
  press always ends in a redraw; inside the spacing window it is simply a
  slower one, and the frame is awake and silent while it waits.
- **A deferred draw is not a failure.** If the spacing outlasts the awake
  budget the poll returns deferred, not failed: the backoff counter is
  untouched, the image hash is NOT recorded, and the frame sleeps only
  until the panel may draw. Treating it as a failure used to back a
  perfectly healthy frame off for minutes because someone pressed refresh
  twice.

Client-side request timeouts (relevant to slow BYOS hosts): 15 s for
`/setup` and `/log`, 20 s for `/display`, 30 s for image and OTA
downloads. A timeout is an ordinary failure: backoff.

## 4. NVS schema

Namespace `flightportrait` (source of truth: `main/nvs_schema.h`). The
COMPLETE list of what a frame remembers — no app-account or location data:

This namespace was renamed before the first device flash. The persisted-name
rename window is now closed forever: future firmware must migrate NVS data in
place and may not silently rename or drop this namespace or any key.

| key | type | purpose |
|---|---|---|
| `wifi_ssid` / `wifi_pass` | str | provisioning result |
| `wifi_bssid` / `wifi_chan` | blob6 / u8 | fast-connect hint (~2 s joins) |
| `dev_token` | str (64 hex) | bearer token from `/setup` |
| `setup_req` | u8 | legacy setup-required flag, read during upgrade migration |
| `target_phase` | u8 | target journal: 0 ready, 1 interrupted/re-submit required, 2 coherent target/setup required |
| `target_v1` | blob | versioned, atomic normalized URL override + BYOS setup-secret pair |
| `depart_clean` | u8 | 1 = first-party departure intent precedes journal; 2 = coherent BYOS target with best-effort old-cloud unlink pending |
| `image_hash` | str | last blitted image — the hash-skip state |
| `api_base` | str (≤127) | legacy BYOS override, read-only until migrated into `target_v1` |
| `byos_setup` | str (1–159 bytes) | legacy encrypted secret, read-only until migrated into `target_v1` |
| `device_ref` | str (32 lowercase hex) | opaque public reference used in pairing QR/signature |
| `pair_ctr` / `prov_state` | u32 / u8 | monotonic pairing counter and recoverable transaction state |
| `pair_nonce` / `pair_hash` / `pair_exp` | str | transient nonce, its hash, and RFC3339 expiry |
| `pair_priv` / `pair_pub` | blob32 / blob65 | runtime P-256 possession key; erased on factory reset |
| `sec2_user` / `sec2_pass` | str | runtime Security-2 QR credentials |
| `sec2_salt` / `sec2_ver` | blob | persisted SRP salt/verifier |
| `prov_qr` / `pair_qr` | u8 | which recoverable QR transaction is on glass |
| `backoff_n` | u8 | consecutive failures (resets to 0 on success) |
| `boot_count` | u32 | diagnostics |
| `err_ring` / `err_pos` | blob / u8 | newest-8 error ring drained to `/log`, cleared after one post attempt |
| `shipping` | u8 | 0 normal; 2 prep complete/field image pending; 1 field image armed for the customer's first cold/USB boot |

The separate namespace `fp_factory` contains `setup_cred`, the per-device
manufacturing credential, plus `reset_clean`, the minimal reset journal:

| key | type | purpose |
|---|---|---|
| `setup_cred` | str | immutable per-device official setup credential |
| `reset_clean` | u8 bitset | 0 none; 1 local reset erase pending; 2 best-effort official-cloud unlink pending; 3 both |

App and remote factory resets never erase `fp_factory`. Every reset commits
the local erase bit before `esp_wifi_restore()` and `nvs_erase_all()` on
`flightportrait`; the cloud bit is added only when official cleanup provenance
must survive. After both local erasures, state 1 becomes 0 and state 3 becomes
2. A power cut with the erase bit set repeats those idempotent local steps
before the device may provision, pair, or poll. This also makes a clean BYOS
reset atomic without inventing an official-cloud request.
Production uses encrypted NVS (`sdkconfig.production.defaults`) under
release-mode flash encryption. The regular defaults are intentionally a
non-eFuse-burning bench profile.

## 5. Bring your own server (BYOS)

The flagship promise made mechanical: a stock frame
can be re-pointed at any server that implements the three endpoints
above, with no toolchain and no reflash. Reference server:
[`examples/byos_server.py`](../examples/byos_server.py), stdlib-only —
point a frame at it and it will set up, poll, download, and display.

**How the URL is set — by hands on the frame, only.** The server base
URL is an optional field in BLE provisioning and in the long-press
re-provision flow, stored with its optional setup secret in the versioned
`target_v1` blob. An empty URL in that blob means the compiled-in default.
**No server can ever write it**: there is no
OTA config path and no API response field that touches it, and any
future proposal to add one is a breaking protocol change, not an
implementation detail. Provisioning normalizes rather than documenting footguns:
leading/trailing whitespace trimmed, scheme lowercased, trailing
slashes stripped; the scheme must be `http` or `https`; anything else
(or >127 chars) is rejected and the previous value kept. A blank entry
restores the default — there is no separate reset action.

**Transport policy, split.** The compiled-in default endpoint is strict
HTTPS against the public CA bundle, non-negotiable. A hand-set custom
URL may be plain `http://` so a machine on your LAN needs no domain and
no certificate (`http://192.168.0.2:8000` is a perfectly good BYOS
server). The trade-off, honestly: on a custom `http://` server, the
device token travels in cleartext on that network. Custom-CA trust is
out of scope for v1; a custom `https://` server must present a
certificate the public CA bundle can verify.

**Setup against your server.** `POST /device/v1/setup` goes to whatever
base is in effect; on your own server, `provision_secret` is whatever
your server decides it is. Image and firmware URLs in `/display`
responses are fetched as given, so they may live on the same host or
anywhere else you point them.

**The OTA consequence, accepted.** A re-pointed frame takes firmware
updates from its new server. Ownership means FlightPortrait's update
guarantees end at the switch; the app's about screen says so quietly.

**A wrong URL can never brick.** Every failure against a custom base
(garbage host, refused connection, bad TLS, vanished server) follows
the normal backoff curve (§3). Factory reset — the long-press flow or
the `reset` flag from whichever server the frame currently polls —
erases the target blob with the rest of app NVS and returns the frame to the
compiled-in default. A server can therefore un-point a frame back to
the default; no server can ever point a frame anywhere else.

## 6. BLE provisioning and local pairing

The implementation is ESP-IDF v5.3.1 Unified Provisioning over BLE
(`wifi_prov_scheme_ble`) with Security 2: SRP6a authentication/key exchange
and AES-GCM. Salt, verifier, username, and a 24-random-byte password are
generated per frame at runtime, retained in encrypted app NVS so an interrupted
transaction can resume, and rotated by factory reset. The provisioning GATT
service (standard Espressif endpoints plus the two custom endpoints below)
uses a custom 128-bit service UUID whose bytes are
`5c 33 af f1 92 c8 47 a8 b4 64 9d 20 72 54 46 50` in array order — note
ESP-IDF hands this array to the BLE stack LSB-first, so generic scanners
display the byte-reversed form. Apps locate the frame by the advertised
`PROV_XXXXXX` name from the QR, not by UUID.

The full-screen black/white e-ink QR is the standard Espressif shape:

```json
{"ver":"v1","name":"PROV_A1B2C3","username":"fp-0123456789abcdef",
 "pop":"<48 lowercase hex>","transport":"ble"}
```

For `ver: "v1"`, these shapes are exact: `name` is `PROV_` followed by six
uppercase hexadecimal characters, `username` is `fp-` followed by 16
lowercase hexadecimal characters, and `pop` is exactly 48 lowercase
hexadecimal characters. The app rejects a non-canonical v1 QR before invoking
the native provisioning bridge.

QR payloads, Security-2 credentials, setup credentials, pairing nonces, bearer
tokens, and signatures are never logged. The upstream QR component's payload
logging is explicitly disabled before encoding.

Before either a Security-2 or re-pair QR can replace the poster, firmware
erases `image_hash` and checks the NVS commit. A failed invalidation prevents
the QR draw. Re-pair expiry repeats the invalidation before clearing its
lifecycle state. Therefore the first recovery poll must redraw the poster and
cannot hash-skip while a QR remains on glass.

Both custom endpoints are created **before** provisioning starts and registered
after it starts. Auto-stop is disabled. BLE stays alive through Wi-Fi join,
cloud setup, delivery of the pairing bundle, and an explicit app
acknowledgement; the one-second cleanup delay lets the acknowledgement leave
the GATT characteristic before transport teardown.

### `fp-api-base`

This endpoint must be called before Wi-Fi credentials are sent. It is locked
after `WIFI_PROV_CRED_RECV`.

- Legacy payload: a plain UTF-8 URL up to 127 bytes. Empty restores the
  compiled-in default. Any plain payload clears a previously staged BYOS
  secret.
- Structured payload:

  ```json
  {"url":"https://example.net/base","setup_secret":"user-chosen value"}
  ```

  Both fields are required strings. `url` follows §5 normalization and is
  1–127 bytes after normalization; it must not resolve to the first-party
  compiled default. `setup_secret` is 1–159 decoded UTF-8 bytes. The endpoint
  accepts up to 2048 wire bytes so maximally sized JSON-escaped values fit;
  malformed UTF-8, decoded NUL, duplicate/extra fields, and embedded input NUL
  are rejected.

Responses: `{"status":"ok"}` on acceptance, `{"status":"invalid"}` for a
rejected payload (previous value kept), `{"status":"locked"}` once Wi-Fi
credentials have been received in this transaction.

Each modeled journal stage ends in a checked `nvs_commit()`. Target
replacement still needs an explicit journal because one commit cannot make
the target blob and every separately scoped identity key one atomic unit:

1. Persist `target_phase=1` before any target mutation. From this write onward,
   bearer use and cloud setup are denied.
2. Atomically replace one versioned `target_v1` blob containing both the
   normalized URL and setup secret. Readers therefore see old+old or new+new,
   never a crossed URL/secret pair.
3. Erase the old bearer plus all server-scoped public-ref/P-256/pairing state,
   set the lifecycle to `QR_READY`, then write `target_phase=2` last.
4. Only after `/setup` and its local pairing acknowledgement are durable does
   firmware write `target_phase=0` last, enabling bearer use.

A cut at any boundary before step 3 finishes leaves phase 1. Boot forces BLE
provisioning, refuses authenticated polling or setup, and returns
`target-required`; the app ends that BLE transaction, asks for an explicit
FlightPortrait-vs-BYOS choice (not “preserve”), and starts a fresh
right-arrow/KEY0
transaction that re-submits `fp-api-base` before Wi-Fi. A coherent phase-2
target safely retries or finishes setup. The pure host test cuts the journal
after every durable step.

An identical target+secret keeps an existing token. First-party setup always
uses the factory enrollment credential; custom setup reads its secret from
`target_v1`. The legacy plain form remains accepted for URL parsing/storage
and clears any staged BYOS secret. If custom setup is then required, it fails
closed with `byos-secret-required` until a structured URL+secret is supplied.
The immutable factory credential is never sent to the effective BYOS target
(especially not over custom HTTP). Neither endpoint form nor any secret is
logged, and temporary decoded/request buffers are wiped.

When a frame with a prior local first-party bearer changes to BYOS, firmware
persists `depart_clean=1` **before** the target journal so a cut cannot lose
the old-target provenance. Once the BYOS blob is coherent this becomes state
2. After Wi-Fi joins and before BYOS setup/use, firmware makes a best-effort
`POST` to the fixed compiled `CONFIG_FP_API_BASE/device/v1/setup` with the
immutable factory credential and no pairing fields, discards the returned
token, and clears the flag on success. This ordinary re-setup removes the old
account link/personal state. Failure never blocks BYOS; state 2 remains and a
later connected wake retries. Until cleanup succeeds (or the owner deletes
the account, or a later first-party setup resets it), old cloud/account data
may remain. Custom→custom changes and departures without a prior local bearer
do not schedule this call. A state-1 intent may be canceled before mutation
only when both persisted and desired targets are already first-party. State 2
survives a transition back to first-party and clears only after successful
official `/setup` and its local acknowledgement.

Factory reset cannot erase this obligation. Reset from an existing
first-party identity, or while `depart_clean` is already pending, journals the
obligation as `fp_factory/reset_clean` before Wi-Fi or app NVS is erased.
State 3 makes boot finish the local erase and becomes state 2 only afterward;
state 2 survives fresh setup. If the new explicit target is BYOS, the same
fixed-host cleanup is attempted after Wi-Fi and never blocks BYOS. If it is
first-party, successful official `/setup` and its durable local
acknowledgement clear the journal. Cleanup failure remains retriable and may
leave old cloud/account data until it succeeds or the owner deletes the
account.

### `fp-pair`

The endpoint accepts compact JSON operations (anything else returns
`{"status":"invalid"}`):

```json
{"op":"get"}
{"op":"ack"}
```

`get` may block up to ~30 seconds while cloud setup is still in flight
before answering `retry` — size app timeouts accordingly.

After first-party `/setup` acknowledges the pairing registration, `get`
returns the exact JSON the app redeems:

```json
{"v":1,"kind":"flightportrait-pair",
 "device_ref":"<32 lowercase hex; opaque ref>",
 "nonce":"<64 lowercase hex>",
 "expires_at":"2026-07-24T12:34:56+00:00"}
```

Field order and spelling are fixed. In v1, `device_ref` is exactly 32
lowercase hexadecimal characters and `nonce` is exactly 64 lowercase
hexadecimal characters. The app rejects any other shape. `ack` is accepted
only after this bundle was read. It clears the plaintext nonce/expiry and ends
BLE only after the app has safely stored its control token. `get` may instead
return:

| response | meaning |
|---|---|
| `{"status":"byos"}` | custom-server setup succeeded; optional FlightPortrait account pairing is unavailable |
| `{"status":"complete"}` | same-target right-arrow/KEY0 Wi-Fi reprovision retained the bearer; no new possession proof was created (use left-arrow/KEY1 for a new phone) |
| `{"status":"expired"}` | an unacknowledged first-pair bundle is confirmed expired against a trusted clock; setup was finalized safely, and a phone without a durable control token uses left-arrow/KEY1 |
| `{"status":"byos-secret-required"}` | a plain custom URL needs a structured BYOS setup secret; no factory credential was sent |
| `{"status":"target-required"}` | a target-journal write was interrupted; no token or cloud target was used, and a fresh right-arrow/KEY0 transaction must explicitly re-submit first-party or BYOS |
| `{"status":"retry"}` | first-party cloud setup/bundle is not ready; keep BLE open and retry |

`byos`, `complete`, `expired`, `byos-secret-required`, and `target-required`
end the transaction after their response. `retry` never tears BLE down.
Transient `/setup` failures are retried at a paced interval while `get` may
return `retry`; those attempts share the original provisioning deadline rather
than starting a new timeout.
Likewise, rejected Wi-Fi credentials reset the Unified Provisioning manager's
failed attempt and clear only FlightPortrait's current attempt bits: the
Security-2/protocomm session and original provisioning deadline remain live so
the same phone can submit another SSID/password without rescanning. Failure to
reset the manager itself is a device-local fatal error. A true BLE
transport/session loss still requires a rescan. Once Wi-Fi associates, loss of
internet is retried while that same bounded session remains open.
`target-required` cannot use “preserve”: the next transaction requires an
explicit target and calls `fp-api-base` before Wi-Fi. The app must also
validate the `status` returned by `fp-api-base` and the
`{"status":"ok"}` returned by `ack`.

`QR_READY`, `SETUP_PENDING`, and `PAIR_READY` are persisted across resets.
If `/setup` may have committed but its response or the subsequent local ack was
lost, recovery from `SETUP_PENDING` discards the still-unexposed runtime P-256
identity and retries setup with a new key, counter 1, and nonce. Once
`PAIR_READY` is stored, the same bundle is returned until app ack or expiry.
If the app durably stores control and its ack response is lost, receipt of the
ack has already moved the frame to `COMPLETE`; if the ack never reached the
frame, reconnect returns the same live bundle and expiry provides a bounded
fallback to `COMPLETE`/left-arrow KEY1. A failed signed KEY1 registration advances to a
fresh counter+nonce on recovery, so an equal proof that expired server-side
cannot deadlock the frame.

Left-arrow/KEY1 pairing is a FlightPortrait first-party extension. On a custom/BYOS
target, the gesture is an explicit unsupported no-op: an intent already
persisted before a power cut is returned to `COMPLETE`, and normal polling
continues. The same missing identity on the compiled first-party target is
treated as corruption and fails closed rather than being silently downgraded.

Expiry is tri-state: live, expired, or unknown. A 1970/otherwise untrusted
wall clock is **unknown**, never expired. `PAIR_READY` keeps BLE open until
Wi-Fi/SNTP establishes trusted time. ESP-IDF's provisioning manager
intentionally clears its RAM STA configuration when it starts; on this one
`PAIR_READY` recovery path firmware therefore loads the explicit
`wifi_ssid`/`wifi_pass` app-NVS mirror and supplies it through
`wifi_prov_mgr_configure_sta()`. This emits the same credential/IP events as a
phone submission while leaving Security-2 open. A stale saved credential
returns the manager to its retryable state so the phone may submit another;
ordinary right-arrow/KEY0 provisioning never auto-submits an old credential.

No-phone recovery is bounded by lifecycle, not the generic 600-second BLE
timeout. A proof already known expired, or a `COMPLETE` setup whose only
missing write is the final target-journal barrier, is committed and returned
to polling without starting BLE. If SNTP establishes that an unknown proof
was already expired, a concurrently waiting phone gets up to 30 seconds to
receive `expired`, then firmware completes without it. A still-live
`PAIR_READY` session waits no longer than the proof's persisted remaining TTL;
at expiry it durably clears the proof and resumes normal polling. A malformed
persisted expiry remains unknown/fail-closed. `REPAIR_VISIBLE` connects and
syncs before deciding, or enters normal backoff. Firmware validates every new
server expiry before persisting it, so clock loss cannot collapse a live
five-minute proof or cause a one-second wake loop.

### Physical controls

The E1004 PCB net names do not follow the printed control order. Seeed's
schematic and peripherals table define the same three active-low nets for the
front capacitive pads and rear physical controls:

| printed control | PCB net | action |
|---|---|---|
| circular-arrow Refresh | KEY2 / GPIO5 | poll now; this alone reports `X-Boot-Reason: button` |
| left arrow | KEY1 / GPIO4 | create/register a new signed nonce, show its QR for five minutes |
| right arrow, short | KEY0 / GPIO3 | no-op; resume the interrupted sleep and never report `button` |
| right arrow, hold (default 2 s) | KEY0 / GPIO3 | reopen Security-2 Wi-Fi provisioning |
| right arrow, hold (default 10 s) | KEY0 / GPIO3 | journal any official-cloud cleanup, erase local app/pair/Wi-Fi state, and reboot immediately |

All pins and admin-hold thresholds are Kconfig values.

Same-cloud right-arrow/KEY0 reprovision keeps the device bearer token and skips setup; its
`fp-pair` result is `complete`, not a new proof. Changing the server or its
setup secret forces setup and rotates all server-scoped identity.
Factory reset erases Security-2 credentials and the P-256 key/counter but
preserves `fp_factory/setup_cred` and any `reset_clean` obligation. It commits
the protected reset erase bit before restoring ESP-IDF's separate persistent
Wi-Fi-driver configuration and erasing app NVS. Left-arrow/KEY1 persists
`REPAIR_INTENT` but does not register a server nonce while the panel's
minimum refresh spacing is active; it sleeps out the guard first so the
on-glass QR receives its actual five-minute window. When that QR expires, the
next wake clears its state and polls the current art back onto glass.

### Factory preparation

Per-unit enrollment, eFuse and Secure Boot burn-in, and the shipping-state
journal are a manufacturing procedure rather than part of this contract, and
are not published. What a server implementer needs to know about their
results is already stated above: a frame arrives holding a per-device
`provision_secret` in the protected `fp_factory/setup_cred` namespace (§2),
and shipping units will boot only images signed with the project key (§2,
`GET /device/v1/display`).
