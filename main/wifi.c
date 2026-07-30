/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "wifi.h"

#include <string.h>
#include <time.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "nvs.h"

#include "nvs_schema.h"

static const char *TAG = "fp_wifi";
static EventGroupHandle_t s_events;
#define CONNECTED_BIT BIT0
#define FAILED_BIT    BIT1
static int s_retries;
static bool s_platform_ready;
static bool s_wifi_started;

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_retries++ < 3) {
            esp_wifi_connect();
        } else {
            xEventGroupSetBits(s_events, FAILED_BIT);
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(s_events, CONNECTED_BIT);
    }
}

static void remember_ap(const wifi_ap_record_t *ap)
{
    if (!ap) {
        return;
    }
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs) == ESP_OK) {
        nvs_set_blob(nvs, FP_NVS_WIFI_BSSID, ap->bssid, 6);
        nvs_set_u8(nvs, FP_NVS_WIFI_CHAN, ap->primary);
        nvs_commit(nvs);
        nvs_close(nvs);
    }
}

esp_err_t fp_wifi_platform_init(void)
{
    if (s_platform_ready) {
        return ESP_OK;
    }
    esp_err_t err = esp_netif_init();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }
    err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }
    if (!esp_netif_create_default_wifi_sta()) {
        return ESP_FAIL;
    }
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&init);
    if (err != ESP_OK) {
        return err;
    }
    s_events = xEventGroupCreate();
    if (!s_events) {
        return ESP_ERR_NO_MEM;
    }
    err = esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                     on_event, NULL);
    if (err == ESP_OK) {
        err = esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                         on_event, NULL);
    }
    if (err != ESP_OK) {
        return err;
    }
    s_platform_ready = true;
    return ESP_OK;
}

esp_err_t fp_wifi_store_credentials(const wifi_sta_config_t *cfg)
{
    if (!cfg || cfg->ssid[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    size_t ssid_len = strnlen(
        (const char *)cfg->ssid, sizeof(cfg->ssid));
    size_t password_len = strnlen(
        (const char *)cfg->password, sizeof(cfg->password));
    char ssid[sizeof(cfg->ssid) + 1];
    char password[sizeof(cfg->password) + 1];
    memcpy(ssid, cfg->ssid, ssid_len);
    ssid[ssid_len] = '\0';
    memcpy(password, cfg->password, password_len);
    password[password_len] = '\0';

    nvs_handle_t nvs;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        memset(password, 0, sizeof(password));
        memset(ssid, 0, sizeof(ssid));
        return err;
    }
    err = nvs_set_str(nvs, FP_NVS_WIFI_SSID, ssid);
    if (err == ESP_OK) {
        err = nvs_set_str(nvs, FP_NVS_WIFI_PASS, password);
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    nvs_close(nvs);
    memset(password, 0, sizeof(password));
    memset(ssid, 0, sizeof(ssid));
    return err;
}

esp_err_t fp_wifi_load_credentials(wifi_sta_config_t *out)
{
    if (!out) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out, 0, sizeof(*out));

    nvs_handle_t nvs;
    char stored_ssid[sizeof(out->ssid) + 1] = {0};
    char stored_password[sizeof(out->password) + 1] = {0};
    size_t ssid_len = sizeof(stored_ssid);
    size_t pass_len = sizeof(stored_password);
    esp_err_t err = nvs_open(
        FP_NVS_NAMESPACE, NVS_READONLY, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    esp_err_t got = nvs_get_str(
        nvs, FP_NVS_WIFI_SSID, stored_ssid, &ssid_len);
    esp_err_t got_password = nvs_get_str(
        nvs, FP_NVS_WIFI_PASS, stored_password, &pass_len);

    uint8_t chan = 0;
    size_t blen = sizeof(out->bssid);
    if (nvs_get_u8(nvs, FP_NVS_WIFI_CHAN, &chan) == ESP_OK && chan) {
        out->channel = chan;
    }
    if (nvs_get_blob(nvs, FP_NVS_WIFI_BSSID, out->bssid, &blen) == ESP_OK &&
        blen == sizeof(out->bssid)) {
        out->bssid_set = true;
    }
    nvs_close(nvs);

    if (got != ESP_OK || ssid_len < 2 ||
        (got_password != ESP_OK &&
         got_password != ESP_ERR_NVS_NOT_FOUND)) {
        memset(stored_password, 0, sizeof(stored_password));
        memset(stored_ssid, 0, sizeof(stored_ssid));
        memset(out, 0, sizeof(*out));
        ESP_LOGW(TAG, "no wifi credentials in NVS");
        return ESP_ERR_NOT_FOUND;
    }
    memcpy(out->ssid, stored_ssid, ssid_len - 1);
    if (got_password == ESP_OK && pass_len > 1) {
        memcpy(out->password, stored_password, pass_len - 1);
    }
    memset(stored_password, 0, sizeof(stored_password));
    memset(stored_ssid, 0, sizeof(stored_ssid));
    return ESP_OK;
}

esp_err_t fp_wifi_sync_time(void)
{
    time_t now = 0;
    time(&now);
    if (now >= 1600000000) {
        return ESP_OK;
    }
    esp_sntp_config_t sntp = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    esp_netif_sntp_init(&sntp);
    esp_err_t err = esp_netif_sntp_sync_wait(pdMS_TO_TICKS(10000));
    esp_netif_sntp_deinit();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "SNTP sync failed; TLS unavailable");
        return err;
    }
    ESP_LOGI(TAG, "clock set via SNTP");
    return ESP_OK;
}

