/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "buttons.h"

#include "esp_log.h"
#include "frame_policy.h"

#include "driver/gpio.h"
#include "esp_sleep.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "fp_buttons";

static const gpio_num_t s_keys[] = {
    CONFIG_FP_PIN_KEY0,
    CONFIG_FP_PIN_KEY1,
    CONFIG_FP_PIN_KEY2,
};

static void configure_inputs(void)
{
    uint64_t mask = 0;
    for (size_t i = 0; i < sizeof(s_keys) / sizeof(s_keys[0]); ++i) {
        mask |= 1ULL << s_keys[i];
    }
    gpio_config_t cfg = {
        .pin_bit_mask = mask,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);
}

static bool any_key_down(void)
{
    for (size_t i = 0; i < sizeof(s_keys) / sizeof(s_keys[0]); ++i) {
        if (gpio_get_level(s_keys[i]) == 0) {
            return true;
        }
    }
    return false;
}

/* flightframe: every key does the same thing, so nobody has to know which
 * is which (the upstream map was laid out for a different board's printed
 * icons). From sleep, a tap fetches and a hold opens the Wi-Fi page; held
 * through a reset, a key factory-resets. The rules are in frame_policy.c. */
fp_button_action_t fp_buttons_wake_action(void)
{
    esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
    bool from_sleep = cause == ESP_SLEEP_WAKEUP_EXT1;
    if (!from_sleep && cause != ESP_SLEEP_WAKEUP_UNDEFINED) {
        return FP_BUTTON_NONE;              /* timer wake: no key involved */
    }
    configure_inputs();
    int pin = -1;
    if (from_sleep) {
        uint64_t status = esp_sleep_get_ext1_wakeup_status();
        for (size_t i = 0; i < sizeof(s_keys) / sizeof(s_keys[0]); ++i) {
            if (status & (1ULL << s_keys[i])) {
                pin = s_keys[i];
                break;
            }
        }
    } else {
        vTaskDelay(pdMS_TO_TICKS(10));      /* let the pull-ups settle */
        for (size_t i = 0; i < sizeof(s_keys) / sizeof(s_keys[0]); ++i) {
            if (gpio_get_level(s_keys[i]) == 0) {
                pin = s_keys[i];
                break;
            }
        }
    }
    if (pin < 0) {
        return FP_BUTTON_NONE;
    }
    uint32_t limit = from_sleep ? CONFIG_FP_WIFI_HOLD_MS
                                : CONFIG_FP_RESET_COMBO_HOLD_MS;
    uint32_t held_ms = 0;
    while (held_ms < limit && any_key_down()) {
        vTaskDelay(pdMS_TO_TICKS(25));
        held_ms += 25;
    }
    fp_button_action_t action = fp_gesture_classify(
        from_sleep, held_ms, CONFIG_FP_WIFI_HOLD_MS,
        CONFIG_FP_RESET_COMBO_HOLD_MS);
    ESP_LOGI(TAG, "key on GPIO%d %s, held %lu ms -> action %d", pin,
             from_sleep ? "woke the frame" : "held through reset",
             (unsigned long)held_ms, action);
    return action;
}

esp_err_t fp_buttons_arm_wakeup(void)
{
    configure_inputs();
    uint64_t mask = 0;
    for (size_t i = 0; i < sizeof(s_keys) / sizeof(s_keys[0]); ++i) {
        mask |= 1ULL << s_keys[i];
    }
    return esp_sleep_enable_ext1_wakeup(mask, ESP_EXT1_WAKEUP_ANY_LOW);
}
