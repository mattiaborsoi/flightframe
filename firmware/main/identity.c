/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "identity.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "esp_random.h"
#include "esp_system.h"
#include "mbedtls/base64.h"
#include "mbedtls/ecdsa.h"
#include "mbedtls/ecp.h"
#include "mbedtls/sha256.h"
#include "nvs.h"
#include "sdkconfig.h"

#include "esp_srp.h"
#include "nvs_schema.h"

#define FP_P256_PRIVATE_BYTES 32
#define FP_SEC2_SALT_BYTES    16

static int random_cb(void *ctx, unsigned char *out, size_t len)
{
    (void)ctx;
    esp_fill_random(out, len);
    return 0;
}

static esp_err_t nvs_get_string_from(const char *ns, const char *key,
                                     char *out, size_t cap)
{
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(ns, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    size_t len = cap;
    err = nvs_get_str(nvs, key, out, &len);
    nvs_close(nvs);
    return err;
}

static esp_err_t nvs_get_blob_alloc(const char *key, char **out,
                                    uint16_t *out_len)
{
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    size_t len = 0;
    err = nvs_get_blob(nvs, key, NULL, &len);
    if (err != ESP_OK || len == 0 || len > UINT16_MAX) {
        nvs_close(nvs);
        return err == ESP_OK ? ESP_ERR_INVALID_SIZE : err;
    }
    char *buf = malloc(len);
    if (!buf) {
        nvs_close(nvs);
        return ESP_ERR_NO_MEM;
    }
    err = nvs_get_blob(nvs, key, buf, &len);
    nvs_close(nvs);
    if (err != ESP_OK) {
        free(buf);
        return err;
    }
    *out = buf;
    *out_len = (uint16_t)len;
    return ESP_OK;
}

static esp_err_t base64url_no_pad(const uint8_t *src, size_t src_len,
                                  char *out, size_t cap)
{
    size_t olen = 0;
    int rc = mbedtls_base64_encode((unsigned char *)out, cap, &olen,
                                   src, src_len);
    if (rc != 0 || olen >= cap) {
        return ESP_ERR_INVALID_SIZE;
    }
    while (olen && out[olen - 1] == '=') {
        --olen;
    }
    for (size_t i = 0; i < olen; ++i) {
        if (out[i] == '+') {
            out[i] = '-';
        } else if (out[i] == '/') {
            out[i] = '_';
        }
    }
    out[olen] = '\0';
    return ESP_OK;
}

esp_err_t fp_factory_seed_dev_credential(void)
{
    char existing[FP_FACTORY_CREDENTIAL_MAX];
    if (fp_factory_credential_get(existing, sizeof(existing)) == ESP_OK) {
        memset(existing, 0, sizeof(existing));
        return ESP_OK;
    }
    memset(existing, 0, sizeof(existing));
#if defined(CONFIG_FP_DEV_PROVISION_SECRET)
    if (CONFIG_FP_DEV_PROVISION_SECRET[0] == '\0') {
        return ESP_ERR_NOT_FOUND;
    }
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_FACTORY_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_set_str(nvs, FP_FACTORY_SETUP_CREDENTIAL,
                      CONFIG_FP_DEV_PROVISION_SECRET);
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    nvs_close(nvs);
    return err;
#else
    return ESP_ERR_NOT_FOUND;
#endif
}

esp_err_t fp_factory_credential_get(char *out, size_t cap)
{
    return nvs_get_string_from(FP_FACTORY_NVS_NAMESPACE,
                               FP_FACTORY_SETUP_CREDENTIAL, out, cap);
}

fp_reset_cleanup_state_t fp_factory_reset_cleanup_state(void)
{
    uint8_t state = FP_RESET_CLEANUP_NONE;
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(
        FP_FACTORY_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return FP_RESET_CLEANUP_NONE;
    }
    if (err != ESP_OK) {
        return FP_RESET_CLEANUP_ERASE_AND_CLOUD_PENDING;
    }
    err = nvs_get_u8(nvs, FP_FACTORY_RESET_CLEANUP, &state);
    nvs_close(nvs);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return FP_RESET_CLEANUP_NONE;
    }
    if (err != ESP_OK) {
        /* A malformed protected journal must not be treated as a completed
         * reset or silently discarded. */
        return FP_RESET_CLEANUP_ERASE_AND_CLOUD_PENDING;
    }
    if (fp_reset_cleanup_resume_erase(state)) {
        return fp_reset_cleanup_cloud_required(state)
            ? FP_RESET_CLEANUP_ERASE_AND_CLOUD_PENDING
            : FP_RESET_CLEANUP_ERASE_PENDING;
    }
    return (fp_reset_cleanup_state_t)state;
}

