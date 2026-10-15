/* SPDX-FileCopyrightText: 2026 flightframe
 * SPDX-License-Identifier: Apache-2.0 */
#include <assert.h>
#include <stdio.h>

#include "../main/frame_policy.h"

int main(void)
{
    /* From sleep: a tap fetches, a 3 s hold opens the Wi-Fi page. */
    assert(fp_gesture_classify(true, 0, 3000, 3000) == FP_BUTTON_POLL);
    assert(fp_gesture_classify(true, 2999, 3000, 3000) == FP_BUTTON_POLL);
    assert(fp_gesture_classify(true, 3000, 3000, 3000) == FP_BUTTON_WIFI);
    assert(fp_gesture_classify(true, 60000, 3000, 3000) == FP_BUTTON_WIFI);

    /* A long hold from sleep is never destructive. Factory reset needs
     * the reset button, so it cannot happen by leaning on the frame. */
    assert(fp_gesture_classify(true, 20000, 3000, 3000) != FP_BUTTON_FACTORY_RESET);

    /* At reset/power-on: only a key held through boot resets. */
    assert(fp_gesture_classify(false, 0, 3000, 3000) == FP_BUTTON_NONE);
    assert(fp_gesture_classify(false, 2999, 3000, 3000) == FP_BUTTON_NONE);
    assert(fp_gesture_classify(false, 3000, 3000, 3000) ==
           FP_BUTTON_FACTORY_RESET);

    /* Zero disables a gesture rather than making it hair-trigger. */
    assert(fp_gesture_classify(true, 9999, 0, 3000) == FP_BUTTON_POLL);
    assert(fp_gesture_classify(false, 9999, 3000, 0) == FP_BUTTON_NONE);

    /* The upstream repair and re-provision gestures are unreachable: they
     * belong to a vendor cloud this deployment does not use. */
    for (uint32_t ms = 0; ms <= 20000; ms += 250) {
        for (int s = 0; s <= 1; ++s) {
            fp_button_action_t a = fp_gesture_classify(s, ms, 3000, 3000);
            assert(a != FP_BUTTON_REPAIR && a != FP_BUTTON_REPROVISION);
        }
    }

    /* Attention after a day of failures, never before; zero disables. */
    assert(!fp_auto_portal_due(0, 24));
    assert(!fp_auto_portal_due(23, 24));
    assert(fp_auto_portal_due(24, 24));
    assert(fp_auto_portal_due(255, 24));
    assert(!fp_auto_portal_due(255, 0));

    /* Firmware offers: no repeats, no retry loop after a rollback. */
    assert(fp_fw_offer_acceptable("1.0.1", "1.0.0", ""));
    assert(!fp_fw_offer_acceptable("1.0.0", "1.0.0", ""));
    assert(!fp_fw_offer_acceptable("1.0.2", "1.0.1", "1.0.2"));
    assert(fp_fw_offer_acceptable("1.0.3", "1.0.1", "1.0.2"));
    assert(!fp_fw_offer_acceptable("", "1.0.0", ""));
    assert(!fp_fw_offer_acceptable(NULL, "1.0.0", ""));
    assert(fp_fw_offer_acceptable("1.0.1", "1.0.0", NULL));

    printf("frame_policy: all cases pass\n");
    return 0;
}
