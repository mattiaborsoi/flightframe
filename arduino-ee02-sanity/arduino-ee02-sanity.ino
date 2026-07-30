/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
// Unit B panel sanity flash — bare 13.3" Spectra 6 glass on a XIAO ePaper
// Display Board EE02 (XIAO ESP32-S3 Plus). Run it from the hardware
// bring-up runbook, as the first step that touches Unit B's glass.
//
// This is the known-good third-party test, not our firmware. It draws six
// full-width ink bands from a synthesized buffer: no SD card, no network, no
// file format, no palette translation. The only things under test are PSRAM,
// SPI, both panel controllers, the FPC seat, and the glass. Keep it that way —
// every variable added here is a variable you debug at the bench.
//
// Success: six distinct bands (black, white, yellow, red, blue, green) across
// the full 1200x1600, both controller halves, uninterrupted black border, no
// tearing, after roughly 19-30 s. Judge against the Unit A photographs from
// the Unit A reference photographs — same glass, so a difference is Unit B's.
//
// Board settings:
//   Board: XIAO_ESP32S3_Plus     PSRAM: OPI PSRAM     USB CDC On Boot: Enabled
// PSRAM defaults to *Disabled* on the Plus. Without it the 937 KB allocation
// fails and nothing draws.
//
// From the firmware root:
//   export ARDUINO_DIRECTORIES_USER="$PWD/.arduino-user"
//   arduino-cli compile --fqbn \
//     'esp32:esp32:XIAO_ESP32S3_Plus:PSRAM=opi,USBMode=hwcdc,CDCOnBoot=default' \
//     arduino-ee02-sanity
//
// Library: Seeed_GxEPD2. PLUS: copy GxEPD2_T133A01_1200x1600.{h,cpp} from that
// repo's examples/GxEPD2_reTerminal_E1004/ into THIS sketch folder. The panel is
// the same T133A01 glass as the E1004, so the driver carries over even though
// the carrier does not. Seeed_GxEPD2 is GPL-3.0, so those files are fetched
// locally and never committed.

#include <SPI.h>
#include <GxEPD2_7C.h>
#include "GxEPD2_T133A01_1200x1600.h"

// ===== EE02 pin map — NOT the E1004's =====
// Seeed_GFX User_Setups/EPaper_Board_Pins_Setups.h, guard
// USE_XIAO_EPAPER_DISPLAY_BOARD_EE02. The `// D7`/`// D8` comments in that
// header are stale; numeric values are authoritative. D-pin -> GPIO from core
// 3.3.10 variants/XIAO_ESP32S3_Plus/pins_arduino.h (D8=7, D10=9).
#define EPD_SCK_PIN     7
#define EPD_MISO_PIN   -1      // EE02 brings no MISO to the panel
#define EPD_MOSI_PIN    9
#define EPD_CS_PIN     44      // primary controller (left half)
#define EPD_CS1_PIN    41      // second controller (right half)
#define EPD_DC_PIN     10
#define EPD_RES_PIN    38
#define EPD_BUSY_PIN    4      // active low
#define EPD_ENABLE_PIN 43      // panel power, active high

// CS (44) and ENABLE (43) are UART0 RX/TX on the ESP32-S3. Logging to Serial0
// here would drive chip-select and panel power directly, and the panel would
// come up dead or half-dead looking exactly like a bad FPC seat. Log over USB
// CDC only. Do not add a Serial0.begin() to this sketch.
#define DBG Serial

static const uint16_t W = 1200;
static const uint16_t H = 1600;
static const uint32_t BUF_SIZE = 960000UL;   // W * H / 2, 4 bpp packed

SPIClass hspi(HSPI);

#define MAX_DISPLAY_BUFFER_SIZE 24000u
#define MAX_HEIGHT(EPD)                                        \
    (EPD::HEIGHT <= (MAX_DISPLAY_BUFFER_SIZE) / (EPD::WIDTH / 2) \
         ? EPD::HEIGHT                                          \
         : (MAX_DISPLAY_BUFFER_SIZE) / (EPD::WIDTH / 2))

