/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "provisioning.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "nvs.h"
#include "sdkconfig.h"
#include "wifi_provisioning/manager.h"
#include "wifi_provisioning/scheme_ble.h"

#include "api_client.h"
#include "api_base.h"
#include "identity.h"
#include "nvs_schema.h"
#include "pairing_contract.h"
#include "provisioning_contract.h"
#include "qr_display.h"
#include "wifi.h"

#define WIFI_READY_BIT BIT0
#define WIFI_FAILED_BIT BIT1
#define TRANSACTION_DONE_BIT BIT2
#define CLOUD_PHASE_DONE_BIT BIT3
#define WIFI_CREDENTIALS_BIT BIT4
#define WIFI_LOCAL_FAILURE_BIT BIT5
#define WIFI_TRANSIENT_BITS (WIFI_READY_BIT | WIFI_FAILED_BIT | \
                             WIFI_CREDENTIALS_BIT)
#define WIFI_ATTEMPT_BITS (WIFI_TRANSIENT_BITS | WIFI_LOCAL_FAILURE_BIT)
#define FP_API_BASE_PAYLOAD_MAX 2048u
#define FP_INTERNET_RETRY_INTERVAL_MS 2000u
#define FP_CLOUD_RETRY_INTERVAL_MS 5000u
#define FP_RECOVERY_STATUS_GRACE_MS 30000u

static const char *TAG = "fp_provision";
static EventGroupHandle_t s_events;
static bool s_wifi_credentials_received;
static bool s_pair_bundle_read;
static bool s_pairing_expired;
static bool s_pairing_not_needed;
static bool s_byos_secret_required;
static bool s_target_required;

static TickType_t provisioning_ticks_remaining(int64_t deadline_us)
{
    int64_t remaining_us = deadline_us - esp_timer_get_time();
    if (remaining_us <= 0) {
        return 0;
    }
    uint64_t remaining_ms =
        ((uint64_t)remaining_us + 999u) / 1000u;
    TickType_t ticks = pdMS_TO_TICKS(remaining_ms);
    return ticks > 0 ? ticks : 1;
}

static bool nvs_has_string(const char *key)
{
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs) != ESP_OK) {
        return false;
    }
    size_t len = 0;
    bool found = nvs_get_str(nvs, key, NULL, &len) == ESP_OK && len > 1;
    nvs_close(nvs);
    return found;
}

static uint8_t nvs_get_flag(const char *key)
{
    uint8_t value = 0;
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs) == ESP_OK) {
        nvs_get_u8(nvs, key, &value);
        nvs_close(nvs);
    }
    return value;
}

static esp_err_t nvs_set_flag(const char *key, uint8_t value)
{
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_set_u8(nvs, key, value);
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    nvs_close(nvs);
    return err;
}

static esp_err_t complete_expired_pairing(void)
{
    esp_err_t err = fp_pairing_complete();
    if (err == ESP_OK && fp_api_setup_required() &&
        nvs_has_string(FP_NVS_DEVICE_TOKEN)) {
        err = fp_api_setup_mark_complete();
    }
    if (err == ESP_OK) {
        s_pairing_expired = true;
    }
    return err;
}

static void service_name(char *out, size_t cap)
{
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(out, cap, "PROV_%02X%02X%02X", mac[3], mac[4], mac[5]);
}

static esp_err_t response_json(const char *value, uint8_t **outbuf,
                               ssize_t *outlen)
{
    char *copy = strdup(value);
    if (!copy) {
        return ESP_ERR_NO_MEM;
    }
    *outbuf = (uint8_t *)copy;
    *outlen = (ssize_t)strlen(copy);
    return ESP_OK;
}

