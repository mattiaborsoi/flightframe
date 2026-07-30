/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdint.h>

#include "esp_err.h"

/* Blit a verified 960,000-byte buffer to the 13.3" Spectra 6 panel:
 * per-row master/slave split per PROTOCOL.md §1, ~12-30 s refresh.
 *
 * Firmware defends the panel whatever the server asks for: it enforces
 * CONFIG_FP_MIN_REFRESH_SPACING_S between refreshes, and never starts a blit
 * while one is running. When the spacing is the only thing in the way it
 * WAITS it out and then draws (up to CONFIG_FP_MAX_GUARD_WAIT_S) rather than
 * discarding an image already in hand — see panel_guard.h.
 *
 * ESP_ERR_INVALID_STATE: a blit is already running.
 * ESP_ERR_TIMEOUT:       the spacing outlasts the awake budget; the caller
 *                        must NOT record the hash, so the next wake redraws.
 */
esp_err_t fp_panel_draw(const uint8_t *buf);

/* Deep-sleep-safe refresh spacing. esp_timer restarts on every boot, so the
 * retained guard stores a duration, never a cross-boot esp_timer timestamp. */
void fp_panel_on_boot(void);
void fp_panel_before_sleep(uint32_t planned_sleep_s);
uint32_t fp_panel_wait_seconds(void);