esp_err_t fp_factory_reset_cleanup_set(fp_reset_cleanup_state_t state)
{
    if (state > FP_RESET_CLEANUP_ERASE_AND_CLOUD_PENDING) {
        return ESP_ERR_INVALID_ARG;
    }
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(
        FP_FACTORY_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, FP_FACTORY_RESET_CLEANUP, (uint8_t)state);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    return err;
}

static esp_err_t load_key(mbedtls_ecp_keypair *key)
{
    uint8_t priv[FP_P256_PRIVATE_BYTES] = {0};
    uint8_t pub[FP_PAIR_PUBLIC_SEC1_BYTES] = {0};
    size_t priv_len = sizeof(priv);
    size_t pub_len = sizeof(pub);
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_get_blob(nvs, FP_NVS_PAIR_PRIVATE, priv, &priv_len);
    if (err == ESP_OK) {
        err = nvs_get_blob(nvs, FP_NVS_PAIR_PUBLIC, pub, &pub_len);
    }
    nvs_close(nvs);
    if (err != ESP_OK || priv_len != sizeof(priv) || pub_len != sizeof(pub)) {
        memset(priv, 0, sizeof(priv));
        memset(pub, 0, sizeof(pub));
        return err == ESP_OK ? ESP_ERR_INVALID_SIZE : err;
    }

    mbedtls_ecp_keypair_init(key);
    if (mbedtls_ecp_group_load(&key->MBEDTLS_PRIVATE(grp),
                               MBEDTLS_ECP_DP_SECP256R1) != 0 ||
        mbedtls_mpi_read_binary(&key->MBEDTLS_PRIVATE(d),
                                priv, sizeof(priv)) != 0 ||
        mbedtls_ecp_point_read_binary(&key->MBEDTLS_PRIVATE(grp),
                                      &key->MBEDTLS_PRIVATE(Q),
                                      pub, sizeof(pub)) != 0 ||
        mbedtls_ecp_check_privkey(&key->MBEDTLS_PRIVATE(grp),
                                  &key->MBEDTLS_PRIVATE(d)) != 0 ||
        mbedtls_ecp_check_pubkey(&key->MBEDTLS_PRIVATE(grp),
                                 &key->MBEDTLS_PRIVATE(Q)) != 0) {
        mbedtls_ecp_keypair_free(key);
        memset(priv, 0, sizeof(priv));
        memset(pub, 0, sizeof(pub));
        return ESP_ERR_INVALID_STATE;
    }
    memset(priv, 0, sizeof(priv));
    memset(pub, 0, sizeof(pub));
    return ESP_OK;
}

