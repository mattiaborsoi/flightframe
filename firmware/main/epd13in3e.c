/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Register/init sequence ported from the Waveshare EPD_13in3e reference
 * driver (github.com/waveshareteam/e-Paper, 13.3inch_e-Paper_E), which is
 * distributed by the Waveshare team under the following MIT-style notice:
 *
 *   Permission is hereby granted, free of charge, to any person obtaining
 *   a copy of this software and associated documentation files (the
 *   "Software"), to deal in the Software without restriction, including
 *   without limitation the rights to use, copy, modify, merge, publish,
 *   distribute, sublicense, and/or sell copies of the Software, and to
 *   permit persons to whom the Software is furnished to do so, subject to
 *   the following conditions: The above copyright notice and this
 *   permission notice shall be included in all copies or substantial
 *   portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT
 *   WARRANTY OF ANY KIND.
 */
#include "epd13in3e.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "epd13in3e";

/* reTerminal E1004 wiring (Kconfig-overridable for the EE02 later). */
#define PIN_SCK   CONFIG_FP_PIN_EPD_SCK
#define PIN_MOSI  CONFIG_FP_PIN_EPD_MOSI
#define PIN_CS_M  CONFIG_FP_PIN_EPD_CS_M
#define PIN_CS_S  CONFIG_FP_PIN_EPD_CS_S
#define PIN_DC    CONFIG_FP_PIN_EPD_DC
#define PIN_RST   CONFIG_FP_PIN_EPD_RST
#define PIN_BUSY  CONFIG_FP_PIN_EPD_BUSY
#define PIN_EN    CONFIG_FP_PIN_EPD_EN

#define CS_MASTER 0x1
#define CS_SLAVE  0x2
#define CS_BOTH   (CS_MASTER | CS_SLAVE)

#define ROW_BYTES      (EPD_WIDTH / 2)      /* 600: one full row     */
#define HALF_ROW_BYTES (ROW_BYTES / 2)      /* 300: one chip's share */

/* Registers + values per the Waveshare EPD_13in3e reference. */
#define R_PSR   0x00
#define R_PWR   0x01
#define R_POF   0x02
#define R_PON   0x04
#define R_BTSTN 0x05
#define R_BTSTP 0x06
#define R_DSLP  0x07
#define R_DTM   0x10
#define R_DRF   0x12
#define R_CDI   0x50
#define R_TCON  0x60
#define R_TRES  0x61
#define R_ANTM  0x74
#define R_AGID  0x86
#define R_VDDN  0xB0
#define R_VCOM  0xB1
#define R_ENBUF 0xB6
#define R_VDDP  0xB7
#define R_CCSET 0xE0
#define R_PWS   0xE3
#define R_CMD66 0xF0

static const uint8_t V_PSR[]   = {0xDF, 0x69};
static const uint8_t V_PWR[]   = {0x0F, 0x00, 0x28, 0x2C, 0x28, 0x38};
static const uint8_t V_POF[]   = {0x00};
static const uint8_t V_DRF[]   = {0x00};
static const uint8_t V_CDI[]   = {0xF7};
static const uint8_t V_TCON[]  = {0x03, 0x03};
static const uint8_t V_TRES[]  = {0x04, 0xB0, 0x03, 0x20};   /* 1200x800/chip */
static const uint8_t V_CMD66[] = {0x49, 0x55, 0x13, 0x5D, 0x05, 0x10};
static const uint8_t V_ENBUF[] = {0x07};
static const uint8_t V_CCSET[] = {0x01};
static const uint8_t V_PWS[]   = {0x22};
static const uint8_t V_ANTM[]  = {0xC0, 0x1C, 0x1C, 0xCC, 0xCC, 0xCC,
                                  0x15, 0x15, 0x55};
static const uint8_t V_AGID[]  = {0x10};
static const uint8_t V_BTSTP[] = {0xE8, 0x28};
static const uint8_t V_VDDP[]  = {0x01};
static const uint8_t V_BTSTN[] = {0xE8, 0x28};
static const uint8_t V_VDDN[]  = {0x01};
static const uint8_t V_VCOM[]  = {0x02};

