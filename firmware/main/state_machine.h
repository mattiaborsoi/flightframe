/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* The frame is a state machine, not an app:
 *
 *   boot -> why did I wake?
 *     first boot / factory reset -> PROVISION (BLE) -> POLL
 *     RTC timer (the 99% case)   -> POLL
 *     button press               -> POLL now / long-press -> re-provision
 *     USB power applied          -> exit shipping mode -> PROVISION|POLL
 *   POLL: WiFi -> GET /display -> hash-skip | download+verify -> refresh
 *   any failure anywhere -> backoff sleep. Always ends asleep.
 */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef enum {
    FP_WAKE_FIRST_BOOT,    /* no provisioning data in NVS            */
    FP_WAKE_TIMER,         /* RTC deep-sleep timer                   */
    FP_WAKE_BUTTON,        /* user pressed the poll-now button       */
    FP_WAKE_USB_POWER,     /* USB attached — exits shipping mode     */
    FP_WAKE_OTHER,
} fp_wake_reason_t;

typedef enum {
    FP_POLL_OK_REFRESHED,  /* new image on glass                     */
    FP_POLL_OK_UNCHANGED,  /* hash matched, nothing downloaded       */
    FP_POLL_OK_DEFERRED,   /* fetched fine; the panel could not draw
                            * yet (refresh spacing). NOT a failure —
                            * backing off here would punish a healthy
                            * frame for an impatient button press.    */
    FP_POLL_FAILED,        /* any failure — caller applies backoff   */
} fp_poll_result_t;

fp_wake_reason_t fp_classify_wake(void);
fp_poll_result_t fp_poll_once(const char *boot_reason,
                              uint32_t *sleep_s_out);
esp_err_t fp_provision(bool reprovision_wifi);
esp_err_t fp_repair_run(void);
esp_err_t fp_repair_expire(void);
void fp_factory_reset_and_restart(void);
uint32_t fp_resume_sleep_seconds(uint32_t fallback_seconds);
void fp_deep_sleep(uint32_t seconds) __attribute__((noreturn));
