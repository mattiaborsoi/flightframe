/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_sleep.h"
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

static esp_err_t clear_flag(const char *key)
{
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_set_u8(nvs, key, 0);
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    nvs_close(nvs);
    return err;
}

static void sleep_after_failure(nvs_handle_t nvs)
{
    uint8_t count = read_u8(nvs, FP_NVS_BACKOFF_N);
    uint32_t seconds = fp_backoff_seconds(count);
    if (count < UINT8_MAX) {
        nvs_set_u8(nvs, FP_NVS_BACKOFF_N, count + 1);
        nvs_commit(nvs);
    }
    nvs_close(nvs);
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

    if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_EXT1 &&
        action == FP_BUTTON_NONE) {
        /* A short admin/right-arrow press is intentionally a no-op. Resume
         * the interrupted cadence and do not report X-Boot-Reason: button. */
        nvs_close(nvs);
        fp_deep_sleep(fp_resume_sleep_seconds(FALLBACK_SLEEP_S));
    }

    fp_prov_state_t state = fp_prov_state_get();
    bool target_recovery = fp_api_setup_required();

    /* A displayed nonce expires on the timer wake. Any deliberate button
     * action also dismisses it early so the requested action is honored. */
    if (!target_recovery &&
        state == FP_PROV_STATE_REPAIR_VISIBLE &&
        ((wake == FP_WAKE_TIMER &&
          read_u8(nvs, FP_NVS_PAIR_QR_DRAWN)) ||
         action != FP_BUTTON_NONE)) {
        uint32_t guard = fp_panel_wait_seconds();
        if (action == FP_BUTTON_POLL && guard != 0) {
            /* Keep the still-live QR lifecycle intact until glass can accept
             * the requested poster refresh. The timer wake then expires it
             * and preserves the one-shot button live-window reason, instead
             * of showing an invalid QR through backoff. */
            s_deferred_button_poll = true;
            nvs_close(nvs);
            fp_deep_sleep(guard);
        }
        if (fp_repair_expire() != ESP_OK) {
            /* Keep the visible lifecycle recoverable until the poster-hash
             * invalidation and expiry writes are durable. */
            sleep_after_failure(nvs);
        }
        state = fp_prov_state_get();
    }

    if (!target_recovery &&
        (state == FP_PROV_STATE_REPAIR_INTENT ||
         state == FP_PROV_STATE_REPAIR_REGISTERING ||
         state == FP_PROV_STATE_REPAIR_VISIBLE)) {
        nvs_close(nvs);
        esp_err_t err = fp_repair_run();
        if (err == ESP_OK) {
            uint32_t remaining = fp_pairing_seconds_remaining();
            if (remaining != 0) {
                fp_deep_sleep(remaining);
            }
            err = ESP_ERR_INVALID_STATE;
        }
        ESP_ERROR_CHECK(nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs));
        uint32_t guard = fp_panel_wait_seconds();
        if (err == ESP_ERR_INVALID_STATE && guard != 0) {
            nvs_close(nvs);
            fp_deep_sleep(guard);
        }
        if (err != ESP_ERR_NOT_SUPPORTED) {
            sleep_after_failure(nvs);
        }
        state = fp_prov_state_get();
    }

    if (!target_recovery && action == FP_BUTTON_REPAIR &&
        state == FP_PROV_STATE_COMPLETE) {
        if (clear_flag(FP_NVS_PAIR_QR_DRAWN) != ESP_OK) {
            sleep_after_failure(nvs);
        }
        if (fp_prov_state_set(FP_PROV_STATE_REPAIR_INTENT) != ESP_OK) {
            sleep_after_failure(nvs);
        }
        nvs_close(nvs);
        esp_err_t err = fp_repair_run();
        if (err == ESP_OK) {
            uint32_t remaining = fp_pairing_seconds_remaining();
            if (remaining != 0) {
                fp_deep_sleep(remaining);
            }
            err = ESP_ERR_INVALID_STATE;
        }
        ESP_ERROR_CHECK(nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs));
        uint32_t guard = fp_panel_wait_seconds();
        if (err == ESP_ERR_INVALID_STATE && guard != 0) {
            nvs_close(nvs);
            fp_deep_sleep(guard);
        }
        if (err != ESP_ERR_NOT_SUPPORTED) {
            sleep_after_failure(nvs);
        }
    }

    state = fp_prov_state_get();
    bool token = has_device_token();
    bool provisioning_state =
        state == FP_PROV_STATE_EMPTY ||
        state == FP_PROV_STATE_QR_READY ||
        state == FP_PROV_STATE_SETUP_PENDING ||
        state == FP_PROV_STATE_PAIR_READY ||
        fp_api_setup_required();
    if (action == FP_BUTTON_REPROVISION && token) {
        if (clear_flag(FP_NVS_PROV_QR_DRAWN) != ESP_OK) {
            sleep_after_failure(nvs);
        }
        if (state == FP_PROV_STATE_COMPLETE) {
            if (fp_prov_state_set(FP_PROV_STATE_QR_READY) != ESP_OK) {
                sleep_after_failure(nvs);
            }
        }
        provisioning_state = true;
    }

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
