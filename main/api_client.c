/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "api_client.h"

#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "mbedtls/sha256.h"
#include "nvs.h"
#include "sdkconfig.h"

#include "api_base.h"
#include "nvs_schema.h"
#include "target_contract.h"
#include "wifi.h"

static const char *TAG = "fp_api";

#define RESP_MAX 2048                 /* poll responses are <1 KB */
#define URL_MAX  (FP_API_BASE_MAX + 24)   /* base + "/device/v1/display" */

/* ---------------------------------------------------------------- helpers */

static void wipe_json_string(const cJSON *item)
{
    if (cJSON_IsString(item) && item->valuestring) {
        memset(item->valuestring, 0, strlen(item->valuestring));
    }
}

static esp_err_t nvs_get_string(const char *key, char *out, size_t cap)
{
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    size_t len = cap;
    err = nvs_get_str(nvs, key, out, &len);
    nvs_close(nvs);
    return err;
}

static esp_err_t nvs_get_target(char *url, size_t url_cap,
                                char *secret, size_t secret_cap)
{
    uint8_t blob[FP_TARGET_BLOB_MAX];
    size_t len = sizeof(blob);
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_get_blob(nvs, FP_NVS_TARGET_BLOB, blob, &len);
    nvs_close(nvs);
    if (err != ESP_OK) {
        memset(blob, 0, sizeof(blob));
        return err;
    }
    bool valid = fp_target_blob_decode(blob, len, url, url_cap,
                                       secret, secret_cap);
    memset(blob, 0, sizeof(blob));
    return valid ? ESP_OK : ESP_ERR_INVALID_STATE;
}

static bool nvs_has_string(const char *key)
{
    char ignored[2];
    size_t len = sizeof(ignored);
    nvs_handle_t nvs = 0;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs) != ESP_OK) {
        return false;
    }
    esp_err_t err = nvs_get_str(nvs, key, ignored, &len);
    nvs_close(nvs);
    return err == ESP_OK || err == ESP_ERR_NVS_INVALID_LENGTH;
}

static uint8_t nvs_get_flag(const char *key)
{
    uint8_t value = 0;
    nvs_handle_t nvs = 0;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs) == ESP_OK) {
        nvs_get_u8(nvs, key, &value);
        nvs_close(nvs);
    }
    return value;
}

bool fp_api_departure_cleanup_pending(void)
{
    return nvs_get_flag(FP_NVS_DEPART_CLEANUP) != 0 ||
           fp_reset_cleanup_cloud_required(
               fp_factory_reset_cleanup_state());
}

esp_err_t fp_api_departure_cleanup_clear(void)
{
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, FP_NVS_DEPART_CLEANUP, 0);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    if (err != ESP_OK) {
        return err;
    }
    return fp_factory_reset_cleanup_set(FP_RESET_CLEANUP_NONE);
}

bool fp_api_factory_reset_cleanup_needed(void)
{
    uint8_t departure_state = nvs_get_flag(FP_NVS_DEPART_CLEANUP);
    char effective[FP_API_BASE_MAX];
    fp_api_base_get(effective, sizeof(effective));
    bool first_party = strcmp(effective, CONFIG_FP_API_BASE) == 0;
    bool prior_identity =
        nvs_has_string(FP_NVS_DEVICE_TOKEN) ||
        nvs_has_string(FP_NVS_DEVICE_REF);
    return fp_reset_cleanup_cloud_should_stage(
        first_party, prior_identity, departure_state);
}

void fp_api_base_get(char *out, size_t cap)
{
    char target[FP_API_BASE_MAX] = "";
    char secret[FP_FACTORY_CREDENTIAL_MAX] = "";
    if (nvs_get_target(target, sizeof(target),
                       secret, sizeof(secret)) == ESP_OK) {
        strlcpy(out, target[0] ? target : CONFIG_FP_API_BASE, cap);
        memset(target, 0, sizeof(target));
        memset(secret, 0, sizeof(secret));
        return;
    }
    memset(target, 0, sizeof(target));
    memset(secret, 0, sizeof(secret));

    /* Read-only upgrade fallback. The next provisioning target write moves
     * these two legacy keys into FP_NVS_TARGET_BLOB. */
    if (nvs_get_string(FP_NVS_API_BASE, out, cap) == ESP_OK && out[0]) {
        return;
    }
    strlcpy(out, CONFIG_FP_API_BASE, cap);
}

esp_err_t fp_api_byos_setup_get(char *out, size_t cap)
{
    char target[FP_API_BASE_MAX] = "";
    esp_err_t err = nvs_get_target(target, sizeof(target), out, cap);
    memset(target, 0, sizeof(target));
    if (err == ESP_OK) {
        return out[0] ? ESP_OK : ESP_ERR_NOT_FOUND;
    }
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return nvs_get_string(FP_NVS_BYOS_SETUP, out, cap);
    }
    return err;
}

