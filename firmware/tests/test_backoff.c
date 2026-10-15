/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Host-side unit test for the one firmware behavior testable without
 * hardware: the backoff curve (PROTOCOL.md §3, min(2^n * 5 min, 6 h)).
 * This math is what stands between "failed poll" and "dead battery" —
 * it gets a real test, not a code review.
 *
 *   cc main/backoff.c tests/test_backoff.c -o /tmp/tb && /tmp/tb
 */
#include <assert.h>
#include <stdio.h>

#include "../main/backoff.h"

int main(void)
{
    assert(fp_backoff_seconds(0) == 300);      /* 5 min  */
    assert(fp_backoff_seconds(1) == 600);
    assert(fp_backoff_seconds(2) == 1200);
    assert(fp_backoff_seconds(3) == 2400);
    assert(fp_backoff_seconds(4) == 3600);     /* 4800 capped to 1 h  */
    assert(fp_backoff_seconds(5) == 3600);
    assert(fp_backoff_seconds(7) == 3600);
    assert(fp_backoff_seconds(8) == 3600);
    assert(fp_backoff_seconds(255) == 3600);   /* no overflow at max  */
    printf("backoff: all cases pass\n");
    return 0;
}
