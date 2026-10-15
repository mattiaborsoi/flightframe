/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "backoff.h"

#define BACKOFF_BASE_S (5u * 60u)
/* flightframe: 1 h, not 6 h. A frame that fails for a transient reason (a
 * router reboot, a server restart) should not then sit on a stale poster
 * for hours after the cause is gone. */
#define BACKOFF_MAX_S  (1u * 3600u)

uint32_t fp_backoff_seconds(uint8_t n)
{
    /* n >= 7 is far past the cap whatever the cap is; short-circuit it so
     * the shift below can never overflow. */
    if (n >= 7) {
        return BACKOFF_MAX_S;
    }
    uint32_t s = BACKOFF_BASE_S << n;
    return s > BACKOFF_MAX_S ? BACKOFF_MAX_S : s;
}
