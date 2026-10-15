/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_system.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "api_client.h"
#include "backoff.h"
#include "buttons.h"
#include "identity.h"
#include "nvs_schema.h"
#include "ota.h"
#include "panel.h"
#include "portal.h"
#include "provisioning.h"
#include "state_machine.h"
#include "frame.h"

static const char *TAG = "flightportrait";
#define FALLBACK_SLEEP_S (12u * 3600u)
static RTC_DATA_ATTR bool s_deferred_button_poll;

static uint8_t read_u8(nvs_handle_t nvs, const char *key)
{
    uint8_t value = 0;
    nvs_get_u8(nvs, key, &value);
    return value;
}

static bool has_device_token(void)
{
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs) != ESP_OK) {
        return false;
    }
    size_t len = 0;
    bool found = nvs_get_str(nvs, FP_NVS_DEVICE_TOKEN, NULL, &len) == ESP_OK;
    nvs_close(nvs);
    return found;
}

static void sleep_after_failure(nvs_handle_t nvs)
{
    uint8_t count = read_u8(nvs, FP_NVS_BACKOFF_N);
    uint32_t seconds = fp_backoff_seconds(count);
    uint8_t next = count < UINT8_MAX ? count + 1 : count;
    if (fp_auto_portal_due(next, CONFIG_FP_AUTO_PORTAL_FAILS)) {
        /* flightframe: a day of failures. Stop failing quietly behind a
         * stale poster and show the setup page with the reason, on a clean
         * boot (the page cannot open with Wi-Fi already running). Counting
         * restarts, so the page returns at most about once a day. */
        nvs_set_u8(nvs, FP_NVS_BACKOFF_N, 0);
        nvs_commit(nvs);
        nvs_close(nvs);
        fp_attention_t why = fp_frame_failure_last();
        fp_frame_attention_request(why != FP_ATTN_NONE ? why : FP_ATTN_SERVER);
        esp_restart();
    }
    nvs_set_u8(nvs, FP_NVS_BACKOFF_N, next);
    nvs_commit(nvs);
    nvs_close(nvs);
    ESP_LOGW(TAG, "failure %u in a row; retrying in %lu s",
             (unsigned)next, (unsigned long)seconds);
    fp_deep_sleep(seconds);
}

static void shipping_sleep(void)
{
    /* Shipping mode intentionally arms no timer or button. The production
     * carrier must route USB/power sense as a reset/wake source. */
    fp_panel_before_sleep(0);
    ESP_LOGI(TAG, "shipping sleep");
    esp_deep_sleep_start();
}

static const char *poll_boot_reason(fp_wake_reason_t wake,
                                    fp_button_action_t action)
{
    if (action == FP_BUTTON_POLL) {
        return "button";
    }
    if (wake == FP_WAKE_TIMER) {
        return "rtc";
    }
    if (wake == FP_WAKE_FIRST_BOOT) {
        return "first-boot";
    }
    /* Left-arrow/KEY1 uses the explicit pairing request path. An admin
     * right-arrow/KEY0 reprovision followed by a poll must not open the
     * server's manual-refresh live window. */
    return "power-on";
}

void app_main(void)
{
    /* Never recover NVS by erasing the whole partition: that would destroy
     * the non-recoverable factory setup credential. Manufacturing recovery
     * is an explicit jig operation. */
    ESP_ERROR_CHECK(nvs_flash_init());
    fp_panel_on_boot();
    esp_err_t seed_err = fp_factory_seed_dev_credential();
    if (seed_err != ESP_OK && seed_err != ESP_ERR_NOT_FOUND) {
        ESP_LOGW(TAG, "factory credential seed unavailable: %s",
                 esp_err_to_name(seed_err));
    }

    if (fp_reset_cleanup_resume_erase(
            fp_factory_reset_cleanup_state())) {
        /* A power cut after the protected reset barrier must finish the
         * local erase before shipping/provisioning/polling can continue. */
        ESP_LOGW(TAG, "resuming interrupted factory reset");
        fp_factory_reset_and_restart();
        fp_deep_sleep(fp_backoff_seconds(0));
    }

    nvs_handle_t nvs;
    ESP_ERROR_CHECK(nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs));

#if CONFIG_FP_FACTORY_PREP
    if (!read_u8(nvs, FP_NVS_SHIPPING)) {
        nvs_close(nvs);
        esp_err_t err = fp_provisioning_factory_prep();
        if (err == ESP_ERR_INVALID_STATE && fp_panel_wait_seconds() != 0) {
            fp_deep_sleep(fp_panel_wait_seconds());
        }
        ESP_ERROR_CHECK(err);
        shipping_sleep();
    }
    nvs_close(nvs);
    shipping_sleep();
