/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/*
 * Wire-format and lifecycle rules shared by the firmware and host tests.
 *
 * This file deliberately has no ESP-IDF dependencies.  The strings emitted
 * here are security protocol inputs: changing punctuation, key order, or line
 * endings changes signatures and QR payloads.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FP_PAIR_NONCE_BYTES       32
#define FP_PAIR_NONCE_HEX_LEN     64
#define FP_PAIR_HASH_HEX_LEN      64
#define FP_DEVICE_TOKEN_HEX_LEN   64
#define FP_DEVICE_REF_HEX_LEN     32
#define FP_IMAGE_HASH_HEX_LEN     64
#define FP_PAIR_DEVICE_REF_MAX    96
#define FP_PAIR_EXPIRES_MAX       40
#define FP_PAIR_SIGNING_MAX       320
#define FP_PAIR_QR_JSON_MAX       384
#define FP_PROV_QR_JSON_MAX       320

typedef enum {
    FP_PROV_STATE_EMPTY = 0,
    FP_PROV_STATE_QR_READY = 1,
    FP_PROV_STATE_SETUP_PENDING = 2,
    FP_PROV_STATE_PAIR_READY = 3,
    FP_PROV_STATE_COMPLETE = 4,
    FP_PROV_STATE_REPAIR_INTENT = 5,
    FP_PROV_STATE_REPAIR_REGISTERING = 6,
    FP_PROV_STATE_REPAIR_VISIBLE = 7,
} fp_prov_state_t;

typedef enum {
    FP_BUTTON_NONE = 0,
    FP_BUTTON_POLL,
    FP_BUTTON_REPAIR,
    FP_BUTTON_REPROVISION,
    FP_BUTTON_FACTORY_RESET,
} fp_button_action_t;

typedef enum {
    FP_SETUP_CREDENTIAL_FACTORY = 0,
    FP_SETUP_CREDENTIAL_BYOS,
    FP_SETUP_CREDENTIAL_MISSING,
} fp_setup_credential_source_t;

typedef enum {
    FP_PAIR_EXPIRY_UNKNOWN = 0,
    FP_PAIR_EXPIRY_LIVE,
    FP_PAIR_EXPIRY_EXPIRED,
} fp_pair_expiry_t;

/* Lowercase hexadecimal with no terminator included in out_len. */
bool fp_hex_lower(const uint8_t *src, size_t src_len,
                  char *out, size_t out_len);

/* Strict response-field validators. These run before any cloud response is
 * copied into fixed buffers or persisted. */
bool fp_lowercase_hex_exact(const char *value, size_t len);
bool fp_json_u32(double value, uint32_t *out);
bool fp_device_token_valid(const char *value);
bool fp_device_ref_valid(const char *value);
bool fp_pairing_ack_valid(double counter, const char *expires_at,
                          uint32_t expected_counter,
                          uint32_t *counter_out);
bool fp_http_url_fits(const char *value, size_t capacity);
bool fp_image_hash_valid(const char *value);
bool fp_ota_hash_valid(const char *value);
bool fp_nonempty_text_fits(const char *value, size_t capacity);
bool fp_display_required_fields_valid(const char *image_url,
                                      size_t image_url_capacity,
                                      const char *image_hash,
                                      double sleep_s,
                                      uint32_t *sleep_out);

/* Exact signed bytes:
 * flightportrait-pair-v1\n{device_ref}\n{counter}\n{nonce_hash}
 */
bool fp_pair_signing_payload(const char *device_ref, uint32_t counter,
                             const char *nonce_hash,
                             char *out, size_t out_len);

/* Exact re-pair QR JSON.  Field order is part of the test vector. */
bool fp_pair_qr_json(const char *device_ref, const char *nonce,
                     const char *expires_at,
                     char *out, size_t out_len);

/* Espressif Unified Provisioning Security-2 QR JSON. */
bool fp_provision_qr_json(const char *service_name, const char *username,
                          const char *password,
                          char *out, size_t out_len);

/* Parse the UTC form emitted by Python datetime.isoformat():
 * YYYY-MM-DDTHH:MM:SS[.fraction]+00:00 (also accepts trailing Z). */
bool fp_rfc3339_utc_epoch(const char *value, int64_t *epoch_out);

/* UNKNOWN means the wall clock is not trustworthy or the persisted expiry
 * cannot be parsed. Callers must sync time/recover; they must not expire a
 * visible proof based on UNKNOWN. */
fp_pair_expiry_t fp_pair_expiry_classify(const char *expires_at,
                                         int64_t now_epoch,
                                         bool clock_valid,
                                         uint32_t *remaining_out);

/* The immutable factory credential is categorically first-party-only. */
fp_setup_credential_source_t fp_setup_credential_source(
    bool first_party, bool byos_secret_available);

/* Hardware-independent gesture policy. */
fp_button_action_t fp_button_classify(unsigned key, uint32_t held_ms,
                                      uint32_t reprovision_ms,
                                      uint32_t factory_reset_ms);

/* Persisted transaction transitions.  Recovery may repeat a state. */
bool fp_prov_transition_allowed(fp_prov_state_t from, fp_prov_state_t to);

/* Server TTL must not start until the e-ink refresh guard is clear. */
bool fp_repair_registration_allowed(uint32_t panel_wait_seconds);

/* KEY1 pairing is a first-party extension. A custom target keeps its normal
 * display cadence and treats the gesture as unsupported. */
bool fp_repair_target_supported(bool first_party);

/* Factory reset erases the app namespace, never the protected factory one. */
bool fp_reset_erases_namespace(const char *name);
