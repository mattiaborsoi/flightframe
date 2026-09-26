/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Host-side unit test for the panel's refresh-spacing arithmetic.
 *
 * The panel is the part of this device that wears out, and this math is what
 * protects it — while also being what decides whether a press of the refresh
 * button appears to do anything. Both directions get a real test.
 *
 *   cc main/panel_guard.c tests/test_panel_guard.c \
 *      -o /tmp/tp && /tmp/tp
 */
#include <assert.h>
#include <stdio.h>

#include "../main/panel_guard.h"

static void plan_now_when_the_guard_is_clear(void)
{
    uint32_t wait = 12345;
    assert(fp_panel_guard_plan(0, false, 90, &wait) == FP_PANEL_DRAW_NOW);
    assert(wait == 0);              /* always written, never left stale */
}

static void plan_waits_out_the_remainder_instead_of_discarding(void)
{
    /* The old behaviour dropped the image and returned an error, so a press
     * inside the spacing window did nothing at all. The image is already in
     * PSRAM by this point — waiting is what turns a press into a redraw. */
    uint32_t wait = 0;
    assert(fp_panel_guard_plan(40, false, 90, &wait)
           == FP_PANEL_DRAW_AFTER_WAIT);
    assert(wait == 40);

    assert(fp_panel_guard_plan(60, false, 90, &wait)
           == FP_PANEL_DRAW_AFTER_WAIT);
    assert(wait == 60);             /* the whole default spacing fits */
}

static void plan_clamps_a_wait_it_cannot_afford(void)
{
    /* Raise the spacing past the awake budget and the wait is clamped; the
     * caller sees it is short and defers rather than half-honouring it. */
    uint32_t wait = 0;
    assert(fp_panel_guard_plan(180, false, 90, &wait)
           == FP_PANEL_DRAW_AFTER_WAIT);
    assert(wait == 90);
    assert(wait < 180);

    /* A zero budget is legal and means "never wait, always defer". */
    assert(fp_panel_guard_plan(1, false, 0, &wait)
           == FP_PANEL_DRAW_AFTER_WAIT);
    assert(wait == 0);
}

static void a_blit_in_progress_outranks_everything(void)
{
    /* Blitting over a running blit drives the panel's rails through two
     * overlapping power sequences. It must lose to nothing, including a
     * clear guard. */
    uint32_t wait = 7;
    assert(fp_panel_guard_plan(0, true, 90, &wait) == FP_PANEL_DRAW_BUSY);
    assert(wait == 0);
    assert(fp_panel_guard_plan(30, true, 90, &wait) == FP_PANEL_DRAW_BUSY);
    assert(wait == 0);
}

static void awake_time_counts_down_and_saturates(void)
{
    assert(fp_panel_guard_after_awake(60, 0) == 60);
    assert(fp_panel_guard_after_awake(60, 25) == 35);
    assert(fp_panel_guard_after_awake(60, 60) == 0);
    assert(fp_panel_guard_after_awake(60, 5000) == 0);   /* never wraps */
    assert(fp_panel_guard_after_awake(0, 5) == 0);
}

static void only_a_timer_wake_may_claim_the_planned_sleep(void)
{
    /* A timer wake really did sleep the whole planned interval. */
    assert(fp_panel_guard_after_sleep(60, 40, true) == 20);
    assert(fp_panel_guard_after_sleep(60, 60, true) == 0);
    assert(fp_panel_guard_after_sleep(60, 99999, true) == 0);

    /* A button wake happened at an unknown earlier moment. Crediting the
     * planned sleep would let repeated presses walk the guard to zero and
     * refresh the panel as fast as someone can press — exactly the wear the
     * guard exists to prevent. */
    assert(fp_panel_guard_after_sleep(60, 40, false) == 60);
    assert(fp_panel_guard_after_sleep(60, 99999, false) == 60);
}

static void presses_cannot_walk_the_guard_down(void)
{
    /* Five impatient presses, each waking early and each costing only the
     * seconds actually spent awake. */
    uint32_t remaining = 60;
    for (int i = 0; i < 5; ++i) {
        remaining = fp_panel_guard_after_sleep(remaining, 3600, false);
        remaining = fp_panel_guard_after_awake(remaining, 4);  /* 4 s awake */
    }
    assert(remaining == 40);        /* 60 - 5*4, not 0 */
}

int main(void)
{
    plan_now_when_the_guard_is_clear();
    plan_waits_out_the_remainder_instead_of_discarding();
    plan_clamps_a_wait_it_cannot_afford();
    a_blit_in_progress_outranks_everything();
    awake_time_counts_down_and_saturates();
    only_a_timer_wake_may_claim_the_planned_sleep();
    presses_cannot_walk_the_guard_down();
    printf("panel_guard: all cases pass\n");
    return 0;
}
