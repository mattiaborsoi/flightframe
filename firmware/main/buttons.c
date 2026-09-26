/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "buttons.h"

#include "driver/gpio.h"
#include "esp_sleep.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

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

fp_button_action_t fp_buttons_wake_action(void)
{
    if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_EXT1) {
        return FP_BUTTON_NONE;
    }
    configure_inputs();
    uint64_t status = esp_sleep_get_ext1_wakeup_status();
    if (status & (1ULL << s_keys[2])) {
        return fp_button_classify(2, 0, CONFIG_FP_REPROVISION_HOLD_MS,
                                  CONFIG_FP_FACTORY_RESET_HOLD_MS);
    }
    if (status & (1ULL << s_keys[1])) {
        return fp_button_classify(1, 0, CONFIG_FP_REPROVISION_HOLD_MS,
                                  CONFIG_FP_FACTORY_RESET_HOLD_MS);
    }
    if (!(status & (1ULL << s_keys[0]))) {
        return FP_BUTTON_NONE;
    }

    uint32_t held_ms = 0;
    while (gpio_get_level(s_keys[0]) == 0 &&
           held_ms < CONFIG_FP_FACTORY_RESET_HOLD_MS) {
        vTaskDelay(pdMS_TO_TICKS(25));
        held_ms += 25;
    }
    return fp_button_classify(0, held_ms, CONFIG_FP_REPROVISION_HOLD_MS,
                              CONFIG_FP_FACTORY_RESET_HOLD_MS);
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