esp_err_t fp_pairing_ensure_key(void)
{
    mbedtls_ecp_keypair key;
    esp_err_t load_err = load_key(&key);
    if (load_err == ESP_OK) {
        mbedtls_ecp_keypair_free(&key);
        return ESP_OK;
    }
    if (load_err != ESP_ERR_NVS_NOT_FOUND) {
        /* Do not silently rotate a malformed persisted possession identity. */
        return load_err;
    }

    uint8_t priv[FP_P256_PRIVATE_BYTES] = {0};
    uint8_t pub[FP_PAIR_PUBLIC_SEC1_BYTES] = {0};
    size_t pub_len = 0;
    mbedtls_ecp_keypair_init(&key);
    int rc = mbedtls_ecp_gen_key(MBEDTLS_ECP_DP_SECP256R1, &key,
                                 random_cb, NULL);
    if (rc == 0) {
        rc = mbedtls_mpi_write_binary(&key.MBEDTLS_PRIVATE(d),
                                      priv, sizeof(priv));
    }
    if (rc == 0) {
        rc = mbedtls_ecp_point_write_binary(
            &key.MBEDTLS_PRIVATE(grp), &key.MBEDTLS_PRIVATE(Q),
            MBEDTLS_ECP_PF_UNCOMPRESSED,
            &pub_len, pub, sizeof(pub));
    }
    if (rc != 0 || pub_len != sizeof(pub)) {
        mbedtls_ecp_keypair_free(&key);
        memset(priv, 0, sizeof(priv));
        return ESP_FAIL;
    }

    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_blob(nvs, FP_NVS_PAIR_PRIVATE, priv, sizeof(priv));
    }
    if (err == ESP_OK) {
        err = nvs_set_blob(nvs, FP_NVS_PAIR_PUBLIC, pub, sizeof(pub));
    }
    if (err == ESP_OK) {
        err = nvs_set_u32(nvs, FP_NVS_PAIR_COUNTER, 0);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    mbedtls_ecp_keypair_free(&key);
    memset(priv, 0, sizeof(priv));
    memset(pub, 0, sizeof(pub));
    return err;
}

static esp_err_t public_key_b64(char *out, size_t cap)
{
    uint8_t pub[FP_PAIR_PUBLIC_SEC1_BYTES];
    size_t len = sizeof(pub);
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_get_blob(nvs, FP_NVS_PAIR_PUBLIC, pub, &len);
    if (nvs != 0) {
        nvs_close(nvs);
    }
    if (err != ESP_OK || len != sizeof(pub) || pub[0] != 0x04) {
        return err == ESP_OK ? ESP_ERR_INVALID_SIZE : err;
    }
    return base64url_no_pad(pub, sizeof(pub), out, cap);
}

fp_prov_state_t fp_prov_state_get(void)
{
    nvs_handle_t nvs = 0;
    uint8_t state = FP_PROV_STATE_EMPTY;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs) == ESP_OK) {
        nvs_get_u8(nvs, FP_NVS_PROV_STATE, &state);
        nvs_close(nvs);
    }
    if (state > FP_PROV_STATE_REPAIR_VISIBLE) {
        return FP_PROV_STATE_EMPTY;
    }
    return (fp_prov_state_t)state;
}

esp_err_t fp_prov_state_set(fp_prov_state_t state)
{
    fp_prov_state_t old = fp_prov_state_get();
    if (!fp_prov_transition_allowed(old, state)) {
        return ESP_ERR_INVALID_STATE;
    }
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, FP_NVS_PROV_STATE, (uint8_t)state);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    return err;
}

static esp_err_t load_pending(fp_pair_registration_t *out)
{
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    size_t nlen = sizeof(out->nonce);
    size_t hlen = sizeof(out->nonce_hash);
    err = nvs_get_str(nvs, FP_NVS_PAIR_NONCE, out->nonce, &nlen);
    if (err == ESP_OK) {
        err = nvs_get_str(nvs, FP_NVS_PAIR_HASH,
                          out->nonce_hash, &hlen);
    }
    if (err == ESP_OK) {
        err = nvs_get_u32(nvs, FP_NVS_PAIR_COUNTER, &out->counter);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    if (err == ESP_OK) {
        err = public_key_b64(out->public_key_b64,
                             sizeof(out->public_key_b64));
    }
    return err;
}

static esp_err_t store_pending(const fp_pair_registration_t *reg,
                               fp_prov_state_t state)
{
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_str(nvs, FP_NVS_PAIR_NONCE, reg->nonce);
    }
    if (err == ESP_OK) {
        err = nvs_set_str(nvs, FP_NVS_PAIR_HASH, reg->nonce_hash);
    }
    if (err == ESP_OK) {
        err = nvs_set_u32(nvs, FP_NVS_PAIR_COUNTER, reg->counter);
    }
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, FP_NVS_PROV_STATE, (uint8_t)state);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    return err;
}

