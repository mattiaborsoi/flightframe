/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/*
 * Host test:
 *   cc -Wall -Wextra -Werror main/provisioning_contract.c \
 *      tests/test_provisioning_contract.c -o /tmp/tprovision \
 *      && /tmp/tprovision
 */
#include <assert.h>
#include <stdio.h>

#include "../main/provisioning_contract.h"

int main(void)
{
    assert(fp_wifi_attempt_action(false, false, false, false) ==
           FP_WIFI_ATTEMPT_WAIT);
    assert(fp_wifi_attempt_action(true, false, false, false) ==
           FP_WIFI_ATTEMPT_CREDENTIALS_RECEIVED);
    assert(fp_wifi_attempt_action(false, true, false, false) ==
           FP_WIFI_ATTEMPT_RETRY);
    assert(fp_wifi_attempt_action(false, false, true, false) ==
           FP_WIFI_ATTEMPT_READY);

    /* A stale ready bit must never turn a rejected credential into success. */
    assert(fp_wifi_attempt_action(false, true, true, false) ==
           FP_WIFI_ATTEMPT_RETRY);

    /* A device-local durability failure is not a credential retry. */
    assert(fp_wifi_attempt_action(true, true, true, true) ==
           FP_WIFI_ATTEMPT_ABORT);

    assert(fp_wifi_recovery_should_restore(false, true, true));
    assert(!fp_wifi_recovery_should_restore(true, true, true));
    assert(!fp_wifi_recovery_should_restore(false, false, true));
    assert(!fp_wifi_recovery_should_restore(false, true, false));

    assert(fp_pair_wait_uses_expiry(true, true, 300, 600));
    assert(fp_pair_wait_uses_expiry(true, true, 600, 600));
    assert(!fp_pair_wait_uses_expiry(true, true, 601, 600));
    assert(!fp_pair_wait_uses_expiry(false, true, 300, 600));
    assert(!fp_pair_wait_uses_expiry(true, false, 300, 600));
    assert(!fp_pair_wait_uses_expiry(true, true, 0, 600));

    puts("provisioning_contract: all cases pass");
    return 0;
}
