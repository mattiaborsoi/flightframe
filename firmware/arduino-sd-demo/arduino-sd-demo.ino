/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
// FlightPortrait day-one unboxing sketch — reTerminal E1004.
// Reads /flightportrait.bin (960,000 bytes, format: ../docs/PROTOCOL.md §1) from
// the microSD card and blits it to the 13.3" Spectra 6 panel. No network.
//
// Board settings (Arduino IDE, Tools menu):
//   Board:  XIAO_ESP32S3        PSRAM: OPI PSRAM   (mandatory: 937 KB buffer)
//   USB CDC On Boot: Enabled. E1004 carrier logs are also on UART0.
//
// Library: Seeed_GxEPD2 (https://github.com/Seeed-Projects/Seeed_GxEPD2)
// PLUS: copy GxEPD2_T133A01_1200x1600.{h,cpp} from that repo's
// examples/GxEPD2_reTerminal_E1004/ into THIS sketch folder (the 13.3"
// dual-chip driver ships inside the example, not the library core).
// Seeed_GxEPD2 is GPL-3.0 — fine for this bench demo, which is why the
// driver files are fetched locally and never committed to this repo.
//
// SD prep:  python3 ../tools/make_sd.py     (drops the latest render on SD)
//
// Buttons: the E1004 circular-arrow Refresh control (KEY2/GPIO5) re-reads
// the card and refreshes (e.g. after swapping SD).

#include <SPI.h>
#include <SD.h>
#include <GxEPD2_7C.h>
#include "GxEPD2_T133A01_1200x1600.h"

// ===== reTerminal E1004 pin map (Seeed wiki + Setup523) =====
#define EPD_SCK_PIN     7
#define EPD_MISO_PIN    8
#define EPD_MOSI_PIN    9
#define EPD_CS_PIN     10
#define EPD_DC_PIN     11
#define EPD_ENABLE_PIN 12
#define EPD_BUSY_PIN   13
#define EPD_RES_PIN    38
#define EPD_CS1_PIN     2      // second controller (right half)

#define SD_CS_PIN      14
#define SD_DET_PIN     15
#define SD_EN_PIN      16      // SD power enable

#define BUTTON_REFRESH  5

static const uint16_t W = 1200;
static const uint16_t H = 1600;
static const uint32_t BIN_SIZE = 960000UL;   // W * H / 2
static const char *BIN_PATH = "/flightportrait.bin";

SPIClass hspi(HSPI);
#define DBG Serial0

#define MAX_DISPLAY_BUFFER_SIZE 24000u
#define MAX_HEIGHT(EPD)                                        \
    (EPD::HEIGHT <= (MAX_DISPLAY_BUFFER_SIZE) / (EPD::WIDTH / 2) \
         ? EPD::HEIGHT                                          \
         : (MAX_DISPLAY_BUFFER_SIZE) / (EPD::WIDTH / 2))

GxEPD2_7C<GxEPD2_T133A01_1200x1600, MAX_HEIGHT(GxEPD2_T133A01_1200x1600)>
    display(GxEPD2_T133A01_1200x1600(EPD_CS_PIN, EPD_DC_PIN, EPD_RES_PIN,
                                     EPD_BUSY_PIN, EPD_CS1_PIN,
                                     EPD_ENABLE_PIN));

uint8_t *frame = nullptr;      // 960 KB in PSRAM

// The FlightPortrait wire format stores the panel's native Spectra 6 codes.
// Seeed's T133A01 driver, despite naming its input writeNative(), stores
// GxEPD2 drawing-palette indexes and converts them to native codes during the
// SPI transfer. Translate once after the SD read so that second conversion
// produces the original protocol code:
//   native K/W/Y/R/B/G 0/1/2/3/5/6 -> GxEPD2 0/1/5/4/3/2.
// Do not move this mapping into the renderer or change PROTOCOL.md codes.
static bool translateNativeBufferForGxEPD()
{
  static const uint8_t native_to_gx[16] = {
    0x0, 0x1, 0x5, 0x4, 0xFF, 0x3, 0x2, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF
  };
  for (uint32_t i = 0; i < BIN_SIZE; i++) {
    uint8_t hi = native_to_gx[(frame[i] >> 4) & 0x0F];
    uint8_t lo = native_to_gx[frame[i] & 0x0F];
    if (hi == 0xFF || lo == 0xFF) {
      DBG.printf("[sd] invalid panel code at byte %lu\n", (unsigned long)i);
      return false;
    }
    frame[i] = (hi << 4) | lo;
  }
  DBG.println("[sd] translated native palette for Seeed_GxEPD2");
  return true;
}

static bool loadBin()
{
  if (!SD.exists(BIN_PATH)) {
    DBG.printf("[sd] %s not found\n", BIN_PATH);
    return false;
  }
  File f = SD.open(BIN_PATH, FILE_READ);
  if (!f || f.size() != BIN_SIZE) {
    DBG.printf("[sd] bad file: size %lu, want %lu\n",
                  f ? (unsigned long)f.size() : 0UL,
                  (unsigned long)BIN_SIZE);
    if (f) f.close();
    return false;
  }
  uint32_t got = 0;
  while (got < BIN_SIZE) {
    int n = f.read(frame + got, min((uint32_t)4096, BIN_SIZE - got));
    if (n <= 0) break;
    got += n;
  }
  f.close();
  DBG.printf("[sd] read %lu bytes\n", (unsigned long)got);
  return got == BIN_SIZE && translateNativeBufferForGxEPD();
}

static void blit()
{
  // loadBin() has translated the protocol-native nibbles to the drawing
  // indexes expected by Seeed's writeNative(); the driver converts those
  // indexes back to panel-native codes during the per-controller SPI send.
  DBG.println("[epd] blit + refresh (~30 s, don't power off)");
  display.epd2.writeNative(frame, nullptr, 0, 0, W, H, false, false, false);
  display.epd2.refresh(false);
  display.epd2.hibernate();
  DBG.println("[epd] done");
}

void setup()
{
  Serial.begin(115200);
  DBG.begin(115200);
  delay(300);
  DBG.println("[flightportrait] SD demo boot");

  pinMode(BUTTON_REFRESH, INPUT_PULLUP);
  pinMode(SD_EN_PIN, OUTPUT);
  digitalWrite(SD_EN_PIN, HIGH);   // power the SD slot
  delay(50);

  frame = (uint8_t *)ps_malloc(BIN_SIZE);
  if (!frame) {
    DBG.println("[mem] ps_malloc failed — is PSRAM set to OPI PSRAM?");
    return;
  }

  hspi.begin(EPD_SCK_PIN, EPD_MISO_PIN, EPD_MOSI_PIN, -1);
  if (!SD.begin(SD_CS_PIN, hspi)) {
    DBG.println("[sd] mount failed (card inserted? FAT32? <=32GB?)");
    return;
  }

  display.epd2.selectSPI(hspi, SPISettings(10000000, MSBFIRST, SPI_MODE0));
  display.init(115200);

  if (loadBin()) blit();
}

void loop()
{
  if (digitalRead(BUTTON_REFRESH) == LOW) {   // re-read card, refresh
    delay(50);                             // debounce
    while (digitalRead(BUTTON_REFRESH) == LOW) delay(10);
    DBG.println("[refresh] reload requested");
    if (loadBin()) blit();
  }
  delay(20);
}
