/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <stdbool.h>

/*
 * Pure reducer for the Wi-Fi portion of a live Security-2 transaction.
 * Credential rejection is deliberately retryable: only an unrecoverable
 * local persistence failure may abort the provisioning manager.
 */
typedef enum {
    FP_WIFI_ATTEMPT_WAIT = 0,
    FP_WIFI_ATTEMPT_CREDENTIALS_RECEIVED,
    FP_WIFI_ATTEMPT_RETRY,
    FP_WIFI_ATTEMPT_READY,
    FP_WIFI_ATTEMPT_ABORT,
} fp_wifi_attempt_action_t;

fp_wifi_attempt_action_t fp_wifi_attempt_action(
    bool credentials_received,
    bool credentials_failed,
    bool wifi_ready,
    bool local_failure);

/* Unified Provisioning deliberately clears the prior RAM STA config. Only a
 * recoverable PAIR_READY proof with an untrusted clock may be seeded from the
 * explicit app-NVS mirror; ordinary right-arrow/KEY0 setup must wait for the
 * phone. */
bool fp_wifi_recovery_should_restore(
    bool target_requires_resubmit,
    bool pair_ready,
    bool clock_unknown);

/* A live possession proof is the tighter no-phone bound when its remaining
 * TTL fits within the generic provisioning transaction limit. */
bool fp_pair_wait_uses_expiry(
    bool pair_ready,
    bool expiry_live,
    unsigned pair_remaining_s,
    unsigned transaction_limit_s);
