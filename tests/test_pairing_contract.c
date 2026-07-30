/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Host tests for the bytes shared with the server/app and the reset/button
 * policies. No ESP-IDF headers are required.
 *
 *   cc main/pairing_contract.c \
 *      tests/test_pairing_contract.c -o /tmp/tpair && /tmp/tpair
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../main/pairing_contract.h"

int main(void)
{
    const uint8_t raw[] = {0x00, 0x7f, 0xa5, 0xff};
    char hex[9];
    assert(fp_hex_lower(raw, sizeof(raw), hex, sizeof(hex)));
    assert(strcmp(hex, "007fa5ff") == 0);

    const char *hash =
        "0123456789abcdef0123456789abcdef"
        "0123456789abcdef0123456789abcdef";
    const char *nonce =
        "fedcba9876543210fedcba9876543210"
        "fedcba9876543210fedcba9876543210";
    const char *device_ref = "0123456789abcdef0123456789abcdef";
    const char *image_hash =
        "sha256:0123456789abcdef0123456789abcdef"
        "0123456789abcdef0123456789abcdef";
    assert(fp_lowercase_hex_exact(hash, 64));
    assert(!fp_lowercase_hex_exact(
        "0123456789abcdef0123456789abcdef"
        "0123456789abcdef0123456789abcdeF", 64));
    assert(fp_device_token_valid(hash));
    assert(!fp_device_token_valid("abcd"));
    assert(fp_device_ref_valid(device_ref));
    assert(!fp_device_ref_valid("0123456789abcdef0123456789abcdeF"));

    uint32_t parsed_u32 = 0;
    assert(fp_json_u32(1.0, &parsed_u32) && parsed_u32 == 1);
    assert(fp_json_u32(4294967295.0, &parsed_u32) &&
           parsed_u32 == UINT32_MAX);
    assert(!fp_json_u32(0.0, &parsed_u32));
    assert(!fp_json_u32(-1.0, &parsed_u32));
    assert(!fp_json_u32(1.5, &parsed_u32));
    assert(!fp_json_u32(4294967296.0, &parsed_u32));

    assert(fp_pairing_ack_valid(
        17.0, "2026-07-24T12:34:56+00:00", 17, &parsed_u32));
    assert(parsed_u32 == 17);
    assert(!fp_pairing_ack_valid(
        17.5, "2026-07-24T12:34:56+00:00", 17, &parsed_u32));
    assert(!fp_pairing_ack_valid(
        18.0, "2026-07-24T12:34:56+00:00", 17, &parsed_u32));
    assert(!fp_pairing_ack_valid(
        17.0, "2026-07-24T12:34:56+08:00", 17, &parsed_u32));
    assert(!fp_pairing_ack_valid(
        17.0, "2026-07-24T12:34:56.12345678901234567890+00:00",
        17, &parsed_u32));

    assert(fp_http_url_fits(
        "https://cdn.example/frame.bin", 768));
    assert(fp_http_url_fits(
        "http://192.168.1.5/frame.bin", 768));
    assert(!fp_http_url_fits("ftp://cdn.example/frame.bin", 768));
    assert(!fp_http_url_fits("https://", 768));
    assert(!fp_http_url_fits("https://cdn.example/frame.bin", 10));
    assert(fp_image_hash_valid(image_hash));
    assert(!fp_image_hash_valid(hash));
    assert(!fp_image_hash_valid(
        "sha256:0123456789abcdef0123456789abcdef"
        "0123456789abcdef0123456789abcdeF"));
    assert(fp_ota_hash_valid(hash));
    assert(!fp_ota_hash_valid(image_hash));
    assert(fp_nonempty_text_fits("0.2.0", 32));
    assert(!fp_nonempty_text_fits("", 32));
    assert(!fp_nonempty_text_fits("bad\nversion", 32));
    uint32_t sleep_s = 0;
    assert(fp_display_required_fields_valid(
        "https://cdn.example/frame.bin", 768, image_hash,
        43873.0, &sleep_s));
    assert(sleep_s == 43873);
    assert(!fp_display_required_fields_valid(
        "https://cdn.example/frame.bin", 768, image_hash,
        0.0, &sleep_s));
    assert(!fp_display_required_fields_valid(
        "https://cdn.example/frame.bin", 768, image_hash,
        60.5, &sleep_s));

    char payload[FP_PAIR_SIGNING_MAX];
    assert(fp_pair_signing_payload(device_ref, 17, hash,
                                   payload, sizeof(payload)));
    assert(strcmp(payload,
                  "flightportrait-pair-v1\n"
                  "0123456789abcdef0123456789abcdef\n17\n"
                  "0123456789abcdef0123456789abcdef"
                  "0123456789abcdef0123456789abcdef") == 0);

    char qr[FP_PAIR_QR_JSON_MAX];
    assert(fp_pair_qr_json(device_ref, nonce,
                           "2026-07-24T12:34:56+00:00",
                           qr, sizeof(qr)));
    assert(strcmp(qr,
                  "{\"v\":1,\"kind\":\"flightportrait-pair\","
                  "\"device_ref\":\"0123456789abcdef0123456789abcdef\","
                  "\"nonce\":\"fedcba9876543210fedcba9876543210"
                  "fedcba9876543210fedcba9876543210\","
                  "\"expires_at\":\"2026-07-24T12:34:56+00:00\"}") == 0);
    assert(!fp_pair_qr_json("bad-ref", nonce,
                            "2026-07-24T12:34:56Z", qr, sizeof(qr)));

    char prov[FP_PROV_QR_JSON_MAX];
    assert(fp_provision_qr_json("PROV_A1B2C3", "fp-0123456789abcdef",
                                "0123456789abcdef0123456789abcdef"
                                "0123456789abcdef",
                                prov, sizeof(prov)));
    assert(strcmp(prov,
                  "{\"ver\":\"v1\",\"name\":\"PROV_A1B2C3\","
                  "\"username\":\"fp-0123456789abcdef\","
                  "\"pop\":\"0123456789abcdef0123456789abcdef"
                  "0123456789abcdef\",\"transport\":\"ble\"}") == 0);

    int64_t epoch = -1;
    assert(fp_rfc3339_utc_epoch("1970-01-01T00:00:00Z", &epoch));
    assert(epoch == 0);
    assert(fp_rfc3339_utc_epoch(
        "2024-02-29T12:34:56.123456+00:00", &epoch));
    assert(epoch == 1709210096);
    assert(!fp_rfc3339_utc_epoch(
        "2023-02-29T00:00:00+00:00", &epoch));
    assert(!fp_rfc3339_utc_epoch(
        "2024-01-01T00:00:00+08:00", &epoch));
    uint32_t remaining = 99;
    assert(fp_pair_expiry_classify(
               "2026-07-24T12:34:56+00:00", 0, false, &remaining) ==
           FP_PAIR_EXPIRY_UNKNOWN);
    assert(remaining == 0);
    assert(fp_pair_expiry_classify(
               "not-a-time", 1700000000, true, &remaining) ==
           FP_PAIR_EXPIRY_UNKNOWN);
    assert(fp_pair_expiry_classify(
               "2024-02-29T12:34:56+00:00", 1709210095, true,
               &remaining) == FP_PAIR_EXPIRY_LIVE);
    assert(remaining == 1);
    assert(fp_pair_expiry_classify(
               "2024-02-29T12:34:56+00:00", 1709210096, true,
               &remaining) == FP_PAIR_EXPIRY_EXPIRED);

    assert(fp_setup_credential_source(true, false) ==
           FP_SETUP_CREDENTIAL_FACTORY);
    assert(fp_setup_credential_source(false, true) ==
           FP_SETUP_CREDENTIAL_BYOS);
    assert(fp_setup_credential_source(false, false) ==
           FP_SETUP_CREDENTIAL_MISSING);

    assert(fp_button_classify(2, 0, 2000, 10000) == FP_BUTTON_POLL);
    assert(fp_button_classify(1, 0, 2000, 10000) == FP_BUTTON_REPAIR);
    assert(fp_button_classify(0, 1999, 2000, 10000) == FP_BUTTON_NONE);
    assert(fp_button_classify(0, 2000, 2000, 10000) ==
           FP_BUTTON_REPROVISION);
    assert(fp_button_classify(0, 10000, 2000, 10000) ==
           FP_BUTTON_FACTORY_RESET);

    assert(fp_prov_transition_allowed(FP_PROV_STATE_EMPTY,
                                      FP_PROV_STATE_QR_READY));
    assert(fp_prov_transition_allowed(FP_PROV_STATE_SETUP_PENDING,
                                      FP_PROV_STATE_PAIR_READY));
    assert(fp_prov_transition_allowed(FP_PROV_STATE_PAIR_READY,
                                      FP_PROV_STATE_COMPLETE));
    assert(fp_prov_transition_allowed(FP_PROV_STATE_COMPLETE,
                                      FP_PROV_STATE_REPAIR_INTENT));
    assert(!fp_prov_transition_allowed(FP_PROV_STATE_COMPLETE,
                                       FP_PROV_STATE_PAIR_READY));
    assert(fp_repair_registration_allowed(0));
    assert(!fp_repair_registration_allowed(1));
    assert(!fp_repair_registration_allowed(180));
    assert(fp_repair_target_supported(true));
    assert(!fp_repair_target_supported(false));

    assert(fp_reset_erases_namespace("flightportrait"));
    assert(!fp_reset_erases_namespace("fp_factory"));
    assert(!fp_reset_erases_namespace("unrelated"));

    puts("pairing_contract: all cases pass");
    return 0;
}