#endif

    uint8_t shipping = read_u8(nvs, FP_NVS_SHIPPING);
    if (shipping == FP_SHIPPING_FIELD_FLASH_PENDING) {
        /* The prep image leaves state 2. The mandatory first boot of the
         * normal field image converts it to customer-armed state 1 and
         * sleeps, so flashing does not start a factory-floor cadence. */
        ESP_ERROR_CHECK(nvs_set_u8(
            nvs, FP_NVS_SHIPPING, FP_SHIPPING_CUSTOMER_ARMED));
        ESP_ERROR_CHECK(nvs_commit(nvs));
        ESP_LOGI(TAG, "field image armed shipping mode");
        nvs_close(nvs);
        shipping_sleep();
    }
    if (shipping == FP_SHIPPING_CUSTOMER_ARMED) {
        if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_UNDEFINED) {
            /* A cold/USB-powered boot exits shipping; a deep-sleep wake
             * cannot, and shipping_sleep does not arm button wake. */
            ESP_ERROR_CHECK(nvs_set_u8(
                nvs, FP_NVS_SHIPPING, FP_SHIPPING_NONE));
            ESP_ERROR_CHECK(nvs_commit(nvs));
            ESP_LOGI(TAG, "exited shipping mode");
        } else {
            nvs_close(nvs);
            shipping_sleep();
        }
    }

    /* flightframe: remember a rolled-back update, then bring state left by
     * any earlier build into a shape that polls. Both are idempotent. */
    fp_frame_ota_note_rollback();
    fp_frame_boot_repair();

    uint32_t boots = 0;
    nvs_get_u32(nvs, FP_NVS_BOOT_COUNT, &boots);
    nvs_set_u32(nvs, FP_NVS_BOOT_COUNT, boots + 1);
    nvs_commit(nvs);

    fp_wake_reason_t wake = fp_classify_wake();
    fp_button_action_t action = fp_buttons_wake_action();
    if (s_deferred_button_poll) {
        /* Consume exactly once. A new explicit button gesture wins. */
        s_deferred_button_poll = false;
        if (action == FP_BUTTON_NONE) {
            action = FP_BUTTON_POLL;
        }
    }
    ESP_LOGI(TAG, "boot %lu, wake %d, action %d",
             (unsigned long)(boots + 1), wake, action);
    if (action == FP_BUTTON_FACTORY_RESET) {
        nvs_close(nvs);
        fp_factory_reset_and_restart();
        fp_deep_sleep(fp_backoff_seconds(0));
    }

    /* flightframe: the setup page, on purpose. Either a held key, or a
     * request left by a day of failures. Both run on this clean boot, before
     * anything starts Wi-Fi; if nobody uses the page it times out and the
     * frame restarts into a normal poll. */
    fp_attention_t attention = fp_frame_attention_take();
    if (action == FP_BUTTON_WIFI) {
        attention = FP_ATTN_USER;
    }
    if (attention != FP_ATTN_NONE) {
        nvs_close(nvs);
        fp_portal_open(attention);          /* restarts itself on save */
        esp_restart();
    }

    /* flightframe: a key wake that names no key used to resume the old
     * sleep, which falls back to 12 h on a frame with no clock. Every key
     * means "fetch" now, so even a spurious wake just polls. */

    /* flightframe: the upstream re-pairing flows are gone. They belong to
     * the vendor's cloud; against a private server they can never complete,
     * and a single key press used to enter one and stop the frame polling
     * for good. fp_frame_boot_repair() clears any such state left behind. */
    fp_prov_state_t state;
    state = fp_prov_state_get();
    bool token = has_device_token();
    bool provisioning_state =
        state == FP_PROV_STATE_EMPTY ||
        state == FP_PROV_STATE_QR_READY ||
        state == FP_PROV_STATE_SETUP_PENDING ||
        state == FP_PROV_STATE_PAIR_READY ||
        fp_api_setup_required();
    if (!token || provisioning_state) {
        bool reprovision = token;
        nvs_close(nvs);
#if CONFIG_FP_WIFI_PORTAL
        (void)reprovision;
        esp_err_t err = fp_portal_provision();
#else
        esp_err_t err = fp_provision(reprovision);
#endif
        if (err != ESP_OK) {
            ESP_ERROR_CHECK(nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs));
            uint32_t guard = fp_panel_wait_seconds();
            if (err == ESP_ERR_INVALID_STATE && guard != 0) {
                nvs_close(nvs);
                fp_deep_sleep(guard);
            }
            sleep_after_failure(nvs);
        }
        uint32_t guard = fp_panel_wait_seconds();
        if (guard != 0) {
            fp_deep_sleep(guard);
        }
        ESP_ERROR_CHECK(nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs));
        wake = FP_WAKE_USB_POWER;
    }

    uint32_t sleep_s = FALLBACK_SLEEP_S;
    fp_poll_result_t result = fp_poll_once(
        poll_boot_reason(wake, action), &sleep_s);
    if (result == FP_POLL_FAILED) {
        sleep_after_failure(nvs);
    }

    nvs_set_u8(nvs, FP_NVS_BACKOFF_N, 0);
    nvs_commit(nvs);
    nvs_close(nvs);
    fp_ota_confirm_if_pending();
    if (result == FP_POLL_OK_DEFERRED) {
        /* The art is in hand but the panel's refresh spacing outlasted this
         * wake. Sleep exactly until it may draw rather than until the next
         * edition, or a press would appear to do nothing for hours. */
        uint32_t wait_s = fp_panel_wait_seconds();
        if (wait_s != 0 && wait_s < sleep_s) {
            sleep_s = wait_s + 5;
        }
    }
    fp_deep_sleep(sleep_s);
}
