/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#include "pairing_contract.h"
#include "target_contract.h"

#define FP_PAIR_PUBLIC_SEC1_BYTES 65
#define FP_PAIR_PUBLIC_B64_MAX    96
#define FP_PAIR_SIGNATURE_B64_MAX 96
#define FP_SEC2_USERNAME_MAX      32
#define FP_SEC2_PASSWORD_MAX      80
#define FP_FACTORY_CREDENTIAL_MAX 160

typedef struct {
    char public_key_b64[FP_PAIR_PUBLIC_B64_MAX];
    char nonce[FP_PAIR_NONCE_HEX_LEN + 1];
    char nonce_hash[FP_PAIR_HASH_HEX_LEN + 1];
    char signature_b64[FP_PAIR_SIGNATURE_B64_MAX];
    uint32_t counter;
} fp_pair_registration_t;

typedef struct {
    char device_ref[FP_PAIR_DEVICE_REF_MAX + 1];
    char nonce[FP_PAIR_NONCE_HEX_LEN + 1];
    char expires_at[FP_PAIR_EXPIRES_MAX + 1];
    uint32_t counter;
} fp_pair_bundle_t;

typedef struct {
    char username[FP_SEC2_USERNAME_MAX];
    char password[FP_SEC2_PASSWORD_MAX];
    char *salt;
    uint16_t salt_len;
    char *verifier;
    uint16_t verifier_len;
} fp_sec2_credentials_t;

/* Seed the protected factory namespace only in explicit DEV builds. */
esp_err_t fp_factory_seed_dev_credential(void);
esp_err_t fp_factory_credential_get(char *out, size_t cap);
fp_reset_cleanup_state_t fp_factory_reset_cleanup_state(void);
esp_err_t fp_factory_reset_cleanup_set(fp_reset_cleanup_state_t state);

/* P-256 key and first/re-pair registration material. */
esp_err_t fp_pairing_ensure_key(void);
esp_err_t fp_pairing_prepare_first(fp_pair_registration_t *out);
esp_err_t fp_pairing_prepare_repair(fp_pair_registration_t *out);
esp_err_t fp_pairing_accept_first_ack(const char *device_ref,
                                      uint32_t counter,
                                      const char *expires_at);
esp_err_t fp_pairing_accept_repair_ack(uint32_t counter,
                                       const char *expires_at);
esp_err_t fp_pairing_load_bundle(fp_pair_bundle_t *out);
fp_pair_expiry_t fp_pairing_expiry_status(uint32_t *remaining_out);
uint32_t fp_pairing_seconds_remaining(void);
esp_err_t fp_pairing_complete(void);

fp_prov_state_t fp_prov_state_get(void);
esp_err_t fp_prov_state_set(fp_prov_state_t state);

/* Runtime Security-2 credentials.  The returned buffers remain caller-owned. */
esp_err_t fp_sec2_load_or_create(fp_sec2_credentials_t *out);
void fp_sec2_credentials_free(fp_sec2_credentials_t *creds);

/* Erase app state only; the protected factory namespace survives. */
esp_err_t fp_factory_reset_app_state(void);