static esp_err_t erase_optional(nvs_handle_t nvs, const char *key)
{
    esp_err_t err = nvs_erase_key(nvs, key);
    return err == ESP_ERR_NVS_NOT_FOUND ? ESP_OK : err;
}

static fp_target_phase_t target_phase_from_nvs(void)
{
    nvs_handle_t nvs = 0;
    uint8_t phase = FP_TARGET_PHASE_READY;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return FP_TARGET_PHASE_READY;
    }
    if (err != ESP_OK) {
        return FP_TARGET_PHASE_NEEDS_RESUBMIT;
    }
    err = nvs_get_u8(nvs, FP_NVS_TARGET_PHASE, &phase);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        uint8_t legacy_required = 0;
        if (nvs_get_u8(nvs, FP_NVS_SETUP_REQUIRED,
                       &legacy_required) == ESP_OK &&
            legacy_required != 0) {
            phase = FP_TARGET_PHASE_SETUP_REQUIRED;
        } else {
            phase = FP_TARGET_PHASE_READY;
        }
        err = ESP_OK;
    }
    nvs_close(nvs);
    if (err != ESP_OK || phase > FP_TARGET_PHASE_SETUP_REQUIRED) {
        return FP_TARGET_PHASE_NEEDS_RESUBMIT;
    }
    return (fp_target_phase_t)phase;
}

fp_target_phase_t fp_api_target_phase(void)
{
    fp_target_phase_t phase = target_phase_from_nvs();
    if (phase != FP_TARGET_PHASE_READY) {
        return phase;
    }
    if (nvs_get_flag(FP_NVS_DEPART_CLEANUP) == 1) {
        /* The intent is written before target mutation. If power failed in
         * that gap, force the phone to resubmit custom vs official intent. */
        return FP_TARGET_PHASE_NEEDS_RESUBMIT;
    }

    /* A malformed blob cannot silently fall back to a different host while
     * retaining a bearer. Absence is the valid pre-journal upgrade case. */
    char target[FP_API_BASE_MAX];
    char secret[FP_FACTORY_CREDENTIAL_MAX];
    esp_err_t err = nvs_get_target(target, sizeof(target),
                                   secret, sizeof(secret));
    memset(target, 0, sizeof(target));
    memset(secret, 0, sizeof(secret));
    return (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND)
        ? FP_TARGET_PHASE_NEEDS_RESUBMIT : phase;
}

