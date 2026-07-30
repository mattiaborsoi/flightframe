/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "target_contract.h"

#include <string.h>

static const uint8_t FP_TARGET_MAGIC[4] = {'F', 'P', 'T', '1'};
#define FP_TARGET_HEADER_BYTES 8u

bool fp_utf8_valid_no_nul(const uint8_t *value, size_t len)
{
    if (!value && len != 0) {
        return false;
    }
    for (size_t i = 0; i < len;) {
        uint8_t c = value[i++];
        if (c == 0) {
            return false;
        }
        if (c <= 0x7f) {
            continue;
        }

        uint32_t cp;
        size_t continuation;
        if (c >= 0xc2 && c <= 0xdf) {
            cp = c & 0x1fu;
            continuation = 1;
        } else if (c >= 0xe0 && c <= 0xef) {
            cp = c & 0x0fu;
            continuation = 2;
        } else if (c >= 0xf0 && c <= 0xf4) {
            cp = c & 0x07u;
            continuation = 3;
        } else {
            return false;
        }
        if (continuation > len - i) {
            return false;
        }
        for (size_t j = 0; j < continuation; ++j) {
            uint8_t next = value[i++];
            if ((next & 0xc0u) != 0x80u) {
                return false;
            }
            cp = (cp << 6) | (next & 0x3fu);
        }
        if ((continuation == 1 && cp < 0x80u) ||
            (continuation == 2 &&
             (cp < 0x800u || (cp >= 0xd800u && cp <= 0xdfffu))) ||
            (continuation == 3 &&
             (cp < 0x10000u || cp > 0x10ffffu))) {
            return false;
        }
    }
    return true;
}

bool fp_json_contains_nul_escape(const uint8_t *json, size_t len)
{
    if (!json) {
        return false;
    }
    for (size_t i = 1; i + 4 < len; ++i) {
        if (json[i] != 'u' ||
            json[i + 1] != '0' || json[i + 2] != '0' ||
            json[i + 3] != '0' || json[i + 4] != '0') {
            continue;
        }
        size_t slashes = 0;
        size_t cursor = i;
        while (cursor > 0 && json[cursor - 1] == '\\') {
            ++slashes;
            --cursor;
        }
        if ((slashes & 1u) != 0) {
            return true;
        }
    }
    return false;
}

bool fp_target_blob_encode(const char *url, const char *secret,
                           uint8_t *out, size_t cap, size_t *out_len)
{
    if (!url || !secret || !out || !out_len) {
        return false;
    }
    size_t url_len = strlen(url);
    size_t secret_len = strlen(secret);
    size_t total = FP_TARGET_HEADER_BYTES + url_len + secret_len;
    if (url_len > FP_TARGET_URL_BYTES_MAX ||
        secret_len > FP_TARGET_SECRET_BYTES_MAX ||
        total > cap ||
        !fp_utf8_valid_no_nul((const uint8_t *)url, url_len) ||
        !fp_utf8_valid_no_nul((const uint8_t *)secret, secret_len)) {
        return false;
    }

    memcpy(out, FP_TARGET_MAGIC, sizeof(FP_TARGET_MAGIC));
    out[4] = (uint8_t)(url_len >> 8);
    out[5] = (uint8_t)url_len;
    out[6] = (uint8_t)(secret_len >> 8);
    out[7] = (uint8_t)secret_len;
    memcpy(out + FP_TARGET_HEADER_BYTES, url, url_len);
    memcpy(out + FP_TARGET_HEADER_BYTES + url_len, secret, secret_len);
    *out_len = total;
    return true;
}

bool fp_target_blob_decode(const uint8_t *blob, size_t len,
                           char *url, size_t url_cap,
                           char *secret, size_t secret_cap)
{
    if (!blob || !url || !secret || len < FP_TARGET_HEADER_BYTES ||
        memcmp(blob, FP_TARGET_MAGIC, sizeof(FP_TARGET_MAGIC)) != 0) {
        return false;
    }
    size_t url_len = ((size_t)blob[4] << 8) | blob[5];
    size_t secret_len = ((size_t)blob[6] << 8) | blob[7];
    if (url_len > FP_TARGET_URL_BYTES_MAX ||
        secret_len > FP_TARGET_SECRET_BYTES_MAX ||
        url_len + secret_len != len - FP_TARGET_HEADER_BYTES ||
        url_len >= url_cap || secret_len >= secret_cap) {
        return false;
    }
    const uint8_t *url_bytes = blob + FP_TARGET_HEADER_BYTES;
    const uint8_t *secret_bytes = url_bytes + url_len;
    if (!fp_utf8_valid_no_nul(url_bytes, url_len) ||
        !fp_utf8_valid_no_nul(secret_bytes, secret_len)) {
        return false;
    }
    memcpy(url, url_bytes, url_len);
    url[url_len] = '\0';
    memcpy(secret, secret_bytes, secret_len);
    secret[secret_len] = '\0';
    return true;
}

bool fp_target_token_allowed(uint8_t phase)
{
    return phase == FP_TARGET_PHASE_READY;
}

bool fp_departure_cleanup_should_stage(bool current_first_party,
                                       bool desired_first_party,
                                       bool prior_identity_present)
{
    return current_first_party && !desired_first_party &&
           prior_identity_present;
}

bool fp_departure_cleanup_should_run(uint8_t cleanup_state,
                                     bool effective_first_party)
{
    return cleanup_state == 2 && !effective_first_party;
}

bool fp_departure_cleanup_cancel_safe(uint8_t cleanup_state,
                                      bool current_first_party,
                                      bool desired_first_party,
                                      bool target_changed,
                                      uint8_t target_phase)
{
    return cleanup_state == 1 &&
           current_first_party && desired_first_party &&
           !target_changed &&
           target_phase == FP_TARGET_PHASE_READY;
}

bool fp_reset_cleanup_cloud_should_stage(
    bool current_first_party,
    bool prior_identity_present,
    uint8_t departure_cleanup_state)
{
    return departure_cleanup_state != 0 ||
           (current_first_party && prior_identity_present);
}

bool fp_reset_cleanup_resume_erase(uint8_t reset_cleanup_state)
{
    return reset_cleanup_state != FP_RESET_CLEANUP_NONE &&
           reset_cleanup_state != FP_RESET_CLEANUP_CLOUD_PENDING;
}

bool fp_reset_cleanup_cloud_required(uint8_t reset_cleanup_state)
{
    return reset_cleanup_state >
               FP_RESET_CLEANUP_ERASE_AND_CLOUD_PENDING ||
           (reset_cleanup_state &
            FP_RESET_CLEANUP_CLOUD_PENDING) != 0;
}

bool fp_reset_cleanup_should_run(uint8_t reset_cleanup_state,
                                 bool effective_first_party)
{
    return reset_cleanup_state == FP_RESET_CLEANUP_CLOUD_PENDING &&
           !effective_first_party;
}
