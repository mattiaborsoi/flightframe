/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "provisioning_contract.h"

fp_wifi_attempt_action_t fp_wifi_attempt_action(
    bool credentials_received,
    bool credentials_failed,
    bool wifi_ready,
    bool local_failure)
{
    if (local_failure) {
        return FP_WIFI_ATTEMPT_ABORT;
    }
    if (credentials_failed) {
        return FP_WIFI_ATTEMPT_RETRY;
    }
    if (credentials_received) {
        return FP_WIFI_ATTEMPT_CREDENTIALS_RECEIVED;
    }
    if (wifi_ready) {
        return FP_WIFI_ATTEMPT_READY;
    }
    return FP_WIFI_ATTEMPT_WAIT;
}

bool fp_wifi_recovery_should_restore(
    bool target_requires_resubmit,
    bool pair_ready,
    bool clock_unknown)
{
    return !target_requires_resubmit && pair_ready && clock_unknown;
}

bool fp_pair_wait_uses_expiry(
    bool pair_ready,
    bool expiry_live,
    unsigned pair_remaining_s,
    unsigned transaction_limit_s)
{
    return pair_ready && expiry_live && pair_remaining_s > 0 &&
           pair_remaining_s <= transaction_limit_s;
}