static esp_err_t generate_pending(fp_pair_registration_t *out,
                                  uint32_t counter,
                                  fp_prov_state_t state)
{
    uint8_t nonce[FP_PAIR_NONCE_BYTES];
    uint8_t digest[32];
    memset(out, 0, sizeof(*out));
    esp_fill_random(nonce, sizeof(nonce));
    if (!fp_hex_lower(nonce, sizeof(nonce), out->nonce,
                      sizeof(out->nonce))) {
        memset(nonce, 0, sizeof(nonce));
        memset(digest, 0, sizeof(digest));
        return ESP_FAIL;
    }
    if (mbedtls_sha256((const unsigned char *)out->nonce,
                       FP_PAIR_NONCE_HEX_LEN, digest, 0) != 0 ||
        !fp_hex_lower(digest, sizeof(digest), out->nonce_hash,
                      sizeof(out->nonce_hash))) {
        memset(nonce, 0, sizeof(nonce));
        memset(digest, 0, sizeof(digest));
        return ESP_FAIL;
    }
    memset(nonce, 0, sizeof(nonce));
    memset(digest, 0, sizeof(digest));
    out->counter = counter;
    esp_err_t err = public_key_b64(out->public_key_b64,
                                   sizeof(out->public_key_b64));
    return err == ESP_OK ? store_pending(out, state) : err;
}

static esp_err_t reset_unacknowledged_first_setup(void)
{
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    const char *keys[] = {
        FP_NVS_DEVICE_TOKEN,
        FP_NVS_DEVICE_REF,
        FP_NVS_PAIR_COUNTER,
        FP_NVS_PAIR_NONCE,
        FP_NVS_PAIR_HASH,
        FP_NVS_PAIR_EXPIRES,
        FP_NVS_PAIR_PRIVATE,
        FP_NVS_PAIR_PUBLIC,
        FP_NVS_PAIR_QR_DRAWN,
    };
    for (size_t i = 0; err == ESP_OK &&
         i < sizeof(keys) / sizeof(keys[0]); ++i) {
        esp_err_t erase_err = nvs_erase_key(nvs, keys[i]);
        if (erase_err != ESP_OK && erase_err != ESP_ERR_NVS_NOT_FOUND) {
            err = erase_err;
        }
    }
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, FP_NVS_SETUP_REQUIRED, 1);
    }
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, FP_NVS_PROV_STATE,
                         (uint8_t)FP_PROV_STATE_QR_READY);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    return err;
}

esp_err_t fp_pairing_prepare_first(fp_pair_registration_t *out)
{
    if (!out) {
        return ESP_ERR_INVALID_ARG;
    }
    fp_prov_state_t state = fp_prov_state_get();
    if (state == FP_PROV_STATE_PAIR_READY) {
        return load_pending(out);
    }
    esp_err_t err = ESP_OK;
    if (state == FP_PROV_STATE_SETUP_PENDING) {
        /* The server may have committed /setup while its response was lost.
         * No pairing bundle was exposed yet, so rotate the unknown identity
         * and safely retry with a fresh key, counter 1, and nonce. */
        err = reset_unacknowledged_first_setup();
        if (err != ESP_OK) {
            return err;
        }
        state = FP_PROV_STATE_QR_READY;
    }
    if (state == FP_PROV_STATE_EMPTY) {
        err = fp_prov_state_set(FP_PROV_STATE_QR_READY);
        if (err != ESP_OK) {
            return err;
        }
        state = FP_PROV_STATE_QR_READY;
    }
    if (state != FP_PROV_STATE_QR_READY) {
        return ESP_ERR_INVALID_STATE;
    }
    err = fp_pairing_ensure_key();
    if (err != ESP_OK) {
        return err;
    }
    return generate_pending(out, 1, FP_PROV_STATE_SETUP_PENDING);
}

