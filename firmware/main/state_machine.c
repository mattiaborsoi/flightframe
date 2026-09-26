/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Wake -> provision/pair/poll -> sleep. Ordinary transaction inputs live in
 * encrypted app NVS. The factory credential and minimal reset-cleanup journal
 * live in a separate namespace that app factory reset never erases. */
#include "state_machine.h"

#include <string.h>
#include <time.h>

#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_system.h"
#include "nvs.h"

#include "api_base.h"
#include "api_client.h"
#include "buttons.h"
#include "errlog.h"
#include "identity.h"
#include "nvs_schema.h"
#include "ota.h"
#include "panel.h"
#include "pairing_contract.h"
#include "provisioning.h"
#include "qr_display.h"
#include "sdkconfig.h"
#include "wifi.h"

static const char *TAG = "flightportrait";
static RTC_DATA_ATTR int64_t s_planned_wake_epoch;

static bool nvs_has_key(const char *key)
{
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs) != ESP_OK) {
        return false;
    }
    size_t len = 0;
    bool ok = nvs_get_str(nvs, key, NULL, &len) == ESP_OK;
    nvs_close(nvs);
    return ok;
}

static uint8_t nvs_flag(const char *key)
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

fp_wake_reason_t fp_classify_wake(void)
{
    if (!nvs_has_key(FP_NVS_DEVICE_TOKEN)) {
        return FP_WAKE_FIRST_BOOT;
    }
    switch (esp_sleep_get_wakeup_cause()) {
    case ESP_SLEEP_WAKEUP_TIMER:
        return FP_WAKE_TIMER;
    case ESP_SLEEP_WAKEUP_EXT0:
    case ESP_SLEEP_WAKEUP_EXT1:
    case ESP_SLEEP_WAKEUP_GPIO:
        return FP_WAKE_BUTTON;
    default:
        return FP_WAKE_USB_POWER;
    }
}

void fp_factory_reset_and_restart(void)
{
    ESP_LOGW(TAG, "factory reset: erasing Wi-Fi and app state");
    fp_reset_cleanup_state_t reset_cleanup =
        fp_factory_reset_cleanup_state();
    if (!fp_reset_cleanup_resume_erase(reset_cleanup)) {
        bool cloud_required =
            fp_reset_cleanup_cloud_required(reset_cleanup) ||
            fp_api_factory_reset_cleanup_needed();
        reset_cleanup = cloud_required
            ? FP_RESET_CLEANUP_ERASE_AND_CLOUD_PENDING
            : FP_RESET_CLEANUP_ERASE_PENDING;
        /* This protected-namespace commit is the reset barrier for every
         * target. The cloud bit independently preserves old first-party
         * provenance when one exists. */
        if (fp_factory_reset_cleanup_set(reset_cleanup) != ESP_OK) {
            ESP_LOGE(TAG, "reset barrier failed; preserving state");
            return;
        }
    }
    if (fp_wifi_factory_reset() != ESP_OK) {
        ESP_LOGE(TAG, "Wi-Fi reset failed; preserving app state");
        return;
    }
    if (fp_factory_reset_app_state() != ESP_OK) {
        ESP_LOGE(TAG, "app reset failed; retry required");
        return;
    }
    fp_reset_cleanup_state_t next_cleanup =
        fp_reset_cleanup_cloud_required(reset_cleanup)
            ? FP_RESET_CLEANUP_CLOUD_PENDING
            : FP_RESET_CLEANUP_NONE;
    if (fp_factory_reset_cleanup_set(next_cleanup) != ESP_OK) {
        /* The erase bit remains durable. The next boot repeats the
         * idempotent Wi-Fi/app erase and cannot enter provisioning. */
        ESP_LOGE(TAG, "reset journal handoff failed; retry required");
        return;
    }
    /* No sleep window with a valid old bearer token. */
    esp_restart();
}

