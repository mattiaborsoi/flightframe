/* SPDX-FileCopyrightText: 2026 flightframe
 * SPDX-License-Identifier: Apache-2.0 */
#include "frame_policy.h"

#include <string.h>

fp_button_action_t fp_gesture_classify(bool from_sleep, uint32_t held_ms,
                                       uint32_t wifi_hold_ms,
                                       uint32_t reset_combo_ms)
{
    if (from_sleep) {
        if (wifi_hold_ms != 0 && held_ms >= wifi_hold_ms) {
            return FP_BUTTON_WIFI;
        }
        return FP_BUTTON_POLL;
    }
    /* Reset or power-on with a key down. Only a deliberate hold through
     * boot counts; a key brushed while plugging in USB does nothing. */
    if (reset_combo_ms != 0 && held_ms >= reset_combo_ms) {
        return FP_BUTTON_FACTORY_RESET;
    }
    return FP_BUTTON_NONE;
}

bool fp_auto_portal_due(uint8_t consecutive_failures, uint8_t threshold)
{
    return threshold != 0 && consecutive_failures >= threshold;
}

bool fp_fw_offer_acceptable(const char *offered, const char *running,
                            const char *rejected)
{
    if (!offered || !offered[0]) {
        return false;
    }
    if (running && strcmp(offered, running) == 0) {
        return false;
    }
    if (rejected && rejected[0] && strcmp(offered, rejected) == 0) {
        return false;
    }
    return true;
}