static esp_err_t sign_registration(fp_pair_registration_t *reg)
{
    char device_ref[FP_PAIR_DEVICE_REF_MAX + 1];
    if (nvs_get_string_from(FP_NVS_NAMESPACE, FP_NVS_DEVICE_REF,
                            device_ref, sizeof(device_ref)) != ESP_OK) {
        return ESP_ERR_NOT_FOUND;
    }
    char payload[FP_PAIR_SIGNING_MAX];
    if (!fp_pair_signing_payload(device_ref, reg->counter,
                                 reg->nonce_hash,
                                 payload, sizeof(payload))) {
        memset(device_ref, 0, sizeof(device_ref));
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t digest[32];
    if (mbedtls_sha256((const unsigned char *)payload, strlen(payload),
                       digest, 0) != 0) {
        memset(device_ref, 0, sizeof(device_ref));
        memset(payload, 0, sizeof(payload));
        memset(digest, 0, sizeof(digest));
        return ESP_FAIL;
    }

    mbedtls_ecp_keypair key;
    esp_err_t err = load_key(&key);
    if (err != ESP_OK) {
        memset(device_ref, 0, sizeof(device_ref));
        memset(payload, 0, sizeof(payload));
        memset(digest, 0, sizeof(digest));
        return err;
    }
    mbedtls_mpi r;
    mbedtls_mpi s;
    mbedtls_mpi_init(&r);
    mbedtls_mpi_init(&s);
    int rc = mbedtls_ecdsa_sign(&key.MBEDTLS_PRIVATE(grp), &r, &s,
                                &key.MBEDTLS_PRIVATE(d),
                                digest, sizeof(digest), random_cb, NULL);
    uint8_t raw[64];
    if (rc == 0) {
        rc = mbedtls_mpi_write_binary(&r, raw, 32);
    }
    if (rc == 0) {
        rc = mbedtls_mpi_write_binary(&s, raw + 32, 32);
    }
    mbedtls_mpi_free(&r);
    mbedtls_mpi_free(&s);
    mbedtls_ecp_keypair_free(&key);
    memset(device_ref, 0, sizeof(device_ref));
    memset(payload, 0, sizeof(payload));
    memset(digest, 0, sizeof(digest));
    if (rc != 0) {
        memset(raw, 0, sizeof(raw));
        return ESP_FAIL;
    }
    err = base64url_no_pad(raw, sizeof(raw), reg->signature_b64,
                           sizeof(reg->signature_b64));
    memset(raw, 0, sizeof(raw));
    return err;
}

esp_err_t fp_pairing_prepare_repair(fp_pair_registration_t *out)
{
    if (!out) {
        return ESP_ERR_INVALID_ARG;
    }
    fp_prov_state_t state = fp_prov_state_get();
    esp_err_t err;
    if (state != FP_PROV_STATE_REPAIR_INTENT &&
        state != FP_PROV_STATE_REPAIR_REGISTERING) {
        return ESP_ERR_INVALID_STATE;
    }
    uint32_t counter = 0;
    nvs_handle_t nvs = 0;
    err = nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err == ESP_OK) {
        err = nvs_get_u32(nvs, FP_NVS_PAIR_COUNTER, &counter);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    if (err != ESP_OK || counter == UINT32_MAX) {
        return err == ESP_OK ? ESP_ERR_INVALID_STATE : err;
    }
    /* If a request committed but its response was lost, its equal proof may
     * be expired by the next backoff wake. A fresh forward counter is valid
     * whether or not the previous request reached the server. */
    err = generate_pending(out, counter + 1,
                           FP_PROV_STATE_REPAIR_REGISTERING);
    if (err == ESP_OK) {
        err = sign_registration(out);
    }
    return err;
}

static esp_err_t accept_ack(const char *device_ref, uint32_t counter,
                            const char *expires_at, fp_prov_state_t state)
{
    int64_t expires_epoch = 0;
    if (!fp_device_ref_valid(device_ref) || !expires_at ||
        strlen(expires_at) > FP_PAIR_EXPIRES_MAX ||
        !fp_rfc3339_utc_epoch(expires_at, &expires_epoch)) {
        return ESP_ERR_INVALID_ARG;
    }
    fp_pair_registration_t pending;
    memset(&pending, 0, sizeof(pending));
    esp_err_t err = load_pending(&pending);
    if (err != ESP_OK || pending.counter != counter) {
        return ESP_ERR_INVALID_STATE;
    }
    nvs_handle_t nvs = 0;
    err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_str(nvs, FP_NVS_DEVICE_REF, device_ref);
    }
    if (err == ESP_OK) {
        err = nvs_set_str(nvs, FP_NVS_PAIR_EXPIRES, expires_at);
    }
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, FP_NVS_PROV_STATE, (uint8_t)state);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    memset(&pending, 0, sizeof(pending));
    return err;
}

