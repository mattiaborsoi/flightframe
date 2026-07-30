/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
// Unit B control sweep — which GPIO is behind which printed key on the EE02.
// Run it from the hardware bring-up runbook, after the panel sanity flash.
//
// Why this exists: sdkconfig.ee02.defaults maps KEY0 (the admin control, whose
// ten-second hold is a DESTRUCTIVE FACTORY RESET) onto a GPIO by inference, not
// measurement. The E1004's printed-label-to-net order already failed to match
// the obvious guess once. Measure before any firmware acts on it.
//
// This sketch never touches the panel bus. GPIO 4/7/9/10/38/41/43/44 are the
// display's BUSY/SCK/MOSI/DC/RST/CS_S/EN/CS_M and are deliberately excluded
// from the scan -- driving a pull-up onto EN or a chip-select while the panel
// is attached is exactly the mistake this file exists to avoid. Nothing here
// refreshes the glass, so the minimum refresh interval does not apply and
// whatever is currently displayed stays put.
//
// From the firmware root:
//   export ARDUINO_DIRECTORIES_USER="$PWD/.arduino-user"
//   arduino-cli compile --fqbn \
//     'esp32:esp32:XIAO_ESP32S3_Plus:PSRAM=opi,USBMode=hwcdc,CDCOnBoot=default' \
//     arduino-ee02-keysweep

// Candidate pins: everything plausibly free on a XIAO ESP32-S3 Plus once the
// panel bus, native USB (19/20), and the octal flash/PSRAM block (26-37) are
// removed. GPIO0 is the XIAO's own BOOT button and is included on purpose: it
// is a known-good control, so seeing it respond proves the sweep itself works
// before you trust anything it says about the unknown keys.
static const uint8_t CANDIDATES[] = {
  0, 1, 2, 3, 5, 6, 8, 11, 12, 13, 14, 15,
  16, 17, 18, 21, 39, 40, 42, 45, 46, 47, 48,
};
static const size_t N = sizeof(CANDIDATES) / sizeof(CANDIDATES[0]);

static bool last[N];

void setup()
{
  Serial.begin(115200);
  delay(2000);                 // USB CDC needs a moment to enumerate
  Serial.println();
  Serial.println("[flightportrait] EE02 control sweep");
  Serial.println("Press and release each control in turn. Suggested order:");
  Serial.println("  1. the XIAO's B button   (expect GPIO0 - proves the sweep works)");
  Serial.println("  2. KEY1");
  Serial.println("  3. KEY2");
  Serial.println("  4. KEY3");
  Serial.println("Record which GPIO each printed label pulls low.");
  Serial.println("RESET is not a GPIO and will not appear here.");
  Serial.println();

  for (size_t i = 0; i < N; i++) {
    pinMode(CANDIDATES[i], INPUT_PULLUP);
  }
  delay(50);

  // Baseline. Anything already low at rest is not a key -- it is a pin held by
  // something else on the board, and pressing buttons will not change it.
  bool any_low = false;
  for (size_t i = 0; i < N; i++) {
    last[i] = digitalRead(CANDIDATES[i]);
    if (!last[i]) {
      Serial.printf("[baseline] GPIO%-2u is LOW at rest - not a key\n",
                    CANDIDATES[i]);
      any_low = true;
    }
  }
  if (!any_low) Serial.println("[baseline] all candidates high at rest");
  Serial.println("[ready] press a control now");
}

void loop()
{
  for (size_t i = 0; i < N; i++) {
    bool now = digitalRead(CANDIDATES[i]);
    if (now == last[i]) continue;
    delay(25);                                   // debounce
    if (digitalRead(CANDIDATES[i]) != now) continue;
    Serial.printf("GPIO%-2u %s\n", CANDIDATES[i], now ? "released" : "PRESSED");
    last[i] = now;
  }
  delay(10);
}
