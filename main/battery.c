/* SPDX-FileCopyrightText: 2026 flightframe
 * SPDX-License-Identifier: Apache-2.0 */
#include "battery.h"

#include "driver/gpio.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

/* EE02 battery sense, per the board schematic and Seeed's PlatformIO
 * cookbook: VBAT -> TPS22916 load switch (enabled by ADC_EN, GPIO6/D5)
 * -> 10K/10K divider -> GPIO1/A0 (ADC1_CH0). Reading is VBAT/2. */
#ifndef CONFIG_FP_PIN_BAT_ADC_EN
#define CONFIG_FP_PIN_BAT_ADC_EN 6
#endif
#define BAT_ADC_CHANNEL ADC_CHANNEL_0     /* GPIO1 on ESP32-S3 */
#define BAT_SAMPLES 10
#define BAT_DIVIDER_NUM 2                 /* (R1+R2)/R2 with 10K/10K */

static const char *TAG = "battery";

uint32_t fp_battery_mv(void)
{
    gpio_config_t en = {
        .pin_bit_mask = 1ULL << CONFIG_FP_PIN_BAT_ADC_EN,
        .mode = GPIO_MODE_OUTPUT,
    };
    if (gpio_config(&en) != ESP_OK) {
        return 0;
    }
    gpio_set_level(CONFIG_FP_PIN_BAT_ADC_EN, 1);
    vTaskDelay(pdMS_TO_TICKS(20));        /* let the divider settle */

    uint32_t mv = 0;
    adc_oneshot_unit_handle_t unit = NULL;
    adc_cali_handle_t cali = NULL;
    adc_oneshot_unit_init_cfg_t unit_cfg = { .unit_id = ADC_UNIT_1 };
    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten = ADC_ATTEN_DB_12,         /* full li-ion/2 range */
        .bitwidth = ADC_BITWIDTH_12,
    };
    if (adc_oneshot_new_unit(&unit_cfg, &unit) == ESP_OK &&
        adc_oneshot_config_channel(unit, BAT_ADC_CHANNEL, &chan_cfg)
            == ESP_OK) {
        adc_cali_curve_fitting_config_t cali_cfg = {
            .unit_id = ADC_UNIT_1,
            .chan = BAT_ADC_CHANNEL,
            .atten = ADC_ATTEN_DB_12,
            .bitwidth = ADC_BITWIDTH_12,
        };
        bool calibrated =
            adc_cali_create_scheme_curve_fitting(&cali_cfg, &cali) == ESP_OK;
        int sum = 0, ok = 0;
        for (int i = 0; i < BAT_SAMPLES; i++) {
            int raw = 0;
            if (adc_oneshot_read(unit, BAT_ADC_CHANNEL, &raw) == ESP_OK) {
                int sample_mv = 0;
                if (calibrated &&
                    adc_cali_raw_to_voltage(cali, raw, &sample_mv)
                        == ESP_OK) {
                    sum += sample_mv;
                } else {
                    sum += raw * 3300 / 4095;
                }
                ok++;
            }
        }
        if (ok) {
            mv = (uint32_t)(sum / ok) * BAT_DIVIDER_NUM;
        }
        if (cali) {
            adc_cali_delete_scheme_curve_fitting(cali);
        }
    }
    if (unit) {
        adc_oneshot_del_unit(unit);
    }
    gpio_set_level(CONFIG_FP_PIN_BAT_ADC_EN, 0);

    /* Below ~2.5V the divider is floating (no battery); above 4.4V the
     * reading is noise. Either way it is not a battery voltage. */
    if (mv < 2500 || mv > 4400) {
        ESP_LOGI(TAG, "no plausible battery reading (%lu mV)",
                 (unsigned long)mv);
        return 0;
    }
    ESP_LOGI(TAG, "battery %lu mV", (unsigned long)mv);
    return mv;
}