esp_err_t fp_pairing_accept_first_ack(const char *device_ref,
                                      uint32_t counter,
                                      const char *expires_at)
{
    return accept_ack(device_ref, counter, expires_at,
                      FP_PROV_STATE_PAIR_READY);
}

esp_err_t fp_pairing_accept_repair_ack(uint32_t counter,
                                       const char *expires_at)
{
    char device_ref[FP_PAIR_DEVICE_REF_MAX + 1];
    esp_err_t err = nvs_get_string_from(FP_NVS_NAMESPACE,
                                        FP_NVS_DEVICE_REF,
                                        device_ref, sizeof(device_ref));
    return err == ESP_OK
        ? accept_ack(device_ref, counter, expires_at,
                     FP_PROV_STATE_REPAIR_VISIBLE)
        : err;
}

esp_err_t fp_pairing_load_bundle(fp_pair_bundle_t *out)
{
    if (!out) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out, 0, sizeof(*out));
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    size_t dlen = sizeof(out->device_ref);
    size_t nlen = sizeof(out->nonce);
    size_t elen = sizeof(out->expires_at);
    err = nvs_get_str(nvs, FP_NVS_DEVICE_REF, out->device_ref, &dlen);
    if (err == ESP_OK) {
        err = nvs_get_str(nvs, FP_NVS_PAIR_NONCE, out->nonce, &nlen);
    }
    if (err == ESP_OK) {
        err = nvs_get_str(nvs, FP_NVS_PAIR_EXPIRES,
                          out->expires_at, &elen);
    }
    if (err == ESP_OK) {
        err = nvs_get_u32(nvs, FP_NVS_PAIR_COUNTER, &out->counter);
    }
    nvs_close(nvs);
    return err;
}

fp_pair_expiry_t fp_pairing_expiry_status(uint32_t *remaining_out)
{
    if (remaining_out) {
        *remaining_out = 0;
    }
    fp_pair_bundle_t bundle;
    if (fp_pairing_load_bundle(&bundle) != ESP_OK) {
        return FP_PAIR_EXPIRY_UNKNOWN;
    }
    time_t now = 0;
    time(&now);
    fp_pair_expiry_t status = fp_pair_expiry_classify(
        bundle.expires_at, (int64_t)now, now >= 1600000000,
        remaining_out);
    memset(&bundle, 0, sizeof(bundle));
    return status;
}

uint32_t fp_pairing_seconds_remaining(void)
{
    uint32_t remaining = 0;
    fp_pairing_expiry_status(&remaining);
    return remaining;
}

esp_err_t fp_pairing_complete(void)
{
    fp_prov_state_t state = fp_prov_state_get();
    if (state != FP_PROV_STATE_PAIR_READY &&
        state != FP_PROV_STATE_QR_READY &&
        state != FP_PROV_STATE_SETUP_PENDING &&
        state != FP_PROV_STATE_REPAIR_VISIBLE &&
        state != FP_PROV_STATE_REPAIR_REGISTERING &&
        state != FP_PROV_STATE_REPAIR_INTENT) {
        return ESP_ERR_INVALID_STATE;
    }
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, FP_NVS_PROV_STATE,
                         (uint8_t)FP_PROV_STATE_COMPLETE);
    }
    const char *keys[] = {
        FP_NVS_PAIR_NONCE, FP_NVS_PAIR_HASH, FP_NVS_PAIR_EXPIRES,
    };
    for (size_t i = 0; err == ESP_OK && i < sizeof(keys) / sizeof(keys[0]); ++i) {
        esp_err_t e = nvs_erase_key(nvs, keys[i]);
        if (e != ESP_OK && e != ESP_ERR_NVS_NOT_FOUND) {
            err = e;
        }
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    return err;
}