static esp_err_t api_base_handler(uint32_t session_id,
                                  const uint8_t *inbuf, ssize_t inlen,
                                  uint8_t **outbuf, ssize_t *outlen,
                                  void *priv)
{
    (void)session_id;
    (void)priv;
    if (!inbuf || inlen < 0 ||
        (size_t)inlen > FP_API_BASE_PAYLOAD_MAX ||
        s_wifi_credentials_received) {
        return response_json("{\"status\":\"locked\"}", outbuf, outlen);
    }
    if (memchr(inbuf, '\0', (size_t)inlen) != NULL) {
        return response_json("{\"status\":\"invalid\"}", outbuf, outlen);
    }
    char *requested = malloc((size_t)inlen + 1);
    if (!requested) {
        return ESP_ERR_NO_MEM;
    }
    memcpy(requested, inbuf, (size_t)inlen);
    requested[inlen] = '\0';
    char *cursor = requested;
    while (*cursor == ' ' || *cursor == '\t' ||
           *cursor == '\r' || *cursor == '\n') {
        ++cursor;
    }
    const char *url = cursor;
    const char *secret = NULL;
    cJSON *structured = NULL;
    if (*cursor == '{') {
        if (fp_json_contains_nul_escape(
                (const uint8_t *)cursor, strlen(cursor))) {
            memset(requested, 0, (size_t)inlen + 1);
            free(requested);
            return response_json("{\"status\":\"invalid\"}",
                                 outbuf, outlen);
        }
        const char *parse_end = NULL;
        structured = cJSON_ParseWithLengthOpts(
            cursor, strlen(cursor) + 1, &parse_end, true);
        const cJSON *url_item = structured
            ? cJSON_GetObjectItemCaseSensitive(structured, "url") : NULL;
        const cJSON *secret_item = structured
            ? cJSON_GetObjectItemCaseSensitive(
                  structured, "setup_secret") : NULL;
        size_t fields = 0;
        bool exact_fields = cJSON_IsObject(structured);
        for (const cJSON *item = structured ? structured->child : NULL;
             item; item = item->next) {
            ++fields;
            if (!item->string ||
                (strcmp(item->string, "url") != 0 &&
                 strcmp(item->string, "setup_secret") != 0)) {
                exact_fields = false;
            }
        }
        if (!exact_fields || fields != 2 ||
            !cJSON_IsString(url_item) || !cJSON_IsString(secret_item) ||
            !parse_end || *parse_end != '\0' ||
            !fp_utf8_valid_no_nul(
                (const uint8_t *)url_item->valuestring,
                strlen(url_item->valuestring)) ||
            !fp_utf8_valid_no_nul(
                (const uint8_t *)secret_item->valuestring,
                strlen(secret_item->valuestring))) {
            if (cJSON_IsString(secret_item)) {
                memset(secret_item->valuestring, 0,
                       strlen(secret_item->valuestring));
            }
            cJSON_Delete(structured);
            memset(requested, 0, (size_t)inlen + 1);
            free(requested);
            return response_json("{\"status\":\"invalid\"}",
                                 outbuf, outlen);
        }
        url = url_item->valuestring;
        secret = secret_item->valuestring;
    }
    esp_err_t err = fp_api_provisioning_target_set(url, secret);
    if (structured && secret) {
        memset((char *)secret, 0, strlen(secret));
    }
    cJSON_Delete(structured);
    memset(requested, 0, (size_t)inlen + 1);
    free(requested);
    if (err != ESP_OK) {
        return response_json("{\"status\":\"invalid\"}", outbuf, outlen);
    }
    if (fp_api_setup_required()) {
        s_pairing_expired = false;
        xEventGroupClearBits(s_events, CLOUD_PHASE_DONE_BIT);
    }
    s_target_required = false;
    return response_json("{\"status\":\"ok\"}", outbuf, outlen);
}

