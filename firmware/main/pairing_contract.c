/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "pairing_contract.h"

#include <stdio.h>
#include <string.h>

#define FP_APP_NAMESPACE      "flightportrait"
#define FP_FACTORY_NAMESPACE  "fp_factory"

static bool json_atom_safe(const char *s, size_t max_len)
{
    if (!s) {
        return false;
    }
    size_t n = strlen(s);
    if (n == 0 || n > max_len) {
        return false;
    }
    for (size_t i = 0; i < n; ++i) {
        unsigned char c = (unsigned char)s[i];
        if (c < 0x20 || c == '"' || c == '\\') {
            return false;
        }
    }
    return true;
}

bool fp_lowercase_hex_exact(const char *s, size_t len)
{
    if (!s || strlen(s) != len) {
        return false;
    }
    for (size_t i = 0; i < len; ++i) {
        if (!((s[i] >= '0' && s[i] <= '9') ||
              (s[i] >= 'a' && s[i] <= 'f'))) {
            return false;
        }
    }
    return true;
}

bool fp_json_u32(double value, uint32_t *out)
{
    if (!(value >= 1.0 && value <= (double)UINT32_MAX)) {
        return false;
    }
    uint32_t parsed = (uint32_t)value;
    if ((double)parsed != value) {
        return false;
    }
    if (out) {
        *out = parsed;
    }
    return true;
}

bool fp_device_token_valid(const char *value)
{
    return fp_lowercase_hex_exact(value, FP_DEVICE_TOKEN_HEX_LEN);
}

bool fp_device_ref_valid(const char *value)
{
    return fp_lowercase_hex_exact(value, FP_DEVICE_REF_HEX_LEN);
}

bool fp_pairing_ack_valid(double counter, const char *expires_at,
                          uint32_t expected_counter,
                          uint32_t *counter_out)
{
    uint32_t parsed = 0;
    int64_t expires_epoch = 0;
    if (expected_counter == 0 ||
        !fp_json_u32(counter, &parsed) ||
        parsed != expected_counter ||
        !expires_at ||
        strlen(expires_at) > FP_PAIR_EXPIRES_MAX ||
        !fp_rfc3339_utc_epoch(expires_at, &expires_epoch)) {
        return false;
    }
    if (counter_out) {
        *counter_out = parsed;
    }
    return true;
}

bool fp_nonempty_text_fits(const char *value, size_t capacity)
{
    if (!value || capacity == 0) {
        return false;
    }
    size_t len = strlen(value);
    if (len == 0 || len >= capacity) {
        return false;
    }
    for (size_t i = 0; i < len; ++i) {
        unsigned char c = (unsigned char)value[i];
        if (c < 0x20 || c == 0x7f) {
            return false;
        }
    }
    return true;
}

bool fp_http_url_fits(const char *value, size_t capacity)
{
    if (!fp_nonempty_text_fits(value, capacity)) {
        return false;
    }
    size_t scheme = strncmp(value, "https://", 8) == 0 ? 8 :
                    strncmp(value, "http://", 7) == 0 ? 7 : 0;
    return scheme != 0 && value[scheme] != '\0';
}

bool fp_image_hash_valid(const char *value)
{
    static const char prefix[] = "sha256:";
    return value &&
           strncmp(value, prefix, sizeof(prefix) - 1) == 0 &&
           fp_lowercase_hex_exact(
               value + sizeof(prefix) - 1, FP_IMAGE_HASH_HEX_LEN);
}

bool fp_ota_hash_valid(const char *value)
{
    return fp_lowercase_hex_exact(value, FP_IMAGE_HASH_HEX_LEN);
}

bool fp_display_required_fields_valid(const char *image_url,
                                      size_t image_url_capacity,
                                      const char *image_hash,
                                      double sleep_s,
                                      uint32_t *sleep_out)
{
    return fp_http_url_fits(image_url, image_url_capacity) &&
           fp_image_hash_valid(image_hash) &&
           fp_json_u32(sleep_s, sleep_out);
}

bool fp_hex_lower(const uint8_t *src, size_t src_len,
                  char *out, size_t out_len)
{
    static const char hex[] = "0123456789abcdef";
    if (!src || !out || out_len < src_len * 2 + 1) {
        return false;
    }
    for (size_t i = 0; i < src_len; ++i) {
        out[i * 2] = hex[src[i] >> 4];
        out[i * 2 + 1] = hex[src[i] & 0x0f];
    }
    out[src_len * 2] = '\0';
    return true;
}

