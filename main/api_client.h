/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "identity.h"
#include "target_contract.h"

#define FP_IMAGE_BYTES 960000u        /* 1200*1600 / 2 — PROTOCOL.md §1 */

typedef struct {
    char image_url[768];              /* presigned URLs are long */
    char image_hash[80];              /* "sha256:<64 hex>" */
    uint32_t sleep_s;
    bool reset;
    bool has_fw;
    char fw_url[768];
    char fw_sha256[65];
    char fw_version[32];
    bool has_pairing_ack;
    uint32_t pairing_counter;
    char pairing_expires_at[FP_PAIR_EXPIRES_MAX + 1];
} fp_display_t;

typedef struct {
    char device_ref[FP_PAIR_DEVICE_REF_MAX + 1];
    bool has_pairing_ack;
    uint32_t pairing_counter;
    char pairing_expires_at[FP_PAIR_EXPIRES_MAX + 1];
} fp_setup_result_t;

/* Resolve the API base URL: the hand-set NVS override if present, else
 * the compiled-in CONFIG_FP_API_BASE. Never fails. */
void fp_api_base_get(char *out, size_t cap);

/* Normalize and store (or, for blank, erase) the hand-set server URL and
 * its optional BYOS setup secret. ESP_ERR_INVALID_ARG = rejected, previous
 * value untouched. setup_secret == NULL is the legacy plain URL form and
 * clears any older staged BYOS secret. A non-NULL secret is accepted only
 * with a non-default, non-blank URL.
 *
 * CONSTITUTION (PROTOCOL.md §5): callers are provisioning flows ONLY —
 * BLE provisioning, the long-press re-provision flow, and the dev-config
 * seed. The server must NEVER reach this: no OTA config path, no API
 * response field. A proposal that lets the server touch the target blob is
 * a breaking protocol change, not an implementation detail. */
esp_err_t fp_api_provisioning_target_set(const char *raw,
                                         const char *setup_secret);

/* Phase 1 means a power interruption occurred during target replacement:
 * target input must be re-submitted and no cloud request is allowed. Phase 2
 * is a complete target that still needs setup. A bearer is usable only in
 * phase 0. */
fp_target_phase_t fp_api_target_phase(void);
bool fp_api_setup_required(void);
bool fp_api_token_allowed(void);
esp_err_t fp_api_setup_mark_required(void);
esp_err_t fp_api_setup_mark_complete(void);

/* Read the staged BYOS setup secret from the versioned target blob, with a
 * read-only fallback for pre-journal firmware state. */
esp_err_t fp_api_byos_setup_get(char *out, size_t cap);

/* First-party -> BYOS departure is a best-effort unlink against the compiled
 * official host. A factory-reset copy of the pending bit lives in the
 * non-erased factory namespace; an attempt is never sent to the effective
 * custom target and its result never blocks BYOS setup. */
bool fp_api_departure_cleanup_pending(void);
esp_err_t fp_api_departure_cleanup_clear(void);
esp_err_t fp_api_departure_cleanup(const char *factory_credential);
/* Load the factory credential, call only the fixed official URL, wipe the
 * credential, and clear the pending bit on success. Callers intentionally
 * ignore failure so custom-server use remains available. */
esp_err_t fp_api_departure_cleanup_attempt(void);

/* Read-only decision for the reset journal's cloud-cleanup bit, made before
 * app NVS is erased. Every reset independently stages its local erase bit. */
bool fp_api_factory_reset_cleanup_needed(void);

/* POST /device/v1/setup. Pairing fields are additive and may be omitted for
 * an older BYOS server. Stores the returned device token only after parsing
 * a complete success response. */
esp_err_t fp_api_setup(const char *provision_secret,
                       const fp_pair_registration_t *pairing,
                       fp_setup_result_t *out);

/* GET /device/v1/display. A non-NULL pairing registration adds the signed
 * re-pair headers; older BYOS servers simply ignore them. */
esp_err_t fp_api_get_display(const char *boot_reason,
                             const fp_pair_registration_t *pairing,
                             fp_display_t *out);

/* Download image_url into buf (FP_IMAGE_BYTES), verifying size and that
 * sha256(buf) matches expected_hash ("sha256:<hex>"). */
esp_err_t fp_api_download(const char *url, const char *expected_hash,
                          uint8_t *buf);

/* POST /device/v1/log with a prebuilt {"logs":[…]} body. Fire-and-forget
 * transport: the caller decides what to do (nothing) on failure. */
esp_err_t fp_api_post_logs(const char *body, const char *boot_reason);
