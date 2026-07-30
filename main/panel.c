/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "panel.h"

#include "panel_guard.h"

#include "esp_attr.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#include "epd13in3e.h"

static const char *TAG = "fp_panel";

/* esp_timer_get_time() restarts at zero after deep sleep. Retain a remaining
 * duration and the planned timer sleep instead of an invalid absolute time.
 * An early button wake is conservative: it does not claim the full planned
 * interval elapsed. */
static RTC_DATA_ATTR uint32_t s_guard_remaining_s;
static RTC_DATA_ATTR uint32_t s_planned_sleep_s;
static int64_t s_guard_boot_us;
/* Not retained: a blit cannot survive a reboot, so "in progress" is only
 * ever true within one boot. */
static bool s_drawing;


static void account_awake_time(void)
{
    int64_t now = esp_timer_get_time();
    if (s_guard_boot_us == 0) {
        s_guard_boot_us = now;
        return;
    }
    uint32_t elapsed = (uint32_t)((now - s_guard_boot_us) / 1000000);
    s_guard_remaining_s = fp_panel_guard_after_awake(s_guard_remaining_s,
                                                     elapsed);
    s_guard_boot_us = now;
}

void fp_panel_on_boot(void)
{
    bool timer_wake =
        esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER;
    s_guard_remaining_s = fp_panel_guard_after_sleep(
        s_guard_remaining_s, s_planned_sleep_s, timer_wake);
    s_planned_sleep_s = 0;
    s_guard_boot_us = esp_timer_get_time();
    s_drawing = false;
}

uint32_t fp_panel_wait_seconds(void)
{
    account_awake_time();
    return s_guard_remaining_s;
}

void fp_panel_before_sleep(uint32_t planned_sleep_s)
{
    account_awake_time();
    s_planned_sleep_s = planned_sleep_s;
}

esp_err_t fp_panel_draw(const uint8_t *buf)
{
    uint32_t wait_s = 0;
    fp_panel_draw_plan_t plan = fp_panel_guard_plan(
        fp_panel_wait_seconds(), s_drawing,
        CONFIG_FP_MAX_GUARD_WAIT_S, &wait_s);

    if (plan == FP_PANEL_DRAW_BUSY) {
        /* Never blit over a blit: it takes tens of seconds and drives the
         * panel's rails through a strict power sequence. */
        ESP_LOGW(TAG, "refresh already in progress, ignoring");
        return ESP_ERR_INVALID_STATE;
    }
    if (plan == FP_PANEL_DRAW_AFTER_WAIT) {
        uint32_t remaining = fp_panel_wait_seconds();
        if (remaining > wait_s) {
            /* Longer than we are willing to stay awake for. The image is
             * dropped rather than half-honoured; the next wake fetches and
             * draws it, and the hash is not recorded, so nothing is lost. */
            ESP_LOGW(TAG, "refresh needs %lus, over the %lus awake budget",
                     (unsigned long)remaining,
                     (unsigned long)CONFIG_FP_MAX_GUARD_WAIT_S);
            return ESP_ERR_TIMEOUT;
        }
        /* The image is already in PSRAM and PSRAM does not survive deep
         * sleep, so sleeping this out would cost a second 960 KB download
         * for the same picture. Wait it out with the radio already down and
         * then blit — a press still ends in a redraw, just a slower one. */
        ESP_LOGI(TAG, "holding %lus for the panel's refresh spacing",
                 (unsigned long)wait_s);
        vTaskDelay(pdMS_TO_TICKS(wait_s * 1000U));
        account_awake_time();
    }

    s_drawing = true;
    esp_err_t err = epd_init();
    if (err == ESP_OK) {
        err = epd_blit(buf);
    }
    epd_sleep();          /* always power the panel back down */
    s_drawing = false;
    if (err == ESP_OK) {
        s_guard_remaining_s = CONFIG_FP_MIN_REFRESH_SPACING_S;
        s_guard_boot_us = esp_timer_get_time();
    }
    return err;
}
