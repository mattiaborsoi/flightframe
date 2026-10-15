/* SPDX-FileCopyrightText: 2026 flightframe
 * SPDX-License-Identifier: Apache-2.0 */
#include <assert.h>
#include <stddef.h>
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
    /* The clock fallback: an HTTP Date header, floored at the build date. */
    int64_t t = 0;
    assert(fp_http_date_parse("Thu, 15 Oct 2026 16:40:00 GMT", &t));
    assert(t == 1792082400);
    assert(fp_http_date_parse("Thu, 01 Jan 1970 00:00:00 GMT", &t) && t == 0);
    assert(fp_http_date_parse("Tue, 29 Feb 2028 23:59:59 GMT", &t));
    assert(t == 1835481599);
    assert(!fp_http_date_parse("Thu, 15 Oct 2026 16:40:00 CET", &t));
    assert(!fp_http_date_parse("Thu, 31 Sep 2026 10:00:00 GMT", &t));
    assert(!fp_http_date_parse("Thu, 15 Foo 2026 10:00:00 GMT", &t));
    assert(!fp_http_date_parse("Thu, 15 Oct 2026 24:00:00 GMT", &t));
    assert(!fp_http_date_parse("Thursday, 15-Oct-26 16:40:00 GMT", &t));
    assert(!fp_http_date_parse("", &t));
    assert(!fp_http_date_parse(NULL, &t));

    int64_t floor = 0;
    assert(fp_build_date_parse("Oct 15 2026", &floor));
    assert(floor == 1792022400);
    assert(fp_build_date_parse("Oct  5 2026", &t) && t == 1791158400);
    assert(!fp_build_date_parse("Oct 32 2026", &t));
    assert(!fp_build_date_parse("garbage", &t));

    assert(fp_clock_plausible(1792082400, floor));
    assert(fp_clock_plausible(floor, floor));
    assert(!fp_clock_plausible(floor - 1, floor));     /* pushed into the past */
    assert(!fp_clock_plausible(0, floor));
    assert(!fp_clock_plausible(floor + (int64_t)21 * 366 * 86400, floor));
    assert(!fp_clock_plausible(1792082400, 0));        /* no floor, no trust */

    return 0;
}