static spi_device_handle_t s_spi;
static bool s_bus_ready;

/* ------------------------------------------------------------------ SPI */

static void cs_set(int mask, int level)
{
    if (mask & CS_MASTER) gpio_set_level(PIN_CS_M, level);
    if (mask & CS_SLAVE)  gpio_set_level(PIN_CS_S, level);
}

static esp_err_t xfer(const uint8_t *buf, size_t len)
{
    spi_transaction_t t = {.length = len * 8, .tx_buffer = buf};
    return spi_device_polling_transmit(s_spi, &t);
}

/* CS asserted by caller: first byte with DC low = command, rest = data. */
static esp_err_t send(uint8_t reg, const uint8_t *data, size_t len)
{
    gpio_set_level(PIN_DC, 0);
    esp_err_t err = xfer(&reg, 1);
    gpio_set_level(PIN_DC, 1);
    if (err == ESP_OK && len) {
        err = xfer(data, len);
    }
    return err;
}

static esp_err_t cmd_to(int cs_mask, uint8_t reg,
                        const uint8_t *data, size_t len)
{
    cs_set(cs_mask, 0);
    esp_err_t err = send(reg, data, len);
    cs_set(CS_BOTH, 1);
    return err;
}

/* BUSY is LOW while busy, HIGH when idle (Seeed/Waveshare semantics). */
static esp_err_t busy_wait(const char *what, int timeout_ms)
{
    int waited = 0;
    while (gpio_get_level(PIN_BUSY) == 0) {
        vTaskDelay(pdMS_TO_TICKS(10));
        waited += 10;
        if (waited > timeout_ms) {
            ESP_LOGE(TAG, "%s: busy timeout after %d ms", what, waited);
            return ESP_ERR_TIMEOUT;
        }
    }
    vTaskDelay(pdMS_TO_TICKS(20));
    return ESP_OK;
}

/* ----------------------------------------------------------------- init */

static void reset_pulse(void)
{
    gpio_set_level(PIN_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(30));
    gpio_set_level(PIN_RST, 0);
    vTaskDelay(pdMS_TO_TICKS(30));
    gpio_set_level(PIN_RST, 1);
    vTaskDelay(pdMS_TO_TICKS(30));
}

esp_err_t epd_init(void)
{
    if (!s_bus_ready) {
        gpio_config_t out = {
            .pin_bit_mask = (1ULL << PIN_CS_M) | (1ULL << PIN_CS_S) |
                            (1ULL << PIN_DC) | (1ULL << PIN_RST) |
                            (1ULL << PIN_EN),
            .mode = GPIO_MODE_OUTPUT,
        };
        ESP_ERROR_CHECK(gpio_config(&out));
        gpio_config_t in = {
            .pin_bit_mask = 1ULL << PIN_BUSY,
            .mode = GPIO_MODE_INPUT,
        };
        ESP_ERROR_CHECK(gpio_config(&in));
        cs_set(CS_BOTH, 1);
        gpio_set_level(PIN_DC, 1);
        gpio_set_level(PIN_RST, 1);

        spi_bus_config_t bus = {
            .mosi_io_num = PIN_MOSI,
            .miso_io_num = -1,
            .sclk_io_num = PIN_SCK,
            .quadwp_io_num = -1,
            .quadhd_io_num = -1,
            .max_transfer_sz = 4096,
        };
        ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));
        spi_device_interface_config_t dev = {
            .clock_speed_hz = 10 * 1000 * 1000,   /* Seeed uses 10 MHz */
            .mode = 0,
            .spics_io_num = -1,                   /* dual CS: manual   */
            .queue_size = 2,
        };
        ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev, &s_spi));
        s_bus_ready = true;
    }

    gpio_set_level(PIN_EN, 1);                    /* panel power rail  */
    vTaskDelay(pdMS_TO_TICKS(10));
    reset_pulse();

    /* Waveshare reference order. Analog/power registers go to the master
     * only; panel-wide config goes to both controllers. */
    esp_err_t err = ESP_OK;
    err |= cmd_to(CS_MASTER, R_ANTM, V_ANTM, sizeof(V_ANTM));
    err |= cmd_to(CS_BOTH, R_CMD66, V_CMD66, sizeof(V_CMD66));
    err |= cmd_to(CS_BOTH, R_PSR, V_PSR, sizeof(V_PSR));
    err |= cmd_to(CS_BOTH, R_CDI, V_CDI, sizeof(V_CDI));
    err |= cmd_to(CS_BOTH, R_TCON, V_TCON, sizeof(V_TCON));
    err |= cmd_to(CS_BOTH, R_AGID, V_AGID, sizeof(V_AGID));
    err |= cmd_to(CS_BOTH, R_PWS, V_PWS, sizeof(V_PWS));
    err |= cmd_to(CS_BOTH, R_CCSET, V_CCSET, sizeof(V_CCSET));
    err |= cmd_to(CS_BOTH, R_TRES, V_TRES, sizeof(V_TRES));
    err |= cmd_to(CS_MASTER, R_PWR, V_PWR, sizeof(V_PWR));
    err |= cmd_to(CS_MASTER, R_ENBUF, V_ENBUF, sizeof(V_ENBUF));
    err |= cmd_to(CS_MASTER, R_BTSTP, V_BTSTP, sizeof(V_BTSTP));
    err |= cmd_to(CS_MASTER, R_VDDP, V_VDDP, sizeof(V_VDDP));
    err |= cmd_to(CS_MASTER, R_BTSTN, V_BTSTN, sizeof(V_BTSTN));
    err |= cmd_to(CS_MASTER, R_VDDN, V_VDDN, sizeof(V_VDDN));
    err |= cmd_to(CS_MASTER, R_VCOM, V_VCOM, sizeof(V_VCOM));
    return err == ESP_OK ? ESP_OK : ESP_FAIL;
}