fp_poll_result_t fp_poll_once(const char *boot_reason,
                              uint32_t *sleep_s_out)
{
    if (!boot_reason || !sleep_s_out || !fp_api_token_allowed()) {
        return FP_POLL_FAILED;
    }
    if (fp_wifi_connect(15000) != ESP_OK) {
        fp_errlog_record(FP_ERRLOG_WARN, "wifi connect failed");
        return FP_POLL_FAILED;
    }
    if (fp_api_departure_cleanup_pending() &&
        fp_api_departure_cleanup_attempt() != ESP_OK) {
        /* Best effort only: a failed old-cloud unlink cannot disable BYOS.
         * The pending bit remains for a later connected wake. */
        ESP_LOGW(TAG, "official-cloud unlink deferred; BYOS poll continues");
    }

    fp_display_t disp;
    if (fp_api_get_display(boot_reason, NULL, &disp) != ESP_OK) {
        fp_errlog_record(FP_ERRLOG_WARN, "display poll failed");
        return FP_POLL_FAILED;
    }
    *sleep_s_out = disp.sleep_s;

    if (disp.reset) {
        fp_factory_reset_and_restart();
        return FP_POLL_FAILED; /* only reachable if NVS erase failed */
    }

    /* The server is reachable and the bearer works: flush any events
     * recorded on earlier failed wakes. */
    fp_errlog_drain(boot_reason);

    /* OTA before hash-skip: a firmware offer must land even on a wake
     * where the art is unchanged. */
    if (disp.has_fw) {
        ESP_LOGI(TAG, "OTA offered: %s", disp.fw_version);
        if (fp_ota_apply(disp.fw_url, disp.fw_sha256) == ESP_OK) {
            esp_restart();
        }
        ESP_LOGW(TAG, "OTA failed, continuing this wake normally");
        fp_errlog_record(FP_ERRLOG_ERROR, "ota apply failed");
    }

    char last_hash[80] = "";
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs) == ESP_OK) {
        size_t len = sizeof(last_hash);
        nvs_get_str(nvs, FP_NVS_IMAGE_HASH, last_hash, &len);
        nvs_close(nvs);
    }
    if (strcmp(last_hash, disp.image_hash) == 0) {
        ESP_LOGI(TAG, "image unchanged, skipping download");
        return FP_POLL_OK_UNCHANGED;
    }

    uint8_t *buf = heap_caps_malloc(FP_IMAGE_BYTES,
                                    MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!buf) {
        ESP_LOGE(TAG, "no PSRAM for framebuffer");
        fp_errlog_record(FP_ERRLOG_ERROR, "no psram for framebuffer");
        return FP_POLL_FAILED;
    }
    esp_err_t err = fp_api_download(disp.image_url, disp.image_hash, buf);
    if (err == ESP_OK) {
        /* Radio down before the panel: the blit (and any wait the panel's
         * refresh spacing imposes) is the longest part of the wake, and
         * holding an association through it buys nothing. */
        fp_wifi_stop();
        err = fp_panel_draw(buf);
    } else {
        fp_errlog_record(FP_ERRLOG_ERROR, "image download failed");
    }
    heap_caps_free(buf);
    /* The panel refused on spacing grounds, not because anything is wrong.
     * The hash is deliberately left unrecorded below, so the next wake
     * fetches this same picture and draws it. */
    if (err == ESP_ERR_INVALID_STATE || err == ESP_ERR_TIMEOUT) {
        ESP_LOGI(TAG, "draw deferred by the panel guard");
        return FP_POLL_OK_DEFERRED;
    }
    if (err != ESP_OK) {
        return FP_POLL_FAILED;
    }

    if (nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs) == ESP_OK) {
        nvs_set_str(nvs, FP_NVS_IMAGE_HASH, disp.image_hash);
        nvs_set_u8(nvs, FP_NVS_PROV_QR_DRAWN, 0);
        nvs_set_u8(nvs, FP_NVS_PAIR_QR_DRAWN, 0);
        nvs_commit(nvs);
        nvs_close(nvs);
    }
    ESP_LOGI(TAG, "refreshed to %.23s...", disp.image_hash);
    return FP_POLL_OK_REFRESHED;
}

esp_err_t fp_provision(bool reprovision_wifi)
{
    return fp_provisioning_run(reprovision_wifi);
}

static esp_err_t show_pending_pair_qr(void)
{
    fp_pair_bundle_t bundle;
    esp_err_t err = fp_pairing_load_bundle(&bundle);
    if (err != ESP_OK) {
        return err;
    }
    char payload[FP_PAIR_QR_JSON_MAX];
    if (!fp_pair_qr_json(bundle.device_ref, bundle.nonce,
                         bundle.expires_at, payload, sizeof(payload))) {
        memset(&bundle, 0, sizeof(bundle));
        return ESP_FAIL;
    }
    memset(&bundle, 0, sizeof(bundle));
    err = fp_qr_display(payload);
    memset(payload, 0, sizeof(payload));
    if (err == ESP_OK) {
        err = nvs_set_flag(FP_NVS_PAIR_QR_DRAWN, 1);
    }
    return err;
}

