/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Provisioning target persistence contract.
 *
 * This is deliberately ESP-independent so power-cut behavior and byte
 * validation can be tested on the host.  NVS stores one versioned blob:
 * readers therefore observe either the complete old target or the complete
 * new target, never a URL paired with the other target's setup secret.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FP_TARGET_URL_BYTES_MAX    127u
#define FP_TARGET_SECRET_BYTES_MAX 159u
#define FP_TARGET_BLOB_MAX         304u

typedef enum {
    FP_TARGET_PHASE_READY = 0,
    FP_TARGET_PHASE_NEEDS_RESUBMIT = 1,
    FP_TARGET_PHASE_SETUP_REQUIRED = 2,
} fp_target_phase_t;

/* Stored in the non-erased factory namespace. Bits independently model the
 * local erase barrier and an old-official-cloud cleanup obligation, so every
 * reset is power-cut safe without inventing a cloud call for a clean BYOS
 * device. */
typedef enum {
    FP_RESET_CLEANUP_NONE = 0,
    FP_RESET_CLEANUP_ERASE_PENDING = 1,
    FP_RESET_CLEANUP_CLOUD_PENDING = 2,
    FP_RESET_CLEANUP_ERASE_AND_CLOUD_PENDING = 3,
} fp_reset_cleanup_state_t;

/* Strict UTF-8 (no overlong encodings, surrogates, out-of-range code points,
 * or embedded NUL). */
bool fp_utf8_valid_no_nul(const uint8_t *value, size_t len);

/* Detect an actual JSON \u0000 escape.  An escaped backslash followed by
 * "u0000" is ordinary text and is not rejected. */
bool fp_json_contains_nul_escape(const uint8_t *json, size_t len);

/* Versioned target blob codec. URL is the normalized override; an empty URL
 * selects the compiled first-party default. Secret is empty for first-party
 * and for the legacy plain-URL BYOS form. */
bool fp_target_blob_encode(const char *url, const char *secret,
                           uint8_t *out, size_t cap, size_t *out_len);
bool fp_target_blob_decode(const uint8_t *blob, size_t len,
                           char *url, size_t url_cap,
                           char *secret, size_t secret_cap);

/* A bearer is usable only after the target and setup acknowledgement are
 * fully durable. Unknown/corrupt phase values fail closed. */
bool fp_target_token_allowed(uint8_t phase);

/* Old-official-cloud cleanup is staged only for a real first-party identity
 * crossing to BYOS. State 1 is pre-journal intent; state 2 is a coherent
 * custom target whose cleanup may be retried without blocking BYOS. */
bool fp_departure_cleanup_should_stage(bool current_first_party,
                                       bool desired_first_party,
                                       bool prior_identity_present);
bool fp_departure_cleanup_should_run(uint8_t cleanup_state,
                                     bool effective_first_party);
/* State 1 can be cancelled immediately only when both persisted and desired
 * targets are already first-party and no journal mutation is needed. State 2
 * survives any target change until a successful official setup/cleanup. */
bool fp_departure_cleanup_cancel_safe(uint8_t cleanup_state,
                                      bool current_first_party,
                                      bool desired_first_party,
                                      bool target_changed,
                                      uint8_t target_phase);

/* Every reset stages the erase bit. The cloud bit is added when an official
 * identity or prior departure cleanup would otherwise be destroyed with app
 * NVS. Unknown persisted values fail closed as erase+cloud pending. */
bool fp_reset_cleanup_cloud_should_stage(
    bool current_first_party,
    bool prior_identity_present,
    uint8_t departure_cleanup_state);
bool fp_reset_cleanup_resume_erase(uint8_t reset_cleanup_state);
bool fp_reset_cleanup_cloud_required(uint8_t reset_cleanup_state);
bool fp_reset_cleanup_should_run(uint8_t reset_cleanup_state,
                                 bool effective_first_party);