bool fp_pair_signing_payload(const char *device_ref, uint32_t counter,
                             const char *nonce_hash,
                             char *out, size_t out_len)
{
    if (!fp_device_ref_valid(device_ref) ||
        !fp_lowercase_hex_exact(nonce_hash, FP_PAIR_HASH_HEX_LEN) ||
        counter == 0 || !out || out_len == 0) {
        return false;
    }
    int n = snprintf(out, out_len, "flightportrait-pair-v1\n%s\n%lu\n%s",
                     device_ref, (unsigned long)counter, nonce_hash);
    return n > 0 && (size_t)n < out_len;
}

bool fp_pair_qr_json(const char *device_ref, const char *nonce,
                     const char *expires_at,
                     char *out, size_t out_len)
{
    if (!fp_device_ref_valid(device_ref) ||
        !fp_lowercase_hex_exact(nonce, FP_PAIR_NONCE_HEX_LEN) ||
        !json_atom_safe(expires_at, FP_PAIR_EXPIRES_MAX) ||
        !out || out_len == 0) {
        return false;
    }
    int n = snprintf(out, out_len,
                     "{\"v\":1,\"kind\":\"flightportrait-pair\","
                     "\"device_ref\":\"%s\",\"nonce\":\"%s\","
                     "\"expires_at\":\"%s\"}",
                     device_ref, nonce, expires_at);
    return n > 0 && (size_t)n < out_len;
}

bool fp_provision_qr_json(const char *service_name, const char *username,
                          const char *password,
                          char *out, size_t out_len)
{
    if (!json_atom_safe(service_name, 31) ||
        !json_atom_safe(username, 63) ||
        !json_atom_safe(password, 127) ||
        !out || out_len == 0) {
        return false;
    }
    int n = snprintf(out, out_len,
                     "{\"ver\":\"v1\",\"name\":\"%s\","
                     "\"username\":\"%s\",\"pop\":\"%s\","
                     "\"transport\":\"ble\"}",
                     service_name, username, password);
    return n > 0 && (size_t)n < out_len;
}

static bool decimal_at(const char *value, size_t offset, size_t count,
                       unsigned *out)
{
    unsigned result = 0;
    for (size_t i = 0; i < count; ++i) {
        char c = value[offset + i];
        if (c < '0' || c > '9') {
            return false;
        }
        result = result * 10u + (unsigned)(c - '0');
    }
    *out = result;
    return true;
}

static bool leap_year(unsigned year)
{
    return year % 4u == 0 && (year % 100u != 0 || year % 400u == 0);
}

bool fp_rfc3339_utc_epoch(const char *value, int64_t *epoch_out)
{
    if (!value || !epoch_out || strlen(value) < 20 ||
        value[4] != '-' || value[7] != '-' || value[10] != 'T' ||
        value[13] != ':' || value[16] != ':') {
        return false;
    }
    unsigned year, month, day, hour, minute, second;
    if (!decimal_at(value, 0, 4, &year) ||
        !decimal_at(value, 5, 2, &month) ||
        !decimal_at(value, 8, 2, &day) ||
        !decimal_at(value, 11, 2, &hour) ||
        !decimal_at(value, 14, 2, &minute) ||
        !decimal_at(value, 17, 2, &second) ||
        year < 1970 || month < 1 || month > 12 ||
        hour > 23 || minute > 59 || second > 59) {
        return false;
    }
    static const uint8_t month_days[] = {
        31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31,
    };
    unsigned max_day = month_days[month - 1];
    if (month == 2 && leap_year(year)) {
        ++max_day;
    }
    if (day < 1 || day > max_day) {
        return false;
    }

    const char *suffix = value + 19;
    if (*suffix == '.') {
        ++suffix;
        const char *fraction = suffix;
        while (*suffix >= '0' && *suffix <= '9') {
            ++suffix;
        }
        if (suffix == fraction) {
            return false;
        }
    }
    if (!((*suffix == 'Z' && suffix[1] == '\0') ||
          strcmp(suffix, "+00:00") == 0)) {
        return false;
    }

    /* Howard Hinnant's civil-date transform, offset to Unix epoch. */
    int64_t y = (int64_t)year - (month <= 2);
    int64_t era = y / 400;
    unsigned yoe = (unsigned)(y - era * 400);
    unsigned shifted_month = month > 2 ? month - 3 : month + 9;
    unsigned doy = (153u * shifted_month + 2u) / 5u + day - 1u;
    unsigned doe = yoe * 365u + yoe / 4u - yoe / 100u + doy;
    int64_t days = era * 146097 + (int64_t)doe - 719468;
    *epoch_out = days * 86400 + (int64_t)hour * 3600 +
                 (int64_t)minute * 60 + second;
    return true;
}

