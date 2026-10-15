/* SPDX-FileCopyrightText: 2026 flightframe
 * SPDX-License-Identifier: Apache-2.0 */
/* Pure decision rules for the flightframe build: what a key press means,
 * when a frame should ask for attention, and which firmware offers to take.
 * No ESP-IDF dependencies, so every rule is exercised by a host test. */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "pairing_contract.h"

/* Why the frame is showing its setup page. Stored as a u8 in NVS and
 * rendered on that page, so values are append-only. */
typedef enum {
    FP_ATTN_NONE = 0,
    FP_ATTN_USER = 1,         /* someone held a key to change Wi-Fi      */
    FP_ATTN_WIFI = 2,         /* could not join the saved network        */
    FP_ATTN_DNS = 3,          /* joined, but the server name won't resolve */
    FP_ATTN_SERVER = 4,       /* server unreachable or refused the frame */
    FP_ATTN_NEEDS_CODE = 5,   /* must register again; no setup code held */
    FP_ATTN_FIRST_SETUP = 6,  /* no Wi-Fi saved at all                   */
} fp_attention_t;

/* Every key behaves the same, so a person never needs to know which is
 * which. From deep sleep: a tap fetches, a hold opens the Wi-Fi page. At a
 * reset or power-on: a key held through boot is a factory reset. A zero
 * threshold disables that gesture. */
fp_button_action_t fp_gesture_classify(bool from_sleep, uint32_t held_ms,
                                       uint32_t wifi_hold_ms,
                                       uint32_t reset_combo_ms);

/* After this many consecutive failed wakes the frame stops failing quietly
 * and shows its setup page. Zero disables it. */
bool fp_auto_portal_due(uint8_t consecutive_failures, uint8_t threshold);

/* A firmware offer is taken only if it names a version that is neither the
 * one already running nor one this frame already rolled back from. The
 * server decides what is newer; this only stops repeats and retry loops. */
bool fp_fw_offer_acceptable(const char *offered, const char *running,
                            const char *rejected);