esp_err_t fp_api_provisioning_target_set(const char *raw,
                                         const char *setup_secret)
{
    if (!raw) {
        return ESP_ERR_INVALID_ARG;
    }
    char norm[FP_API_BASE_MAX];
    if (fp_api_base_normalize(raw, norm, sizeof(norm)) != 0) {
        ESP_LOGW(TAG, "api_base rejected (scheme/host/length)");
        return ESP_ERR_INVALID_ARG;
    }
    size_t secret_len = setup_secret ? strlen(setup_secret) : 0;
    if (setup_secret &&
        (secret_len == 0 || secret_len >= FP_FACTORY_CREDENTIAL_MAX ||
         norm[0] == '\0' || strcmp(norm, CONFIG_FP_API_BASE) == 0)) {
        ESP_LOGW(TAG, "structured BYOS target rejected");
        return ESP_ERR_INVALID_ARG;
    }
    if (!fp_utf8_valid_no_nul((const uint8_t *)norm, strlen(norm)) ||
        (setup_secret &&
         !fp_utf8_valid_no_nul((const uint8_t *)setup_secret,
                               secret_len))) {
        ESP_LOGW(TAG, "provisioning target is not valid UTF-8");
        return ESP_ERR_INVALID_ARG;
    }

    /* Treat an explicit copy of the compiled default as the default. This
     * keeps target equality stable and avoids retaining a meaningless
     * override. */
    if (strcmp(norm, CONFIG_FP_API_BASE) == 0) {
        norm[0] = '\0';
    }
    const char *desired_secret = setup_secret ? setup_secret : "";
    uint8_t desired_blob[FP_TARGET_BLOB_MAX];
    size_t desired_blob_len = 0;
    if (!fp_target_blob_encode(norm, desired_secret,
                               desired_blob, sizeof(desired_blob),
                               &desired_blob_len)) {
        return ESP_ERR_INVALID_ARG;
    }

    char current_override[FP_API_BASE_MAX] = "";
    char current_secret[FP_FACTORY_CREDENTIAL_MAX] = "";
    esp_err_t current_err = nvs_get_target(
        current_override, sizeof(current_override),
        current_secret, sizeof(current_secret));
    if (current_err == ESP_ERR_NVS_NOT_FOUND) {
        nvs_get_string(FP_NVS_API_BASE, current_override,
                       sizeof(current_override));
        nvs_get_string(FP_NVS_BYOS_SETUP, current_secret,
                       sizeof(current_secret));
        if (strcmp(current_override, CONFIG_FP_API_BASE) == 0) {
            current_override[0] = '\0';
        }
    }
    bool current_first_party =
        (current_err == ESP_OK || current_err == ESP_ERR_NVS_NOT_FOUND) &&
        current_override[0] == '\0';
    bool desired_first_party = norm[0] == '\0';
    bool changed =
        (current_err != ESP_OK &&
         current_err != ESP_ERR_NVS_NOT_FOUND) ||
        strcmp(current_override, norm) != 0 ||
        strcmp(current_secret, desired_secret) != 0;
    fp_target_phase_t phase = target_phase_from_nvs();
    bool resume_interrupted =
        phase == FP_TARGET_PHASE_NEEDS_RESUBMIT;
    bool token_present = nvs_has_string(FP_NVS_DEVICE_TOKEN);
    bool prior_identity_present =
        token_present || nvs_has_string(FP_NVS_DEVICE_REF);
    uint8_t cleanup_state = nvs_get_flag(FP_NVS_DEPART_CLEANUP);
    bool stage_departure_cleanup = fp_departure_cleanup_should_stage(
        current_first_party, desired_first_party, prior_identity_present);
    bool cancel_departure_cleanup = fp_departure_cleanup_cancel_safe(
        cleanup_state, current_first_party, desired_first_party,
        changed, (uint8_t)phase);
    memset(current_override, 0, sizeof(current_override));
    memset(current_secret, 0, sizeof(current_secret));

    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        memset(desired_blob, 0, sizeof(desired_blob));
        return err;
    }

    /* This intent precedes the mutation barrier. If power fails between the
     * two writes, fp_api_target_phase() derives NEEDS_RESUBMIT from it. */
    if (stage_departure_cleanup) {
        err = nvs_set_u8(nvs, FP_NVS_DEPART_CLEANUP, 1);
    } else if (cancel_departure_cleanup) {
        err = nvs_set_u8(nvs, FP_NVS_DEPART_CLEANUP, 0);
    }
    if (err == ESP_OK &&
        (stage_departure_cleanup || cancel_departure_cleanup)) {
        err = nvs_commit(nvs);
    }
    if (err == ESP_OK && (changed || resume_interrupted)) {
        /* First durable write: from this point through the final phase-2
         * write, no bearer or cloud target is usable after any power cut. */
        err = nvs_set_u8(nvs, FP_NVS_TARGET_PHASE,
                         FP_TARGET_PHASE_NEEDS_RESUBMIT);
    }
    if (err == ESP_OK && (changed || resume_interrupted)) {
        err = nvs_commit(nvs);
    }
    if (err == ESP_OK &&
        (changed || resume_interrupted ||
         current_err == ESP_ERR_NVS_NOT_FOUND)) {
        /* One blob write atomically replaces URL+secret. */
        err = nvs_set_blob(nvs, FP_NVS_TARGET_BLOB,
                           desired_blob, desired_blob_len);
    }
    if (err == ESP_OK &&
        (changed || resume_interrupted ||
         current_err == ESP_ERR_NVS_NOT_FOUND)) {
        err = nvs_commit(nvs);
    }
    if (err == ESP_OK && (changed || resume_interrupted)) {
        /* The bearer, public ref, and possession key are scoped to one
         * server. Never carry any of them across a target/credential change.
         * The phase-1 barrier was persisted before this multi-key cleanup. */
        const char *server_scoped[] = {
            FP_NVS_DEVICE_TOKEN,
            FP_NVS_DEVICE_REF,
            FP_NVS_PAIR_COUNTER,
            FP_NVS_PAIR_NONCE,
            FP_NVS_PAIR_HASH,
            FP_NVS_PAIR_EXPIRES,
            FP_NVS_PAIR_PRIVATE,
            FP_NVS_PAIR_PUBLIC,
            FP_NVS_PAIR_QR_DRAWN,
            FP_NVS_IMAGE_HASH,
        };
        for (size_t i = 0;
             err == ESP_OK &&
             i < sizeof(server_scoped) / sizeof(server_scoped[0]);
             ++i) {
            err = erase_optional(nvs, server_scoped[i]);
        }
    }
    if (err == ESP_OK && (changed || resume_interrupted)) {
        err = nvs_commit(nvs);
    }
    if (err == ESP_OK &&
        (changed || resume_interrupted ||
         current_err == ESP_ERR_NVS_NOT_FOUND)) {
        err = erase_optional(nvs, FP_NVS_API_BASE);
    }
    if (err == ESP_OK &&
        (changed || resume_interrupted ||
         current_err == ESP_ERR_NVS_NOT_FOUND)) {
        err = erase_optional(nvs, FP_NVS_BYOS_SETUP);
    }
    if (err == ESP_OK &&
        (changed || resume_interrupted ||
         current_err == ESP_ERR_NVS_NOT_FOUND)) {
        err = nvs_commit(nvs);
    }
    if (err == ESP_OK && (changed || resume_interrupted)) {
        err = nvs_set_u8(nvs, FP_NVS_SETUP_REQUIRED, 1);
    }
    if (err == ESP_OK && (changed || resume_interrupted)) {
        err = nvs_set_u8(nvs, FP_NVS_PROV_STATE,
                         (uint8_t)FP_PROV_STATE_QR_READY);
    }
    if (err == ESP_OK && (changed || resume_interrupted)) {
        err = nvs_commit(nvs);
    }
    if (err == ESP_OK && !desired_first_party &&
        (stage_departure_cleanup || cleanup_state != 0)) {
        /* Phase 2 means the new target is coherent; value 2 may safely remain
         * across non-blocking cleanup failures without denying BYOS tokens. */
        err = nvs_set_u8(nvs, FP_NVS_DEPART_CLEANUP, 2);
    }
    if (err == ESP_OK && !desired_first_party &&
        (stage_departure_cleanup || cleanup_state != 0)) {
        err = nvs_commit(nvs);
    }
    if (err == ESP_OK && (changed || resume_interrupted)) {
        /* Last durable write: the target is coherent and may be used for
         * setup, but token use remains denied until the setup ack commits. */
        err = nvs_set_u8(nvs, FP_NVS_TARGET_PHASE,
                         FP_TARGET_PHASE_SETUP_REQUIRED);
        if (err == ESP_OK) {
            err = nvs_commit(nvs);
        }
    } else if (err == ESP_OK && !token_present &&
               phase == FP_TARGET_PHASE_READY) {
        err = nvs_set_u8(nvs, FP_NVS_SETUP_REQUIRED, 1);
        if (err == ESP_OK) {
            err = nvs_commit(nvs);
        }
        if (err == ESP_OK) {
            err = nvs_set_u8(nvs, FP_NVS_TARGET_PHASE,
                             FP_TARGET_PHASE_SETUP_REQUIRED);
        }
        if (err == ESP_OK) {
            err = nvs_commit(nvs);
        }
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    memset(desired_blob, 0, sizeof(desired_blob));
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "provisioning target %s",
                 (changed || resume_interrupted)
                     ? "journal complete; setup required" : "unchanged");
    }
    return err;
}