esp_err_t fp_sec2_load_or_create(fp_sec2_credentials_t *out)
{
    if (!out) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out, 0, sizeof(*out));
    esp_err_t user_err = nvs_get_string_from(
        FP_NVS_NAMESPACE, FP_NVS_SEC2_USERNAME,
        out->username, sizeof(out->username));
    esp_err_t pass_err = nvs_get_string_from(
        FP_NVS_NAMESPACE, FP_NVS_SEC2_PASSWORD,
        out->password, sizeof(out->password));
    esp_err_t salt_err = nvs_get_blob_alloc(
        FP_NVS_SEC2_SALT, &out->salt, &out->salt_len);
    esp_err_t verifier_err = nvs_get_blob_alloc(
        FP_NVS_SEC2_VERIFIER, &out->verifier, &out->verifier_len);
    if (user_err == ESP_OK && pass_err == ESP_OK &&
        salt_err == ESP_OK && verifier_err == ESP_OK) {
        return ESP_OK;
    }
    fp_sec2_credentials_free(out);

    uint8_t user_random[8] = {0};
    uint8_t pass_random[24] = {0};
    esp_fill_random(user_random, sizeof(user_random));
    esp_fill_random(pass_random, sizeof(pass_random));
    char user_hex[sizeof(user_random) * 2 + 1] = {0};
    if (!fp_hex_lower(user_random, sizeof(user_random),
                      user_hex, sizeof(user_hex)) ||
        !fp_hex_lower(pass_random, sizeof(pass_random),
                      out->password, sizeof(out->password))) {
        memset(user_random, 0, sizeof(user_random));
        memset(pass_random, 0, sizeof(pass_random));
        memset(user_hex, 0, sizeof(user_hex));
        return ESP_FAIL;
    }
    snprintf(out->username, sizeof(out->username), "fp-%s", user_hex);
    int verifier_len = 0;
    int rc = esp_srp_gen_salt_verifier(
        out->username, strlen(out->username),
        out->password, strlen(out->password),
        &out->salt, FP_SEC2_SALT_BYTES,
        &out->verifier, &verifier_len);
    if (rc != ESP_OK || verifier_len <= 0 || verifier_len > UINT16_MAX) {
        fp_sec2_credentials_free(out);
        memset(user_random, 0, sizeof(user_random));
        memset(pass_random, 0, sizeof(pass_random));
        memset(user_hex, 0, sizeof(user_hex));
        return ESP_FAIL;
    }
    out->salt_len = FP_SEC2_SALT_BYTES;
    out->verifier_len = (uint16_t)verifier_len;

    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_str(nvs, FP_NVS_SEC2_USERNAME, out->username);
    }
    if (err == ESP_OK) {
        err = nvs_set_str(nvs, FP_NVS_SEC2_PASSWORD, out->password);
    }
    if (err == ESP_OK) {
        err = nvs_set_blob(nvs, FP_NVS_SEC2_SALT,
                           out->salt, out->salt_len);
    }
    if (err == ESP_OK) {
        err = nvs_set_blob(nvs, FP_NVS_SEC2_VERIFIER,
                           out->verifier, out->verifier_len);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    if (err != ESP_OK) {
        fp_sec2_credentials_free(out);
    }
    memset(user_random, 0, sizeof(user_random));
    memset(pass_random, 0, sizeof(pass_random));
    memset(user_hex, 0, sizeof(user_hex));
    return err;
}

void fp_sec2_credentials_free(fp_sec2_credentials_t *creds)
{
    if (!creds) {
        return;
    }
    if (creds->salt) {
        memset(creds->salt, 0, creds->salt_len);
        free(creds->salt);
    }
    if (creds->verifier) {
        memset(creds->verifier, 0, creds->verifier_len);
        free(creds->verifier);
    }
    memset(creds, 0, sizeof(*creds));
}

esp_err_t fp_factory_reset_app_state(void)
{
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_erase_all(nvs);
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    nvs_close(nvs);
    return err;
}