esp_err_t fp_wifi_connect(int timeout_ms)
{
    wifi_config_t cfg = {0};
    esp_err_t err = fp_wifi_load_credentials(&cfg.sta);
    if (err != ESP_OK) {
        return err;
    }
    err = fp_wifi_platform_init();
    if (err != ESP_OK) {
        memset(&cfg, 0, sizeof(cfg));
        return err;
    }

    /* Unified Provisioning intentionally leaves the station associated for
     * the immediate first poll. Adopt that live connection: clearing event
     * bits and waiting for a second GOT_IP event would time out because DHCP
     * has already completed. */
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        s_wifi_started = true;
        memset(&cfg, 0, sizeof(cfg));
        remember_ap(&ap);
        return fp_wifi_sync_time();
    }

    s_retries = 0;
    xEventGroupClearBits(s_events, CONNECTED_BIT | FAILED_BIT);
    err = esp_wifi_set_mode(WIFI_MODE_STA);
    if (err == ESP_OK) {
        err = esp_wifi_set_config(WIFI_IF_STA, &cfg);
    }
    memset(&cfg, 0, sizeof(cfg));
    if (err == ESP_OK && !s_wifi_started) {
        err = esp_wifi_start();
        if (err == ESP_OK) {
            s_wifi_started = true;
        }
    }
    if (err == ESP_OK) {
        err = esp_wifi_connect();
    }
    if (err != ESP_OK) {
        return err;
    }

    EventBits_t bits = xEventGroupWaitBits(
        s_events, CONNECTED_BIT | FAILED_BIT, pdFALSE, pdFALSE,
        pdMS_TO_TICKS(timeout_ms));
    if (!(bits & CONNECTED_BIT)) {
        ESP_LOGW(TAG, "join failed/timeout");
        return ESP_FAIL;
    }

    /* Remember AP for ~2 s joins next wake */
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        remember_ap(&ap);
    }

    /* No RTC battery: after deep power loss the clock is 1970 and TLS
     * certificate validation rejects EVERYTHING. Sync before any HTTPS.
     * (RTC keeps time through deep sleep, so this is cheap on normal
     * wakes — the sane-clock check skips the wait entirely.) */
    if (fp_wifi_sync_time() != ESP_OK) {
        return ESP_FAIL;
    }
    return ESP_OK;
}

int fp_wifi_rssi(void)
{
    wifi_ap_record_t ap;
    return esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}

esp_err_t fp_wifi_factory_reset(void)
{
    esp_err_t err = fp_wifi_platform_init();
    if (err != ESP_OK) {
        return err;
    }

    /* esp_wifi_restore() requires an initialized, stopped driver and clears
     * the driver's own persistent "wifi" namespace. Unified Provisioning
     * writes there in addition to our explicit app-namespace mirror. */
    esp_wifi_stop();
    s_wifi_started = false;
    err = esp_wifi_restore();
    fp_wifi_stop();
    return err;
}

void fp_wifi_stop(void)
{
    if (s_platform_ready) {
        /* Unified Provisioning may have started Wi-Fi without updating this
         * module's local flag. Stop unconditionally before deinit; NOT_STARTED
         * is harmless and avoids ESP_ERR_WIFI_NOT_STOPPED on that path. */
        esp_wifi_stop();
        s_wifi_started = false;
        esp_event_handler_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                     on_event);
        esp_event_handler_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                     on_event);
        esp_wifi_deinit();
        if (s_events) {
            vEventGroupDelete(s_events);
            s_events = NULL;
        }
        s_platform_ready = false;
    }
}