bool fp_api_setup_required(void)
{
    return fp_api_target_phase() != FP_TARGET_PHASE_READY;
}

bool fp_api_token_allowed(void)
{
    return fp_target_token_allowed((uint8_t)fp_api_target_phase());
}

esp_err_t fp_api_setup_mark_required(void)
{
    fp_target_phase_t phase = fp_api_target_phase();
    if (phase == FP_TARGET_PHASE_NEEDS_RESUBMIT) {
        return ESP_ERR_INVALID_STATE;
    }
    if (phase == FP_TARGET_PHASE_SETUP_REQUIRED) {
        return ESP_OK;
    }
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, FP_NVS_SETUP_REQUIRED, 1);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, FP_NVS_TARGET_PHASE,
                         FP_TARGET_PHASE_SETUP_REQUIRED);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    return err;
}

esp_err_t fp_api_setup_mark_complete(void)
{
    fp_target_phase_t phase = fp_api_target_phase();
    if (phase == FP_TARGET_PHASE_NEEDS_RESUBMIT) {
        return ESP_ERR_INVALID_STATE;
    }
    char effective[FP_API_BASE_MAX];
    fp_api_base_get(effective, sizeof(effective));
    if (strcmp(effective, CONFIG_FP_API_BASE) == 0 &&
        fp_api_departure_cleanup_pending()) {
        /* A successful official /setup has itself severed the old official
         * account state. Clear departure/reset cleanup journals only now,
         * never before the target transition is durable. */
        esp_err_t cleanup_err = fp_api_departure_cleanup_clear();
        if (cleanup_err != ESP_OK) {
            return cleanup_err;
        }
    }
    if (phase == FP_TARGET_PHASE_READY) {
        return ESP_OK;
    }
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_u8(nvs, FP_NVS_SETUP_REQUIRED, 0);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (err == ESP_OK) {
        /* Last durable write: local setup/pairing state already committed. */
        err = nvs_set_u8(nvs, FP_NVS_TARGET_PHASE,
                         FP_TARGET_PHASE_READY);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    return err;
}

static void auth_header(esp_http_client_handle_t http)
{
    char token[80], bearer[96];
    if (fp_api_token_allowed() &&
        nvs_get_string(FP_NVS_DEVICE_TOKEN, token, sizeof(token)) == ESP_OK) {
        snprintf(bearer, sizeof(bearer), "Bearer %s", token);
        esp_http_client_set_header(http, "Authorization", bearer);
    }
    memset(token, 0, sizeof(token));
    memset(bearer, 0, sizeof(bearer));
}

static void telemetry_headers(esp_http_client_handle_t http,
                              const char *boot_reason)
{
    char buf[16];
    int rssi = fp_wifi_rssi();
    if (rssi) {
        snprintf(buf, sizeof(buf), "%d", rssi);
        esp_http_client_set_header(http, "X-Rssi", buf);
    }
    /* TODO(hw): battery mV via ADC (GPIO1 on the E1004) / MAX17048 later.
     * TODO(hw): X-Panel-Temp — the T133A01 has an onboard I2C temp sensor
     * (TSCL/TSDA); if the E1004 routes it, this header becomes real data. */
    esp_http_client_set_header(http, "X-Fw-Version",
                               esp_app_get_description()->version);
    esp_http_client_set_header(http, "X-Boot-Reason", boot_reason);
}

/* Perform a request whose response body fits in RESP_MAX. */
static esp_err_t small_request(esp_http_client_handle_t http,
                               const char *body, char *resp, int *resp_len)
{
    esp_err_t err = esp_http_client_open(http, body ? strlen(body) : 0);
    if (err != ESP_OK) {
        return err;
    }
    if (body) {
        esp_http_client_write(http, body, strlen(body));
    }
    esp_http_client_fetch_headers(http);
    int n = esp_http_client_read_response(http, resp, RESP_MAX - 1);
    int status = esp_http_client_get_status_code(http);
    esp_http_client_close(http);
    if (n < 0) {
        return ESP_FAIL;
    }
    resp[n] = 0;
    *resp_len = n;
    if (status != 200) {
        /* Bodies can echo validation inputs. Never put setup credentials,
         * nonces, signatures, control tokens, or QR material in logs. */
        ESP_LOGW(TAG, "HTTP %d (%d-byte response)", status, n);
        return ESP_FAIL;
    }
    return ESP_OK;
}

/* ------------------------------------------------------------------ setup */

esp_err_t fp_api_departure_cleanup(const char *factory_credential)
{
    if (!factory_credential || factory_credential[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    char mac_text[18];
    snprintf(mac_text, sizeof(mac_text),
             "%02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    cJSON *request = cJSON_CreateObject();
    if (!request) {
        return ESP_ERR_NO_MEM;
    }
    cJSON_AddStringToObject(request, "mac", mac_text);
    cJSON_AddStringToObject(request, "hw_rev", CONFIG_FP_HW_REV);
    cJSON_AddStringToObject(request, "provision_secret",
                            factory_credential);
    char *body = cJSON_PrintUnformatted(request);
    cJSON_Delete(request);
    if (!body) {
        return ESP_ERR_NO_MEM;
    }

    /* Intentionally fixed: never resolve this through the effective BYOS
     * target. No pairing fields are included and the returned token is not
     * persisted. The ordinary setup semantics unlink account/personal state. */
    char url[URL_MAX];
    snprintf(url, sizeof(url), "%s/device/v1/setup", CONFIG_FP_API_BASE);
    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 15000,
    };
    esp_http_client_handle_t http = esp_http_client_init(&cfg);
    if (!http) {
        memset(body, 0, strlen(body));
        cJSON_free(body);
        return ESP_ERR_NO_MEM;
    }
    esp_http_client_set_header(http, "Content-Type", "application/json");
    char resp[RESP_MAX] = {0};
    int n = 0;
    esp_err_t err = small_request(http, body, resp, &n);
    esp_http_client_cleanup(http);
    memset(body, 0, strlen(body));
    cJSON_free(body);
    if (err == ESP_OK) {
        if (fp_json_contains_nul_escape(
                (const uint8_t *)resp, (size_t)n)) {
            err = ESP_FAIL;
        }
        cJSON *json = err == ESP_OK
            ? cJSON_ParseWithLength(resp, (size_t)n) : NULL;
        const cJSON *token = json
            ? cJSON_GetObjectItemCaseSensitive(json, "device_token") : NULL;
        if (!cJSON_IsString(token) ||
            !fp_device_token_valid(token->valuestring)) {
            err = ESP_FAIL;
        }
        wipe_json_string(token);
        cJSON_Delete(json);
    }
    memset(resp, 0, sizeof(resp));
    return err;
}

esp_err_t fp_api_departure_cleanup_attempt(void)
{
    uint8_t cleanup_state = nvs_get_flag(FP_NVS_DEPART_CLEANUP);
    fp_reset_cleanup_state_t reset_state =
        fp_factory_reset_cleanup_state();
    if (cleanup_state == 0 && reset_state == FP_RESET_CLEANUP_NONE) {
        return ESP_OK;
    }
    char effective[FP_API_BASE_MAX];
    fp_api_base_get(effective, sizeof(effective));
    bool first_party = strcmp(effective, CONFIG_FP_API_BASE) == 0;
    if (!fp_departure_cleanup_should_run(cleanup_state, first_party) &&
        !fp_reset_cleanup_should_run(reset_state, first_party)) {
        return ESP_ERR_INVALID_STATE;
    }

    char credential[FP_FACTORY_CREDENTIAL_MAX] = {0};
    esp_err_t err = fp_factory_credential_get(
        credential, sizeof(credential));
    if (err == ESP_OK) {
        err = fp_api_departure_cleanup(credential);
    }
    memset(credential, 0, sizeof(credential));
    if (err == ESP_OK) {
        err = fp_api_departure_cleanup_clear();
    }
    return err;
}

esp_err_t fp_api_setup(const char *provision_secret,
                       const fp_pair_registration_t *pairing,
                       fp_setup_result_t *out)
{
    if (!provision_secret ||
        fp_api_setup_mark_required() != ESP_OK) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    cJSON *request = cJSON_CreateObject();
    if (!request) {
        return ESP_ERR_NO_MEM;
    }
    char mac_text[18];
    snprintf(mac_text, sizeof(mac_text),
             "%02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    cJSON_AddStringToObject(request, "mac", mac_text);
    cJSON_AddStringToObject(request, "hw_rev", CONFIG_FP_HW_REV);
    cJSON_AddStringToObject(request, "provision_secret", provision_secret);
    if (pairing) {
        cJSON_AddStringToObject(request, "pairing_public_key",
                                pairing->public_key_b64);
        cJSON_AddNumberToObject(request, "pairing_counter",
                               pairing->counter);
        cJSON_AddStringToObject(request, "pairing_nonce_hash",
                                pairing->nonce_hash);
    }
    char *body = cJSON_PrintUnformatted(request);
    cJSON_Delete(request);
    if (!body) {
        return ESP_ERR_NO_MEM;
    }

    char base[FP_API_BASE_MAX], url[URL_MAX];
    fp_api_base_get(base, sizeof(base));
    snprintf(url, sizeof(url), "%s/device/v1/setup", base);
    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 15000,
    };
    esp_http_client_handle_t http = esp_http_client_init(&cfg);
    if (!http) {
        memset(body, 0, strlen(body));
        cJSON_free(body);
        return ESP_ERR_NO_MEM;
    }
    esp_http_client_set_header(http, "Content-Type", "application/json");

    char resp[RESP_MAX];
    int n = 0;
    esp_err_t err = small_request(http, body, resp, &n);
    esp_http_client_cleanup(http);
    memset(body, 0, strlen(body));
    cJSON_free(body);
    if (err != ESP_OK) {
        memset(resp, 0, sizeof(resp));
        return err;
    }

    if (fp_json_contains_nul_escape(
            (const uint8_t *)resp, (size_t)n)) {
        memset(resp, 0, sizeof(resp));
        return ESP_FAIL;
    }
    cJSON *json = cJSON_ParseWithLength(resp, (size_t)n);
    const cJSON *tok = json ? cJSON_GetObjectItemCaseSensitive(
                                      json, "device_token") : NULL;
    if (!cJSON_IsString(tok) ||
        !fp_device_token_valid(tok->valuestring)) {
        wipe_json_string(tok);
        cJSON_Delete(json);
        memset(resp, 0, sizeof(resp));
        return ESP_FAIL;
    }
    fp_setup_result_t parsed = {0};
    const cJSON *device_ref = cJSON_GetObjectItemCaseSensitive(
        json, "device_ref");
    const cJSON *pair_ack = cJSON_GetObjectItemCaseSensitive(json, "pairing");
    if (pairing) {
        if (!cJSON_IsString(device_ref) ||
            !fp_device_ref_valid(device_ref->valuestring) ||
            !cJSON_IsObject(pair_ack)) {
            wipe_json_string(tok);
            cJSON_Delete(json);
            memset(resp, 0, sizeof(resp));
            return ESP_FAIL;
        }
        const cJSON *counter = cJSON_GetObjectItemCaseSensitive(
            pair_ack, "counter");
        const cJSON *expires = cJSON_GetObjectItemCaseSensitive(
            pair_ack, "expires_at");
        if (!cJSON_IsNumber(counter) ||
            !cJSON_IsString(expires) ||
            !fp_pairing_ack_valid(
                counter->valuedouble, expires->valuestring,
                pairing->counter, &parsed.pairing_counter)) {
            wipe_json_string(tok);
            cJSON_Delete(json);
            memset(resp, 0, sizeof(resp));
            return ESP_FAIL;
        }
        strlcpy(parsed.device_ref, device_ref->valuestring,
                sizeof(parsed.device_ref));
        parsed.has_pairing_ack = true;
        strlcpy(parsed.pairing_expires_at, expires->valuestring,
                sizeof(parsed.pairing_expires_at));
    }

    nvs_handle_t nvs = 0;
    err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_str(nvs, FP_NVS_DEVICE_TOKEN, tok->valuestring);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    if (nvs != 0) {
        nvs_close(nvs);
    }
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "setup accepted; device credential stored");
        if (out) {
            *out = parsed;
        }
    }
    wipe_json_string(tok);
    cJSON_Delete(json);
    memset(resp, 0, sizeof(resp));
    return err;
}

/* ---------------------------------------------------------------- display */

esp_err_t fp_api_get_display(const char *boot_reason,
                             const fp_pair_registration_t *pairing,
                             fp_display_t *out)
{
    if (!boot_reason || !out || !fp_api_token_allowed()) {
        return ESP_ERR_INVALID_STATE;
    }
    char base[FP_API_BASE_MAX], req_url[URL_MAX];
    fp_api_base_get(base, sizeof(base));
    snprintf(req_url, sizeof(req_url), "%s/device/v1/display", base);
    esp_http_client_config_t cfg = {
        .url = req_url,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 20000,
    };
    esp_http_client_handle_t http = esp_http_client_init(&cfg);
    if (!http) {
        return ESP_ERR_NO_MEM;
    }
    auth_header(http);
    telemetry_headers(http, boot_reason);
    if (pairing) {
        char counter[16];
        snprintf(counter, sizeof(counter), "%lu",
                 (unsigned long)pairing->counter);
        esp_http_client_set_header(http, "X-Pairing-Nonce-Hash",
                                   pairing->nonce_hash);
        esp_http_client_set_header(http, "X-Pairing-Counter", counter);
        esp_http_client_set_header(http, "X-Pairing-Signature",
                                   pairing->signature_b64);
    }

    char resp[RESP_MAX];
    int n = 0;
    esp_err_t err = small_request(http, NULL, resp, &n);
    esp_http_client_cleanup(http);
    if (err != ESP_OK) {
        return err;
    }

    if (fp_json_contains_nul_escape(
            (const uint8_t *)resp, (size_t)n)) {
        memset(resp, 0, sizeof(resp));
        return ESP_FAIL;
    }
    cJSON *json = cJSON_ParseWithLength(resp, (size_t)n);
    if (!cJSON_IsObject(json)) {
        cJSON_Delete(json);
        memset(resp, 0, sizeof(resp));
        return ESP_FAIL;
    }
    const cJSON *url = cJSON_GetObjectItem(json, "image_url");
    const cJSON *hash = cJSON_GetObjectItem(json, "image_hash");
    const cJSON *sleep_s = cJSON_GetObjectItem(json, "sleep_s");
    const cJSON *reset = cJSON_GetObjectItem(json, "reset");
    fp_display_t parsed = {0};
    if (!cJSON_IsString(url) || !cJSON_IsString(hash) ||
        !cJSON_IsNumber(sleep_s) || !cJSON_IsBool(reset) ||
        !fp_display_required_fields_valid(
            url->valuestring, sizeof(parsed.image_url),
            hash->valuestring, sleep_s->valuedouble,
            &parsed.sleep_s)) {
        cJSON_Delete(json);
        memset(resp, 0, sizeof(resp));
        return ESP_FAIL;
    }
    strlcpy(parsed.image_url, url->valuestring,
            sizeof(parsed.image_url));
    strlcpy(parsed.image_hash, hash->valuestring,
            sizeof(parsed.image_hash));
    parsed.reset = cJSON_IsTrue(reset);
    const cJSON *pair_ack = cJSON_GetObjectItemCaseSensitive(json, "pairing");
    if (pair_ack) {
        if (!pairing || !cJSON_IsObject(pair_ack)) {
            cJSON_Delete(json);
            memset(resp, 0, sizeof(resp));
            return ESP_FAIL;
        }
        const cJSON *counter = cJSON_GetObjectItemCaseSensitive(
            pair_ack, "counter");
        const cJSON *expires = cJSON_GetObjectItemCaseSensitive(
            pair_ack, "expires_at");
        if (!cJSON_IsNumber(counter) || !cJSON_IsString(expires) ||
            !fp_pairing_ack_valid(
                counter->valuedouble, expires->valuestring,
                pairing->counter, &parsed.pairing_counter)) {
            cJSON_Delete(json);
            memset(resp, 0, sizeof(resp));
            return ESP_FAIL;
        }
        parsed.has_pairing_ack = true;
        strlcpy(parsed.pairing_expires_at, expires->valuestring,
                sizeof(parsed.pairing_expires_at));
    }
    const cJSON *fw = cJSON_GetObjectItem(json, "firmware");
    if (fw && !cJSON_IsNull(fw)) {
        const cJSON *fu = cJSON_GetObjectItem(fw, "url");
        const cJSON *fs = cJSON_GetObjectItem(fw, "sha256");
        const cJSON *fv = cJSON_GetObjectItem(fw, "version");
        if (!cJSON_IsObject(fw) ||
            !cJSON_IsString(fu) || !cJSON_IsString(fs) ||
            !cJSON_IsString(fv) ||
            !fp_http_url_fits(
                fu->valuestring, sizeof(parsed.fw_url)) ||
            !fp_ota_hash_valid(fs->valuestring) ||
            !fp_nonempty_text_fits(
                fv->valuestring, sizeof(parsed.fw_version))) {
            cJSON_Delete(json);
            memset(resp, 0, sizeof(resp));
            return ESP_FAIL;
        }
        parsed.has_fw = true;
        strlcpy(parsed.fw_url, fu->valuestring,
                sizeof(parsed.fw_url));
        strlcpy(parsed.fw_sha256, fs->valuestring,
                sizeof(parsed.fw_sha256));
        strlcpy(parsed.fw_version, fv->valuestring,
                sizeof(parsed.fw_version));
    }
    *out = parsed;
    cJSON_Delete(json);
    memset(resp, 0, sizeof(resp));
    return ESP_OK;
}

/* -------------------------------------------------------------------- log */

esp_err_t fp_api_post_logs(const char *body, const char *boot_reason)
{
    if (!body || !boot_reason || !fp_api_token_allowed()) {
        return ESP_ERR_INVALID_STATE;
    }
    char base[FP_API_BASE_MAX], req_url[URL_MAX];
    fp_api_base_get(base, sizeof(base));
    snprintf(req_url, sizeof(req_url), "%s/device/v1/log", base);
    esp_http_client_config_t cfg = {
        .url = req_url,
        .method = HTTP_METHOD_POST,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 15000,
    };
    esp_http_client_handle_t http = esp_http_client_init(&cfg);
    if (!http) {
        return ESP_ERR_NO_MEM;
    }
    esp_http_client_set_header(http, "Content-Type", "application/json");
    auth_header(http);
    telemetry_headers(http, boot_reason);
    char resp[RESP_MAX];
    int n = 0;
    esp_err_t err = small_request(http, body, resp, &n);
    esp_http_client_cleanup(http);
    return err;
}

/* --------------------------------------------------------------- download */

esp_err_t fp_api_download(const char *url, const char *expected_hash,
                          uint8_t *buf)
{
    if (!buf || !fp_http_url_fits(
            url, sizeof(((fp_display_t *)0)->image_url)) ||
        !fp_image_hash_valid(expected_hash)) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_http_client_config_t cfg = {
        .url = url,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 30000,
    };
    esp_http_client_handle_t http = esp_http_client_init(&cfg);
    if (!http) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = esp_http_client_open(http, 0);
    if (err != ESP_OK) {
        esp_http_client_cleanup(http);
        return err;
    }
    esp_http_client_fetch_headers(http);

    uint32_t got = 0;
    while (got < FP_IMAGE_BYTES) {
        int n = esp_http_client_read(http, (char *)buf + got,
                                     FP_IMAGE_BYTES - got);
        if (n <= 0) {
            break;
        }
        got += n;
    }
    /* Anything beyond the expected size is a protocol violation. */
    char extra;
    bool oversize = esp_http_client_read(http, &extra, 1) > 0;
    int status = esp_http_client_get_status_code(http);
    esp_http_client_close(http);
    esp_http_client_cleanup(http);

    if (status != 200 || got != FP_IMAGE_BYTES || oversize) {
        ESP_LOGW(TAG, "download bad: HTTP %d, %lu bytes%s", status,
                 (unsigned long)got, oversize ? " (oversize)" : "");
        return ESP_FAIL;
    }

    unsigned char digest[32];
    mbedtls_sha256(buf, FP_IMAGE_BYTES, digest, 0);
    char hex[7 + 64 + 1] = "sha256:";
    for (int i = 0; i < 32; i++) {
        snprintf(hex + 7 + i * 2, 3, "%02x", digest[i]);
    }
    if (strcmp(hex, expected_hash) != 0) {
        ESP_LOGW(TAG, "sha256 MISMATCH, dropping image");
        return ESP_FAIL;      /* never blit an unverified buffer */
    }
    return ESP_OK;
}
