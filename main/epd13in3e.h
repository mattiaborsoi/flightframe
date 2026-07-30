/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Native ESP-IDF driver for the 13.3" E Ink Spectra 6 (E) panel,
 * dual-controller (master = left 600 px, slave = right 600 px).
 *
 * Register sequence ported from the Waveshare EPD_13in3e reference code
 * (carries an MIT-style permission notice); wiring/timing semantics per
 * the Seeed reTerminal E1004 (4-wire SPI: DC low = command byte, per-chip
 * CS, BUSY low = busy). Buffer format: PROTOCOL.md §1.
 */
#pragma once
#include <stdint.h>

#include "esp_err.h"

#define EPD_WIDTH        1200
#define EPD_HEIGHT       1600
#define EPD_BYTES        (EPD_WIDTH * EPD_HEIGHT / 2)   /* 960,000 */

esp_err_t epd_init(void);                   /* bus + GPIO + panel registers */
esp_err_t epd_blit(const uint8_t *buf);     /* EPD_BYTES; DTM+PON+DRF+POF  */
void epd_sleep(void);                       /* panel deep sleep, power off */
