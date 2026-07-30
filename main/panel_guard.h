/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* The panel's refresh-spacing arithmetic, with no ESP-IDF in it.
 *
 * The panel is the one part of this device that wears out, and this is what
 * protects it. It lives apart from panel.c so it can be reasoned about and
 * tested on a host: panel.c owns the retained state and the SPI, this owns
 * the decisions.
 *
 * The spacing is a build option (CONFIG_FP_MIN_REFRESH_SPACING_S). Know the
 * panel's own numbers before changing it: the maker suggests updating ONCE A
 * DAY, and their published reliability testing ran at 150 s intervals.
 * Anything below 150 s is off the tested envelope — fine for a demo table
 * under supervision, a choice to make deliberately for frames that go on a
 * customer's wall for years.
 */

typedef enum {
    FP_PANEL_DRAW_NOW,        /* guard clear: blit immediately         */
    FP_PANEL_DRAW_AFTER_WAIT, /* wait out the remainder, then blit     */
    FP_PANEL_DRAW_BUSY,       /* a blit is already running: do nothing */
} fp_panel_draw_plan_t;

/* What to do with an image that is ready to go on the glass.
 *
 * `wait_s` is set for FP_PANEL_DRAW_AFTER_WAIT and zeroed otherwise. Waiting
 * is bounded by `max_wait_s`: past that the answer is still AFTER_WAIT with
 * wait_s clamped, because the caller holds the image in PSRAM and PSRAM does
 * not survive deep sleep — dropping the image to sleep it out would mean
 * downloading 960 KB again for the same picture. */
fp_panel_draw_plan_t fp_panel_guard_plan(uint32_t remaining_s, bool drawing,
                                         uint32_t max_wait_s,
                                         uint32_t *wait_s);

/* Guard left after `elapsed_s` of being awake. Saturates at zero. */
uint32_t fp_panel_guard_after_awake(uint32_t remaining_s, uint32_t elapsed_s);

/* Guard left after a deep sleep.
 *
 * Only a timer wake may claim the full planned sleep: a button wake happens
 * at an unknown earlier moment, and crediting the whole planned interval
 * would let an impatient press walk the guard down to nothing — which is
 * precisely the wear this exists to prevent. */
uint32_t fp_panel_guard_after_sleep(uint32_t remaining_s,
                                    uint32_t planned_sleep_s,
                                    bool timer_wake);
