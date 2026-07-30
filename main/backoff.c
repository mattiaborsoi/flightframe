/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "backoff.h"

#define BACKOFF_BASE_S (5u * 60u)
#define BACKOFF_MAX_S  (6u * 3600u)

uint32_t fp_backoff_seconds(uint8_t n)
{
    /* 2^n saturates well before the cap: n >= 7 -> 640 min > 6 h. */
    if (n >= 7) {
        return BACKOFF_MAX_S;
    }
    uint32_t s = BACKOFF_BASE_S << n;
    return s > BACKOFF_MAX_S ? BACKOFF_MAX_S : s;
}
