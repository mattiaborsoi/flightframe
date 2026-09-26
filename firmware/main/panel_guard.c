/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "panel_guard.h"

fp_panel_draw_plan_t fp_panel_guard_plan(uint32_t remaining_s, bool drawing,
                                         uint32_t max_wait_s,
                                         uint32_t *wait_s)
{
    if (wait_s) {
        *wait_s = 0;
    }
    /* A blit takes tens of seconds and drives the panel's rails through a
     * strict power sequence. Starting a second one over the top of it is the
     * one thing that must never happen, so it outranks the spacing check. */
    if (drawing) {
        return FP_PANEL_DRAW_BUSY;
    }
    if (remaining_s == 0) {
        return FP_PANEL_DRAW_NOW;
    }
    if (wait_s) {
        *wait_s = remaining_s < max_wait_s ? remaining_s : max_wait_s;
    }
    return FP_PANEL_DRAW_AFTER_WAIT;
}

uint32_t fp_panel_guard_after_awake(uint32_t remaining_s, uint32_t elapsed_s)
{
    return elapsed_s >= remaining_s ? 0u : remaining_s - elapsed_s;
}

uint32_t fp_panel_guard_after_sleep(uint32_t remaining_s,
                                    uint32_t planned_sleep_s,
                                    bool timer_wake)
{
    if (!timer_wake) {
        return remaining_s;
    }
    return planned_sleep_s >= remaining_s ? 0u : remaining_s - planned_sleep_s;
}