esp_err_t fp_repair_run(void)
{
    fp_prov_state_t state = fp_prov_state_get();
    if (state == FP_PROV_STATE_REPAIR_VISIBLE) {
        fp_pair_expiry_t expiry = fp_pairing_expiry_status(NULL);
        if (expiry == FP_PAIR_EXPIRY_UNKNOWN) {
            /* A cold boot has a 1970 wall clock. Join and sync before making
             * any decision about a proof already visible on glass. */
            if (fp_wifi_connect(15000) != ESP_OK) {
                return ESP_FAIL;
            }
            expiry = fp_pairing_expiry_status(NULL);
        }
        if (expiry == FP_PAIR_EXPIRY_EXPIRED) {
            esp_err_t expire_err = fp_repair_expire();
            return expire_err == ESP_OK
                ? ESP_ERR_NOT_SUPPORTED : expire_err;
        }
        if (expiry != FP_PAIR_EXPIRY_LIVE) {
            return ESP_ERR_INVALID_STATE;
        }
        return nvs_flag(FP_NVS_PAIR_QR_DRAWN)
            ? ESP_OK : show_pending_pair_qr();
    }
    if (state != FP_PROV_STATE_REPAIR_INTENT &&
        state != FP_PROV_STATE_REPAIR_REGISTERING) {
        return ESP_ERR_INVALID_STATE;
    }
    char effective[FP_API_BASE_MAX];
    fp_api_base_get(effective, sizeof(effective));
    if (!fp_repair_target_supported(
            strcmp(effective, CONFIG_FP_API_BASE) == 0)) {
        /*
         * BYOS setup deliberately has no FlightPortrait device_ref, key, or
         * counter. Roll back even after a power cut between the KEY1 intent
         * write and this check. Missing identity on the official target still
         * reaches prepare_repair() below and fails closed as corruption.
         */
        esp_err_t complete_err = fp_pairing_complete();
        if (complete_err != ESP_OK) {
            return complete_err;
        }
        ESP_LOGI(TAG,
                 "pairing extension unavailable on custom server");
        return ESP_ERR_NOT_SUPPORTED;
    }
    /* Do not start the server TTL while the panel's minimum refresh interval
     * prevents the QR from appearing. app_main preserves REPAIR_INTENT and
     * sleeps exactly this guard before retrying registration. */
    if (!fp_repair_registration_allowed(fp_panel_wait_seconds())) {
        return ESP_ERR_INVALID_STATE;
    }

    fp_pair_registration_t registration;
    esp_err_t err = fp_pairing_prepare_repair(&registration);
    if (err != ESP_OK) {
        return err;
    }
    if (fp_wifi_connect(15000) != ESP_OK) {
        memset(&registration, 0, sizeof(registration));
        return ESP_FAIL;
    }
    fp_display_t display;
    err = fp_api_get_display("pairing", &registration, &display);
    memset(&registration, 0, sizeof(registration));
    if (err != ESP_OK) {
        return err;
    }
    if (display.reset) {
        fp_factory_reset_and_restart();
        return ESP_FAIL;
    }
    if (!display.has_pairing_ack) {
        /* Optional account feature: an older BYOS server remains usable. */
        err = fp_pairing_complete();
        if (err != ESP_OK) {
            return err;
        }
        ESP_LOGI(TAG, "pairing extension unavailable on current server");
        return ESP_ERR_NOT_SUPPORTED;
    }
    err = fp_pairing_accept_repair_ack(
        display.pairing_counter, display.pairing_expires_at);
    if (err == ESP_OK) {
        err = nvs_set_flag(FP_NVS_PAIR_QR_DRAWN, 0);
    }
    if (err == ESP_OK) {
        err = show_pending_pair_qr();
    }
    return err;
}

esp_err_t fp_repair_expire(void)
{
    if (fp_prov_state_get() != FP_PROV_STATE_REPAIR_VISIBLE) {
        return ESP_OK;
    }
    esp_err_t err = fp_qr_invalidate_cached_poster();
    if (err == ESP_OK) {
        err = fp_pairing_complete();
    }
    if (err == ESP_OK) {
        err = nvs_set_flag(FP_NVS_PAIR_QR_DRAWN, 0);
    }
    return err;
}

uint32_t fp_resume_sleep_seconds(uint32_t fallback_seconds)
{
    time_t now = 0;
    time(&now);
    if (now >= 1600000000 && s_planned_wake_epoch > (int64_t)now) {
        uint64_t remaining = (uint64_t)(s_planned_wake_epoch - (int64_t)now);
        return remaining > UINT32_MAX ? UINT32_MAX : (uint32_t)remaining;
    }
    return fallback_seconds;
}

void fp_deep_sleep(uint32_t seconds)
{
    fp_wifi_stop();
    fp_panel_before_sleep(seconds);
    if (seconds != 0) {
        time_t now = 0;
        time(&now);
        s_planned_wake_epoch = now >= 1600000000
            ? (int64_t)now + seconds : 0;
        esp_sleep_enable_timer_wakeup(1000000ULL * seconds);
    } else {
        s_planned_wake_epoch = 0;
    }
    esp_err_t button_err = fp_buttons_arm_wakeup();
    if (button_err != ESP_OK) {
        ESP_LOGW(TAG, "button wake unavailable: %s",
                 esp_err_to_name(button_err));
    }
    ESP_LOGI(TAG, "deep sleep %lus", (unsigned long)seconds);
    esp_deep_sleep_start();
}