fp_pair_expiry_t fp_pair_expiry_classify(const char *expires_at,
                                         int64_t now_epoch,
                                         bool clock_valid,
                                         uint32_t *remaining_out)
{
    if (remaining_out) {
        *remaining_out = 0;
    }
    int64_t expires_epoch = 0;
    if (!clock_valid ||
        !fp_rfc3339_utc_epoch(expires_at, &expires_epoch)) {
        return FP_PAIR_EXPIRY_UNKNOWN;
    }
    if (expires_epoch <= now_epoch) {
        return FP_PAIR_EXPIRY_EXPIRED;
    }
    uint64_t remaining = (uint64_t)(expires_epoch - now_epoch);
    if (remaining_out) {
        *remaining_out = remaining > UINT32_MAX
            ? UINT32_MAX : (uint32_t)remaining;
    }
    return FP_PAIR_EXPIRY_LIVE;
}

fp_setup_credential_source_t fp_setup_credential_source(
    bool first_party, bool byos_secret_available)
{
    if (first_party) {
        return FP_SETUP_CREDENTIAL_FACTORY;
    }
    return byos_secret_available
        ? FP_SETUP_CREDENTIAL_BYOS : FP_SETUP_CREDENTIAL_MISSING;
}

fp_button_action_t fp_button_classify(unsigned key, uint32_t held_ms,
                                      uint32_t reprovision_ms,
                                      uint32_t factory_reset_ms)
{
    /* E1004 PCB labels are not ordered like the printed controls:
     * KEY2/GPIO5 is circular Refresh, KEY1/GPIO4 is left-arrow, and
     * KEY0/GPIO3 is right-arrow. Keep that translation explicit here. */
    if (key == 2) {
        return FP_BUTTON_POLL;
    }
    if (key == 1) {
        return FP_BUTTON_REPAIR;
    }
    if (key != 0 || factory_reset_ms <= reprovision_ms) {
        return FP_BUTTON_NONE;
    }
    if (held_ms >= factory_reset_ms) {
        return FP_BUTTON_FACTORY_RESET;
    }
    if (held_ms >= reprovision_ms) {
        return FP_BUTTON_REPROVISION;
    }
    return FP_BUTTON_NONE;
}

bool fp_prov_transition_allowed(fp_prov_state_t from, fp_prov_state_t to)
{
    if (from == to) {
        return true; /* idempotent retry after a reset or lost response */
    }
    switch (from) {
    case FP_PROV_STATE_EMPTY:
        return to == FP_PROV_STATE_QR_READY;
    case FP_PROV_STATE_QR_READY:
        return to == FP_PROV_STATE_SETUP_PENDING ||
               to == FP_PROV_STATE_COMPLETE;
    case FP_PROV_STATE_SETUP_PENDING:
        return to == FP_PROV_STATE_PAIR_READY ||
               to == FP_PROV_STATE_COMPLETE ||
               to == FP_PROV_STATE_QR_READY;
    case FP_PROV_STATE_PAIR_READY:
        return to == FP_PROV_STATE_COMPLETE;
    case FP_PROV_STATE_COMPLETE:
        return to == FP_PROV_STATE_REPAIR_INTENT ||
               to == FP_PROV_STATE_QR_READY;
    case FP_PROV_STATE_REPAIR_INTENT:
        return to == FP_PROV_STATE_REPAIR_REGISTERING ||
               to == FP_PROV_STATE_COMPLETE;
    case FP_PROV_STATE_REPAIR_REGISTERING:
        return to == FP_PROV_STATE_REPAIR_VISIBLE ||
               to == FP_PROV_STATE_REPAIR_INTENT ||
               to == FP_PROV_STATE_COMPLETE;
    case FP_PROV_STATE_REPAIR_VISIBLE:
        return to == FP_PROV_STATE_COMPLETE;
    default:
        return false;
    }
}

bool fp_repair_registration_allowed(uint32_t panel_wait_seconds)
{
    return panel_wait_seconds == 0;
}

bool fp_repair_target_supported(bool first_party)
{
    return first_party;
}

bool fp_reset_erases_namespace(const char *name)
{
    if (!name) {
        return false;
    }
    if (strcmp(name, FP_FACTORY_NAMESPACE) == 0) {
        return false;
    }
    return strcmp(name, FP_APP_NAMESPACE) == 0;
}