static esp_err_t pair_handler(uint32_t session_id,
                              const uint8_t *inbuf, ssize_t inlen,
                              uint8_t **outbuf, ssize_t *outlen,
                              void *priv)
{
    (void)session_id;
    (void)priv;
    if (!inbuf || inlen <= 0 || inlen > 96) {
        return response_json("{\"status\":\"invalid\"}", outbuf, outlen);
    }
    cJSON *request = cJSON_ParseWithLength((const char *)inbuf,
                                          (size_t)inlen);
    const cJSON *op = request
        ? cJSON_GetObjectItemCaseSensitive(request, "op") : NULL;
    if (!cJSON_IsString(op)) {
        cJSON_Delete(request);
        return response_json("{\"status\":\"invalid\"}", outbuf, outlen);
    }

    if (strcmp(op->valuestring, "get") == 0) {
        if (!(xEventGroupGetBits(s_events) & CLOUD_PHASE_DONE_BIT)) {
            xEventGroupWaitBits(
                s_events, CLOUD_PHASE_DONE_BIT, pdFALSE, pdTRUE,
                pdMS_TO_TICKS(30000));
        }
        if (!s_target_required &&
            fp_api_target_phase() == FP_TARGET_PHASE_READY &&
            fp_prov_state_get() == FP_PROV_STATE_PAIR_READY) {
            fp_pair_expiry_t expiry =
                fp_pairing_expiry_status(NULL);
            if (expiry == FP_PAIR_EXPIRY_EXPIRED &&
                fp_pairing_complete() == ESP_OK) {
                s_pairing_expired = true;
            }
            fp_pair_bundle_t bundle;
            if (expiry == FP_PAIR_EXPIRY_LIVE &&
                fp_pairing_load_bundle(&bundle) == ESP_OK) {
                char qr[FP_PAIR_QR_JSON_MAX];
                bool ok = fp_pair_qr_json(
                    bundle.device_ref, bundle.nonce,
                    bundle.expires_at, qr, sizeof(qr));
                memset(&bundle, 0, sizeof(bundle));
                cJSON_Delete(request);
                if (!ok) {
                    return response_json("{\"status\":\"invalid\"}",
                                         outbuf, outlen);
                }
                s_pair_bundle_read = true;
                esp_err_t err = response_json(qr, outbuf, outlen);
                memset(qr, 0, sizeof(qr));
                return err;
            }
        }
        cJSON_Delete(request);
        if (!(xEventGroupGetBits(s_events) & CLOUD_PHASE_DONE_BIT)) {
            /* A first-party setup failure is retryable. It must never be
             * mislabeled as optional-account-unavailable. */
            return response_json("{\"status\":\"retry\"}", outbuf, outlen);
        }
        char effective[FP_API_BASE_MAX];
        fp_api_base_get(effective, sizeof(effective));
        const char *status = NULL;
        if (s_target_required) {
            status = "{\"status\":\"target-required\"}";
        } else if (s_byos_secret_required) {
            status = "{\"status\":\"byos-secret-required\"}";
        } else if (strcmp(effective, CONFIG_FP_API_BASE) != 0) {
            status = "{\"status\":\"byos\"}";
        } else if (s_pairing_expired) {
            status = "{\"status\":\"expired\"}";
        } else if (s_pairing_not_needed) {
            status = "{\"status\":\"complete\"}";
        }
        if (!status) {
            return response_json("{\"status\":\"retry\"}", outbuf, outlen);
        }
        esp_err_t err = response_json(status, outbuf, outlen);
        if (err == ESP_OK) {
            xEventGroupSetBits(s_events, TRANSACTION_DONE_BIT);
            wifi_prov_mgr_stop_provisioning();
        }
        return err;
    }

    if (strcmp(op->valuestring, "ack") == 0 && s_pair_bundle_read &&
        fp_api_target_phase() == FP_TARGET_PHASE_READY) {
        cJSON_Delete(request);
        esp_err_t err = fp_pairing_complete();
        if (err != ESP_OK) {
            return response_json("{\"status\":\"invalid\"}", outbuf, outlen);
        }
        err = response_json("{\"status\":\"ok\"}", outbuf, outlen);
        if (err == ESP_OK) {
            xEventGroupSetBits(s_events, TRANSACTION_DONE_BIT);
            /* Auto-stop is disabled; its cleanup delay lets this ack leave
             * the GATT characteristic before BLE is torn down. */
            wifi_prov_mgr_stop_provisioning();
        }
        return err;
    }
    cJSON_Delete(request);
    return response_json("{\"status\":\"invalid\"}", outbuf, outlen);
}