GxEPD2_7C<GxEPD2_T133A01_1200x1600, MAX_HEIGHT(GxEPD2_T133A01_1200x1600)>
    display(GxEPD2_T133A01_1200x1600(EPD_CS_PIN, EPD_DC_PIN, EPD_RES_PIN,
                                     EPD_BUSY_PIN, EPD_CS1_PIN,
                                     EPD_ENABLE_PIN));

uint8_t *frame = nullptr;      // 937 KB in PSRAM

// Seeed's writeNative() takes GxEPD2 *drawing* indexes and converts them to
// native Spectra 6 codes during the SPI send. This sketch synthesizes drawing
// indexes directly, so there is deliberately no protocol translation here —
// PROTOCOL.md's native codes are not involved in a panel sanity test.
enum : uint8_t {
  GX_BLACK  = 0,
  GX_WHITE  = 1,
  GX_GREEN  = 2,
  GX_BLUE   = 3,
  GX_RED    = 4,
  GX_YELLOW = 5,
};

static const uint8_t BANDS[6] = {
  GX_BLACK, GX_WHITE, GX_YELLOW, GX_RED, GX_BLUE, GX_GREEN
};
static const char *BAND_NAMES[6] = {
  "black", "white", "yellow", "red", "blue", "green"
};

static inline void setPixel(uint16_t x, uint16_t y, uint8_t gx)
{
  uint32_t i = ((uint32_t)y * W + x) >> 1;
  if (x & 1) frame[i] = (frame[i] & 0xF0) | (gx & 0x0F);
  else       frame[i] = (frame[i] & 0x0F) | ((gx & 0x0F) << 4);
}

static void paint()
{
  // Six full-width horizontal bands. Full width is the point: a dead or
  // miswired chip-select shows up as one half of every band being wrong,
  // which is unmistakable and is the failure this test exists to catch.
  const uint16_t band_h = H / 6;
  for (uint16_t y = 0; y < H; y++) {
    uint16_t b = y / band_h;
    if (b > 5) b = 5;                       // last band absorbs the remainder
    uint8_t gx = BANDS[b];
    uint8_t packed = (uint8_t)((gx << 4) | gx);
    memset(frame + ((uint32_t)y * W) / 2, packed, W / 2);
  }

  // 8 px black border. Drawn last and continuous across the seam: if the two
  // controllers disagree on geometry, the border steps at x=600 and says so.
  for (uint16_t y = 0; y < H; y++)
    for (uint16_t x = 0; x < W; x++)
      if (x < 8 || x >= W - 8 || y < 8 || y >= H - 8)
        setPixel(x, y, GX_BLACK);

  for (int b = 0; b < 6; b++)
    DBG.printf("[paint] band %d: %s\n", b, BAND_NAMES[b]);
}

void setup()
{
  DBG.begin(115200);
  delay(2000);                 // USB CDC needs a moment to enumerate
  DBG.println("[flightportrait] EE02 panel sanity boot");

  frame = (uint8_t *)ps_malloc(BUF_SIZE);
  if (!frame) {
    DBG.println("[mem] ps_malloc failed - set PSRAM to OPI PSRAM on "
                "XIAO_ESP32S3_Plus (it defaults to Disabled)");
    return;
  }
  DBG.printf("[mem] %lu-byte framebuffer in PSRAM\n",
             (unsigned long)BUF_SIZE);

  paint();

  hspi.begin(EPD_SCK_PIN, EPD_MISO_PIN, EPD_MOSI_PIN, -1);
  display.epd2.selectSPI(hspi, SPISettings(10000000, MSBFIRST, SPI_MODE0));
  display.init(115200);

  DBG.println("[epd] blit + refresh (~19-30 s, do not power off)");
  display.epd2.writeNative(frame, nullptr, 0, 0, W, H, false, false, false);
  display.epd2.refresh(false);
  display.epd2.hibernate();
  DBG.println("[epd] done - expect six bands, both halves, unbroken border");
}

void loop()
{
  // Deliberately empty. No button handling: the EE02 key GPIOs are still
  // unverified, and the panel needs its minimum spacing between refreshes
  // (CONFIG_FP_MIN_REFRESH_SPACING_S in the ESP-IDF firmware; never go below
  // the 150 s the vendor reliability-tested without meaning to).
  // Power-cycle to repeat the test.
  delay(1000);
}