/* ----------------------------------------------------------------- blit */

static esp_err_t send_half(int cs_mask, const uint8_t *buf, int offset)
{
    cs_set(cs_mask, 0);
    gpio_set_level(PIN_DC, 0);
    uint8_t reg = R_DTM;
    esp_err_t err = xfer(&reg, 1);
    gpio_set_level(PIN_DC, 1);
    for (int row = 0; row < EPD_HEIGHT && err == ESP_OK; row++) {
        err = xfer(buf + row * ROW_BYTES + offset, HALF_ROW_BYTES);
        /* Reference paces ~1 ms/row; yield periodically for the WDT. */
        esp_rom_delay_us(800);
        if ((row & 0xFF) == 0xFF) {
            vTaskDelay(1);
        }
    }
    cs_set(CS_BOTH, 1);
    return err;
}

esp_err_t epd_blit(const uint8_t *buf)
{
    esp_err_t err = send_half(CS_MASTER, buf, 0);
    if (err == ESP_OK) {
        err = send_half(CS_SLAVE, buf, HALF_ROW_BYTES);
    }
    if (err != ESP_OK) {
        return err;
    }

    err = cmd_to(CS_BOTH, R_PON, NULL, 0);
    if (err != ESP_OK || busy_wait("PON", 3000) != ESP_OK) {
        return ESP_FAIL;
    }
    vTaskDelay(pdMS_TO_TICKS(50));
    err = cmd_to(CS_BOTH, R_DRF, V_DRF, sizeof(V_DRF));
    /* Full-color refresh: 12 s typical @25 C (T133A01 datasheet); the
     * generous timeout covers cold-panel worst cases. */
    if (err != ESP_OK || busy_wait("DRF", 60000) != ESP_OK) {
        return ESP_FAIL;
    }
    err = cmd_to(CS_BOTH, R_POF, V_POF, sizeof(V_POF));
    busy_wait("POF", 3000);
    ESP_LOGI(TAG, "refresh complete");
    return err;
}

void epd_sleep(void)
{
    uint8_t a5 = 0xA5;
    cmd_to(CS_BOTH, R_DSLP, &a5, 1);
    /* Datasheet §7: let the driver rails discharge after power-off
     * sequencing before cutting supply — never yank power mid-sequence. */
    vTaskDelay(pdMS_TO_TICKS(100));
    gpio_set_level(PIN_EN, 0);                    /* panel rail off */
}