static void event_handler(void *arg, esp_event_base_t base,
                          int32_t id, void *data)
{
    (void)arg;
    if (base == WIFI_PROV_EVENT) {
        if (id == WIFI_PROV_CRED_RECV) {
            /* Deliberately do not log the SSID or password. */
            xEventGroupClearBits(s_events, WIFI_TRANSIENT_BITS);
            if (!data ||
                fp_wifi_store_credentials(
                    (const wifi_sta_config_t *)data) != ESP_OK) {
                s_wifi_credentials_received = false;
                ESP_LOGE(TAG, "credential mirror commit failed");
                xEventGroupSetBits(s_events, WIFI_LOCAL_FAILURE_BIT);
            } else {
                s_wifi_credentials_received = true;
                xEventGroupSetBits(s_events, WIFI_CREDENTIALS_BIT);
            }
        } else if (id == WIFI_PROV_CRED_FAIL) {
            xEventGroupClearBits(s_events, WIFI_TRANSIENT_BITS);
            esp_err_t reset_err =
                wifi_prov_mgr_reset_sm_state_on_failure();
            if (reset_err == ESP_OK) {
                xEventGroupSetBits(s_events, WIFI_FAILED_BIT);
            } else {
                ESP_LOGE(TAG,
                         "provisioning manager retry reset failed: %s",
                         esp_err_to_name(reset_err));
                xEventGroupSetBits(s_events, WIFI_LOCAL_FAILURE_BIT);
            }
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xEventGroupClearBits(
            s_events, WIFI_FAILED_BIT | WIFI_CREDENTIALS_BIT);
        xEventGroupSetBits(s_events, WIFI_READY_BIT);
    }
}

static esp_err_t qr_for_security2(const fp_sec2_credentials_t *creds,
                                  const char *name)
{
    char payload[FP_PROV_QR_JSON_MAX];
    if (!fp_provision_qr_json(name, creds->username, creds->password,
                              payload, sizeof(payload))) {
        return ESP_FAIL;
    }
    esp_err_t err = fp_qr_display(payload);
    memset(payload, 0, sizeof(payload));
    return err;
}

typedef enum {
    FP_CLOUD_ATTEMPT_COMPLETE = 0,
    FP_CLOUD_ATTEMPT_RETRY,
    FP_CLOUD_ATTEMPT_ABORT,
} fp_cloud_attempt_status_t;

typedef struct {
    fp_cloud_attempt_status_t status;
    esp_err_t error;
} fp_cloud_attempt_t;

static fp_cloud_attempt_t cloud_complete(void)
{
    return (fp_cloud_attempt_t){
        .status = FP_CLOUD_ATTEMPT_COMPLETE,
        .error = ESP_OK,
    };
}

static fp_cloud_attempt_t cloud_retry(esp_err_t error)
{
    return (fp_cloud_attempt_t){
        .status = FP_CLOUD_ATTEMPT_RETRY,
        .error = error,
    };
}

static fp_cloud_attempt_t cloud_abort(esp_err_t error)
{
    return (fp_cloud_attempt_t){
        .status = FP_CLOUD_ATTEMPT_ABORT,
        .error = error,
    };
}

/*
 * Run one idempotent cloud-phase attempt. The `/setup` operation is retryable
 * as a unit because its own durable barriers and SETUP_PENDING recovery make
 * response loss safe. Lifecycle parsing, crypto preparation, and subsequent
 * local acknowledgement/barrier failures remain fail-closed.
 */
static fp_cloud_attempt_t cloud_phase_attempt(void)
{
    fp_target_phase_t target_phase = fp_api_target_phase();
    if (target_phase == FP_TARGET_PHASE_NEEDS_RESUBMIT) {
        /* No target or bearer may be used until the phone resubmits. */
        s_target_required = true;
        return cloud_complete();
    }

    char effective[FP_API_BASE_MAX];
    fp_api_base_get(effective, sizeof(effective));
    bool first_party = strcmp(effective, CONFIG_FP_API_BASE) == 0;
    if (!first_party && fp_api_departure_cleanup_pending()) {
        esp_err_t cleanup_err = fp_api_departure_cleanup_attempt();
        if (cleanup_err != ESP_OK) {
            ESP_LOGW(TAG,
                     "official-cloud unlink deferred; BYOS continues");
        } else {
            ESP_LOGI(TAG, "official-cloud unlink completed");
        }
    }

    fp_prov_state_t state = fp_prov_state_get();
    bool token_present = nvs_has_string(FP_NVS_DEVICE_TOKEN);
    bool cloud_recovered = false;
    esp_err_t err = ESP_OK;

    if (state == FP_PROV_STATE_PAIR_READY) {
        fp_pair_expiry_t expiry = fp_pairing_expiry_status(NULL);
        if (expiry == FP_PAIR_EXPIRY_UNKNOWN) {
            return cloud_abort(ESP_ERR_INVALID_STATE);
        }
        if (expiry == FP_PAIR_EXPIRY_EXPIRED) {
            err = complete_expired_pairing();
            if (err != ESP_OK) {
                return cloud_abort(err);
            }
            cloud_recovered = true;
        } else if (token_present) {
            /* The setup response and local pairing ack are already durable. */
            if (target_phase == FP_TARGET_PHASE_SETUP_REQUIRED) {
                err = fp_api_setup_mark_complete();
            }
            if (err != ESP_OK) {
                return cloud_abort(err);
            }
            cloud_recovered = true;
        }
    } else if (state == FP_PROV_STATE_COMPLETE &&
               token_present &&
               target_phase == FP_TARGET_PHASE_SETUP_REQUIRED) {
        /* Finish only the last target-journal barrier after a cut. */
        err = fp_api_setup_mark_complete();
        if (err != ESP_OK) {
            return cloud_abort(err);
        }
        cloud_recovered = true;
    }

    bool setup_needed = !token_present ||
                        fp_api_setup_required() ||
                        state == FP_PROV_STATE_SETUP_PENDING;
    if (!cloud_recovered && !setup_needed) {
        err = state == FP_PROV_STATE_COMPLETE
            ? ESP_OK : fp_pairing_complete();
        if (err != ESP_OK) {
            return cloud_abort(err);
        }
        s_pairing_not_needed = true;
        cloud_recovered = true;
    }
    if (cloud_recovered) {
        return cloud_complete();
    }

    char credential[FP_FACTORY_CREDENTIAL_MAX];
    esp_err_t byos_err = fp_api_byos_setup_get(
        credential, sizeof(credential));
    fp_setup_credential_source_t source =
        fp_setup_credential_source(first_party, byos_err == ESP_OK);
    if (source == FP_SETUP_CREDENTIAL_FACTORY) {
        memset(credential, 0, sizeof(credential));
        err = fp_factory_credential_get(
            credential, sizeof(credential));
    } else if (source == FP_SETUP_CREDENTIAL_BYOS) {
        err = ESP_OK;
    } else {
        memset(credential, 0, sizeof(credential));
        s_byos_secret_required = true;
        return cloud_complete();
    }
    if (err != ESP_OK) {
        memset(credential, 0, sizeof(credential));
        return cloud_abort(err);
    }

    fp_pair_registration_t registration;
    memset(&registration, 0, sizeof(registration));
    if (first_party) {
        err = fp_pairing_prepare_first(&registration);
    }
    if (err != ESP_OK) {
        memset(credential, 0, sizeof(credential));
        memset(&registration, 0, sizeof(registration));
        return cloud_abort(err);
    }

    fp_setup_result_t setup;
    memset(&setup, 0, sizeof(setup));
    err = fp_api_setup(
        credential, first_party ? &registration : NULL, &setup);
    memset(credential, 0, sizeof(credential));
    memset(&registration, 0, sizeof(registration));
    if (err != ESP_OK) {
        memset(&setup, 0, sizeof(setup));
        return cloud_retry(err);
    }

    if (first_party) {
        err = fp_pairing_accept_first_ack(
            setup.device_ref, setup.pairing_counter,
            setup.pairing_expires_at);
    } else {
        err = fp_pairing_complete();
    }
    memset(&setup, 0, sizeof(setup));
    if (err == ESP_OK) {
        err = fp_api_setup_mark_complete();
    }
    return err == ESP_OK ? cloud_complete() : cloud_abort(err);
}

esp_err_t fp_provisioning_factory_prep(void)
{
    char setup_credential[FP_FACTORY_CREDENTIAL_MAX];
    esp_err_t err = fp_factory_credential_get(
        setup_credential, sizeof(setup_credential));
    memset(setup_credential, 0, sizeof(setup_credential));
    if (err != ESP_OK) {
        /* A shippable QR is never produced for an unenrolled unit. */
        return err;
    }
    fp_sec2_credentials_t creds;
    err = fp_sec2_load_or_create(&creds);
    if (err != ESP_OK) {
        return err;
    }
    char name[16];
    service_name(name, sizeof(name));
    err = qr_for_security2(&creds, name);
    fp_sec2_credentials_free(&creds);
    if (err == ESP_OK) {
        nvs_handle_t nvs = 0;
        err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
        if (err == ESP_OK) {
            err = nvs_set_u8(nvs, FP_NVS_PROV_QR_DRAWN, 1);
        }
        if (err == ESP_OK) {
            err = nvs_set_u8(nvs, FP_NVS_SHIPPING,
                             FP_SHIPPING_FIELD_FLASH_PENDING);
        }
        if (err == ESP_OK) {
            err = nvs_commit(nvs);
        }
        if (nvs != 0) {
            nvs_close(nvs);
        }
    }
    return err;
}

esp_err_t fp_provisioning_run(bool reprovision_wifi)
{
    (void)reprovision_wifi;
    esp_err_t err = fp_wifi_platform_init();
    if (err != ESP_OK) {
        return err;
    }

    fp_sec2_credentials_t creds;
    err = fp_sec2_load_or_create(&creds);
    if (err != ESP_OK) {
        return err;
    }

    s_events = xEventGroupCreate();
    if (!s_events) {
        fp_sec2_credentials_free(&creds);
        return ESP_ERR_NO_MEM;
    }
    s_wifi_credentials_received = false;
    s_pair_bundle_read = false;
    s_pairing_expired = false;
    s_pairing_not_needed = false;
    s_byos_secret_required = false;
    s_target_required =
        fp_api_target_phase() == FP_TARGET_PHASE_NEEDS_RESUBMIT;
    fp_prov_state_t initial_state = fp_prov_state_get();
    if (initial_state == FP_PROV_STATE_EMPTY) {
        err = fp_prov_state_set(FP_PROV_STATE_QR_READY);
        if (err != ESP_OK) {
            fp_sec2_credentials_free(&creds);
            vEventGroupDelete(s_events);
            return err;
        }
        initial_state = FP_PROV_STATE_QR_READY;
    }
    fp_pair_expiry_t initial_expiry = FP_PAIR_EXPIRY_UNKNOWN;
    bool terminal_recovery = false;
    if (!s_target_required &&
        initial_state == FP_PROV_STATE_PAIR_READY) {
        initial_expiry = fp_pairing_expiry_status(NULL);
        if (initial_expiry == FP_PAIR_EXPIRY_EXPIRED) {
            err = complete_expired_pairing();
            if (err != ESP_OK) {
                fp_sec2_credentials_free(&creds);
                vEventGroupDelete(s_events);
                s_events = NULL;
                return err;
            }
            initial_state = FP_PROV_STATE_COMPLETE;
            terminal_recovery = true;
        } else if (
            nvs_has_string(FP_NVS_DEVICE_TOKEN) &&
            fp_api_target_phase() ==
                FP_TARGET_PHASE_SETUP_REQUIRED) {
            /* The setup response and PAIR_READY write are durable. A cut
             * before the target journal's last write needs no cloud retry. */
            err = fp_api_setup_mark_complete();
            if (err != ESP_OK) {
                fp_sec2_credentials_free(&creds);
                vEventGroupDelete(s_events);
                s_events = NULL;
                return err;
            }
        }
    } else if (!s_target_required &&
               initial_state == FP_PROV_STATE_COMPLETE &&
               nvs_has_string(FP_NVS_DEVICE_TOKEN) &&
               fp_api_target_phase() ==
                   FP_TARGET_PHASE_SETUP_REQUIRED) {
        /* A cut after local setup/pairing acknowledgement but before the
         * target journal's final phase write is a local-only recovery. */
        err = fp_api_setup_mark_complete();
        if (err != ESP_OK) {
            fp_sec2_credentials_free(&creds);
            vEventGroupDelete(s_events);
            s_events = NULL;
            return err;
        }
        terminal_recovery = true;
    }

    if (terminal_recovery) {
        fp_sec2_credentials_free(&creds);
        vEventGroupDelete(s_events);
        s_events = NULL;
        ESP_LOGI(TAG,
                 "terminal provisioning recovery completed without BLE wait");
        return ESP_OK;
    }
    bool recover_pair_clock = fp_wifi_recovery_should_restore(
        s_target_required,
        initial_state == FP_PROV_STATE_PAIR_READY,
        initial_expiry == FP_PAIR_EXPIRY_UNKNOWN);

    esp_event_handler_instance_t prov_instance = NULL;
    esp_event_handler_instance_t ip_instance = NULL;
    bool prov_registered = false;
    bool ip_registered = false;
    err = esp_event_handler_instance_register(
        WIFI_PROV_EVENT, ESP_EVENT_ANY_ID, event_handler, NULL,
        &prov_instance);
    if (err == ESP_OK) {
        prov_registered = true;
    }
    if (err == ESP_OK) {
        err = esp_event_handler_instance_register(
            IP_EVENT, IP_EVENT_STA_GOT_IP, event_handler, NULL,
            &ip_instance);
        if (err == ESP_OK) {
            ip_registered = true;
        }
    }
    if (err != ESP_OK) {
        if (prov_registered) {
            esp_event_handler_instance_unregister(
                WIFI_PROV_EVENT, ESP_EVENT_ANY_ID, prov_instance);
        }
        fp_sec2_credentials_free(&creds);
        vEventGroupDelete(s_events);
        return err;
    }

    wifi_prov_mgr_config_t config = {
        .scheme = wifi_prov_scheme_ble,
        .scheme_event_handler = WIFI_PROV_SCHEME_BLE_EVENT_HANDLER_FREE_BTDM,
    };
    err = wifi_prov_mgr_init(config);
    if (err != ESP_OK) {
        goto cleanup_events;
    }

    if (s_pairing_expired ||
        (initial_state == FP_PROV_STATE_PAIR_READY &&
         initial_expiry == FP_PAIR_EXPIRY_LIVE &&
         fp_api_target_phase() == FP_TARGET_PHASE_READY)) {
        xEventGroupSetBits(s_events, CLOUD_PHASE_DONE_BIT);
    }
    uint8_t uuid[] = {
        0x5c, 0x33, 0xaf, 0xf1, 0x92, 0xc8, 0x47, 0xa8,
        0xb4, 0x64, 0x9d, 0x20, 0x72, 0x54, 0x46, 0x50,
    };
    wifi_prov_scheme_ble_set_service_uuid(uuid);
    err = wifi_prov_mgr_endpoint_create("fp-api-base");
    if (err == ESP_OK) {
        err = wifi_prov_mgr_endpoint_create("fp-pair");
    }
    if (err == ESP_OK) {
        err = wifi_prov_mgr_disable_auto_stop(1000);
    }

    char name[16];
    service_name(name, sizeof(name));
    wifi_prov_security2_params_t sec2 = {
        .salt = creds.salt,
        .salt_len = creds.salt_len,
        .verifier = creds.verifier,
        .verifier_len = creds.verifier_len,
    };
    if (err == ESP_OK) {
        err = wifi_prov_mgr_start_provisioning(
            WIFI_PROV_SECURITY_2, &sec2, name, NULL);
    }
    if (err == ESP_OK) {
        err = wifi_prov_mgr_endpoint_register(
            "fp-api-base", api_base_handler, NULL);
    }
    if (err == ESP_OK) {
        err = wifi_prov_mgr_endpoint_register(
            "fp-pair", pair_handler, NULL);
    }
    if (err == ESP_OK && !nvs_get_flag(FP_NVS_PROV_QR_DRAWN)) {
        err = qr_for_security2(&creds, name);
        if (err == ESP_OK) {
            err = nvs_set_flag(FP_NVS_PROV_QR_DRAWN, 1);
        }
    }
    if (err == ESP_OK && recover_pair_clock) {
        wifi_config_t stored = {0};
        esp_err_t load_err = fp_wifi_load_credentials(&stored.sta);
        if (load_err == ESP_OK) {
            /*
             * start_provisioning() deliberately clears the manager's RAM
             * STA config. Feed the explicit app-NVS mirror back through the
             * manager so it emits the normal credential/IP events while the
             * Security-2 session remains available for a phone fallback.
             */
            err = wifi_prov_mgr_configure_sta(&stored);
            if (err == ESP_OK) {
                ESP_LOGI(TAG,
                         "restoring saved Wi-Fi for pairing clock recovery");
            }
        } else {
            ESP_LOGW(
                TAG,
                "saved Wi-Fi unavailable for pairing recovery: %s; "
                "waiting for phone credentials",
                esp_err_to_name(load_err));
        }
        memset(&stored, 0, sizeof(stored));
    }
    if (err != ESP_OK) {
        wifi_prov_mgr_stop_provisioning();
        wifi_prov_mgr_deinit();
        goto cleanup_events;
    }

    int64_t provisioning_deadline_us =
        esp_timer_get_time() +
        (int64_t)CONFIG_FP_PROVISION_TIMEOUT_S * 1000000LL;
    if (!(xEventGroupGetBits(s_events) & CLOUD_PHASE_DONE_BIT)) {
        bool wifi_associated = false;
        bool internet_ready = false;
        bool cloud_done = false;
        while (err == ESP_OK && !cloud_done) {
            while (err == ESP_OK && !internet_ready) {
                if (wifi_associated) {
                    if (fp_wifi_sync_time() == ESP_OK) {
                        internet_ready = true;
                        break;
                    }
                    ESP_LOGW(
                        TAG,
                        "internet unavailable; Security-2 session remains open");
                }

                TickType_t remaining =
                    provisioning_ticks_remaining(provisioning_deadline_us);
                if (remaining == 0) {
                    err = ESP_ERR_TIMEOUT;
                    break;
                }
                TickType_t wait_ticks = remaining;
                TickType_t retry_ticks =
                    pdMS_TO_TICKS(FP_INTERNET_RETRY_INTERVAL_MS);
                if (wifi_associated && retry_ticks > 0 &&
                    wait_ticks > retry_ticks) {
                    wait_ticks = retry_ticks;
                }
                EventBits_t bits = xEventGroupWaitBits(
                    s_events, WIFI_ATTEMPT_BITS,
                    pdTRUE, pdFALSE, wait_ticks);
                fp_wifi_attempt_action_t action =
                    fp_wifi_attempt_action(
                        (bits & WIFI_CREDENTIALS_BIT) != 0,
                        (bits & WIFI_FAILED_BIT) != 0,
                        (bits & WIFI_READY_BIT) != 0,
                        (bits & WIFI_LOCAL_FAILURE_BIT) != 0);
                if (action == FP_WIFI_ATTEMPT_ABORT) {
                    err = ESP_FAIL;
                } else if (action == FP_WIFI_ATTEMPT_RETRY) {
                    wifi_associated = false;
                    ESP_LOGW(
                        TAG,
                        "Wi-Fi credentials rejected; provisioning remains open");
                } else if (
                    action == FP_WIFI_ATTEMPT_CREDENTIALS_RECEIVED) {
                    wifi_associated = false;
                } else if (action == FP_WIFI_ATTEMPT_READY) {
                    wifi_associated = true;
                } else if (!wifi_associated) {
                    err = ESP_ERR_TIMEOUT;
                }
            }
            if (err != ESP_OK) {
                break;
            }

            fp_cloud_attempt_t attempt = cloud_phase_attempt();
            if (attempt.status == FP_CLOUD_ATTEMPT_COMPLETE) {
                xEventGroupSetBits(s_events, CLOUD_PHASE_DONE_BIT);
                cloud_done = true;
                break;
            }
            if (attempt.status == FP_CLOUD_ATTEMPT_ABORT) {
                err = attempt.error;
                break;
            }

            ESP_LOGW(
                TAG,
                "cloud setup retryable; Security-2 session remains open: %s",
                esp_err_to_name(attempt.error));
            TickType_t remaining =
                provisioning_ticks_remaining(provisioning_deadline_us);
            if (remaining == 0) {
                err = ESP_ERR_TIMEOUT;
                break;
            }
            TickType_t wait_ticks =
                pdMS_TO_TICKS(FP_CLOUD_RETRY_INTERVAL_MS);
            if (wait_ticks == 0 || wait_ticks > remaining) {
                wait_ticks = remaining;
            }
            EventBits_t bits = xEventGroupWaitBits(
                s_events, WIFI_ATTEMPT_BITS,
                pdTRUE, pdFALSE, wait_ticks);
            fp_wifi_attempt_action_t action = fp_wifi_attempt_action(
                (bits & WIFI_CREDENTIALS_BIT) != 0,
                (bits & WIFI_FAILED_BIT) != 0,
                (bits & WIFI_READY_BIT) != 0,
                (bits & WIFI_LOCAL_FAILURE_BIT) != 0);
            if (action == FP_WIFI_ATTEMPT_ABORT) {
                err = ESP_FAIL;
            } else if (action == FP_WIFI_ATTEMPT_RETRY ||
                       action ==
                           FP_WIFI_ATTEMPT_CREDENTIALS_RECEIVED) {
                wifi_associated = false;
                internet_ready = false;
            } else if (action == FP_WIFI_ATTEMPT_READY) {
                wifi_associated = true;
                internet_ready = false;
            }
        }
        if (err != ESP_OK) {
            wifi_prov_mgr_stop_provisioning();
            wifi_prov_mgr_deinit();
            goto cleanup_events;
        }
    }

    if (err == ESP_OK) {
        TickType_t transaction_wait =
            provisioning_ticks_remaining(provisioning_deadline_us);
        bool wait_ends_at_pair_expiry = false;
        if (transaction_wait == 0) {
            err = ESP_ERR_TIMEOUT;
        }

        uint32_t pair_remaining_s = 0;
        bool pair_ready =
            fp_prov_state_get() == FP_PROV_STATE_PAIR_READY;
        fp_pair_expiry_t pair_expiry =
            pair_ready
                ? fp_pairing_expiry_status(&pair_remaining_s)
                : FP_PAIR_EXPIRY_UNKNOWN;
        if (err == ESP_OK &&
            fp_pair_wait_uses_expiry(
                pair_ready,
                pair_expiry == FP_PAIR_EXPIRY_LIVE,
                pair_remaining_s,
                (uint32_t)CONFIG_FP_PROVISION_TIMEOUT_S)) {
            TickType_t pair_wait =
                pdMS_TO_TICKS(pair_remaining_s * 1000u);
            if (pair_wait > 0 && pair_wait <= transaction_wait) {
                transaction_wait = pair_wait;
                wait_ends_at_pair_expiry = true;
            }
        }

        /*
         * If saved Wi-Fi established that the proof had already expired,
         * allow a concurrently waiting phone a short window to receive the
         * explicit status. With no phone, recovery still finishes in 30 s,
         * rather than burning the full provisioning timeout on a terminal
         * state.
         */
        bool terminal_clock_recovery =
            recover_pair_clock && s_pairing_expired;
        if (err == ESP_OK && terminal_clock_recovery) {
            TickType_t grace =
                pdMS_TO_TICKS(FP_RECOVERY_STATUS_GRACE_MS);
            if (grace > 0 && grace < transaction_wait) {
                transaction_wait = grace;
            }
        }

        if (err == ESP_OK) {
            EventBits_t bits = xEventGroupWaitBits(
                s_events, TRANSACTION_DONE_BIT, pdFALSE, pdTRUE,
                transaction_wait);
            if (!(bits & TRANSACTION_DONE_BIT)) {
                bool recovered_without_phone = terminal_clock_recovery;
                if (wait_ends_at_pair_expiry &&
                    fp_pairing_expiry_status(NULL) ==
                        FP_PAIR_EXPIRY_EXPIRED) {
                    err = complete_expired_pairing();
                    recovered_without_phone = err == ESP_OK;
                }
                if (recovered_without_phone) {
                    ESP_LOGI(
                        TAG,
                        "pairing expiry recovery completed without phone");
                } else if (err == ESP_OK) {
                    err = ESP_ERR_TIMEOUT;
                }
                wifi_prov_mgr_stop_provisioning();
            }
        }
    }
    wifi_prov_mgr_deinit();

cleanup_events:
    if (prov_registered) {
        esp_event_handler_instance_unregister(
            WIFI_PROV_EVENT, ESP_EVENT_ANY_ID, prov_instance);
    }
    if (ip_registered) {
        esp_event_handler_instance_unregister(
            IP_EVENT, IP_EVENT_STA_GOT_IP, ip_instance);
    }
    fp_sec2_credentials_free(&creds);
    vEventGroupDelete(s_events);
    s_events = NULL;
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "provisioning transaction complete");
    } else {
        ESP_LOGW(TAG, "provisioning transaction stopped: %s",
                 esp_err_to_name(err));
    }
    return err;
}
