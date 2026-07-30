# E1004 first-flash kit

The box-opening sequence. Do it in this order; stop on the first failed
checkpoint. The SD path proves glass, power, SPI and palette before WiFi or
cloud enter the room.

Sources checked for this runbook: Seeed's E1004 getting-started guide,
peripherals cookbook and `Seeed_GxEPD2` E1004 example. The E1004 ships with a
16 GB microSD card; use FAT32 and never exceed 32 GB for this first session.

## Night before

1. Charge the E1004. Put it face-up on a clean foam mat.
2. Install Arduino IDE or `arduino-cli` and ESP32 Arduino core 3.3.10. Keep
   this repo's CLI libraries isolated from the user's global sketchbook, then
   install the exact versions used for this bring-up. From the firmware
   root:

   ```sh
   export ARDUINO_DIRECTORIES_USER="$PWD/.arduino-user"
   arduino-cli lib update-index
   arduino-cli lib install 'Adafruit BusIO@1.17.4'
   arduino-cli lib install 'Adafruit GFX Library@1.12.6'
   env ARDUINO_LIBRARY_ENABLE_UNSAFE_INSTALL=true \
     arduino-cli lib install --git-url \
     'https://github.com/Seeed-Projects/Seeed_GxEPD2.git#1100ea37c16b910fd79152f4250c13d802b9c20b'
   ```

   The registry's plain `GxEPD2` package is the upstream library, not Seeed's
   E1004 fork. The commit pin is deliberate: Seeed's inherited release tag
   predates the E1004 example.
3. Copy `GxEPD2_T133A01_1200x1600.{h,cpp}` from
   `Seeed_GxEPD2/examples/GxEPD2_reTerminal_E1004/` beside
   `arduino-sd-demo/arduino-sd-demo.ino`. They are fetched locally,
   not committed, because the driver is GPL-3.0.
4. Verify the kit:

   ```sh
   cd first-flash/bins
   shasum -a 256 -c MANIFEST.sha256
   ```

5. Compile before the device arrives:

   ```sh
   export ARDUINO_DIRECTORIES_USER="$PWD/.arduino-user"
   arduino-cli compile \
     --fqbn 'esp32:esp32:XIAO_ESP32S3:PSRAM=opi,USBMode=hwcdc,CDCOnBoot=default' \
     arduino-sd-demo
   ```

   With ESP32 core 3.3.10, `CDCOnBoot=default` explicitly means enabled;
   `CDCOnBoot=cdc` means disabled despite the misleading option name.

## Glass day: SD proof first

1. Photograph the unopened unit and its labels. Record the serial number.
2. Leave the power switch OFF. Connect a known data-capable USB-C cable.
3. Remove the supplied microSD, confirm FAT32, and copy one test image as the
   exact root filename `/flightportrait.bin`:

   ```sh
   python3 tools/make_sd.py \
     --src first-flash/bins/calibration-01-palette.bin \
     --volume /Volumes/FLIGHTPORTRAIT
   diskutil eject /Volumes/FLIGHTPORTRAIT
   ```

4. Reinsert the card fully. Turn the rear power switch ON.
5. Confirm the serial port appears. On macOS:

   ```sh
   find /dev -maxdepth 1 \
     \( -name 'cu.wchusbserial*' -o -name 'cu.usbmodem*' \) \
     -print | sort
   ```

   Run this before and after connecting/powering/waking the unit and select
   the new path explicitly. The carrier's CH34x port may require WCH's macOS
   driver. Logs are 115200 baud on the E1004 UART bridge; prefer the
   `cu.wchusbserial…` port for logs if both forms appear.
6. Upload the SD sketch:

   ```sh
   export ARDUINO_DIRECTORIES_USER="$PWD/.arduino-user"
   arduino-cli upload \
     --fqbn 'esp32:esp32:XIAO_ESP32S3:PSRAM=opi,USBMode=hwcdc,CDCOnBoot=default' \
     -p /dev/cu.wchusbserialXXXX arduino-sd-demo
   ```

7. If upload does not start, hold the rear **Boot** button, tap **Reset**,
   release Boot, and retry. The unit must be awake; Seeed notes that a sleeping
   E1004 will not flash.
8. Open the UART port at 115200. Expect, in order:
   `SD demo boot` → `read 960000 bytes` →
   `translated native palette for Seeed_GxEPD2` → `blit + refresh` → `done`.
   A full refresh takes roughly 30–40 seconds. Do not disconnect power.
   The translation is intentionally confined to the Arduino demo: the
   FlightPortrait `.bin` remains in the native panel encoding documented by
   `docs/PROTOCOL.md`, while Seeed's `writeNative()` path internally expects
   GxEPD2 drawing-palette indexes before converting them back for SPI.
9. Photograph the palette result. Then wait at least 180 seconds between
   refreshes and repeat with:
   - `calibration-02-geometry.bin`: black outer border; red/yellow top
     corners; blue/green bottom corners; uninterrupted center seam.
   - `calibration-03-nibbles.bin`: red/blue one-pixel stripes, reversed in the
     middle, black/white at the bottom. This catches nibble reversal.
   - Optionally, a real poster: render one from your own day's log with
     `tools/build_first_flash_kit.py --logs <YYYY-MM-DD.json>` and
     blit the resulting `portrait-*.bin` through the same swap procedure.

To swap images, replace `/flightportrait.bin`, safely eject, reinsert, then
press the front circular-arrow Refresh control (PCB KEY2/GPIO5). Never remove
the card during a read or the power during a refresh.

## Native firmware second

Only start this after all three calibration images draw correctly.

1. Put the connected ESP32-S3 in download mode and capture its live,
   eFuse-derived Wi-Fi STA MAC:

   ```sh
   esptool.py --chip esp32s3 --port /dev/cu.wchusbserialXXXX read_mac
   ```

   Use this value, not a carton or manually maintained inventory value.
   Enroll that MAC with whatever server the frame will poll: on your own
   server (PROTOCOL.md §5 BYOS), `provision_secret` is whatever your server
   decides it is; on the official cloud, each unit gets its own high-entropy
   credential through the enrollment procedure in PROTOCOL.md §6.

   For a bench-only image, paste that unit's credential into
   `FP_DEV_PROVISION_SECRET`; production uses the encrypted factory-NVS tool
   instead and must leave this blank:

   ```sh
   idf.py set-target esp32s3
   idf.py menuconfig
   # FlightPortrait -> DEV ONLY factory setup credential
   idf.py fullclean
   idf.py build
   ```

2. Put the unit in download mode as above, then:

   ```sh
   idf.py -p /dev/cu.wchusbserialXXXX erase-flash
   idf.py -p /dev/cu.wchusbserialXXXX flash monitor
   ```

3. Expected first boot: scan the on-glass Security-2 QR in the app and send a
   2.4 GHz network, then
   provisioning → WiFi join → `/device/v1/setup` → `/device/v1/display` →
   960,000-byte download → SHA-256 verified → panel refresh → deep sleep.
4. On the server side, confirm the frame's `/setup` and first `/display`
   poll landed with a current last-seen time before the unit goes on the
   wall.

Never run a fresh `/setup` against the MAC of an already provisioned
physical frame: setup intentionally rotates its device token and resets
personal state.

Glass day closes the radio-only gate.

If any checkpoint fails, use [TRIAGE.md](TRIAGE.md) and change one variable at
a time.
