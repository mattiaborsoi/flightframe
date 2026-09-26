/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "qr_display.h"

#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "nvs.h"
#include "qrcode.h"

#include "api_client.h"
#include "nvs_schema.h"
#include "panel.h"

#define WIDTH 1200
#define HEIGHT 1600
#define ROW_BYTES (WIDTH / 2)
#define QUIET_MODULES 4

typedef struct {
    uint8_t *framebuffer;
    esp_err_t result;
} qr_context_t;

/* qrcode 0.1.x has no callback context. Generation is serialized by the
 * state machine, so a short-lived private pointer is sufficient. */
static qr_context_t *s_context;

esp_err_t fp_qr_invalidate_cached_poster(void)
{
    nvs_handle_t nvs = 0;
    esp_err_t err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_erase_key(nvs, FP_NVS_IMAGE_HASH);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        err = ESP_OK;
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    nvs_close(nvs);
    return err;
}

static void set_black(uint8_t *buf, int x, int y)
{
    size_t i = (size_t)y * ROW_BYTES + (size_t)x / 2;
    if ((x & 1) == 0) {
        buf[i] &= 0x0f; /* left pixel is the high nibble */
    } else {
        buf[i] &= 0xf0;
    }
}

static void render_qr(esp_qrcode_handle_t qr)
{
    qr_context_t *ctx = s_context;
    if (!ctx) {
        return;
    }
    int modules = esp_qrcode_get_size(qr);
    int total = modules + 2 * QUIET_MODULES;
    int scale = (WIDTH < HEIGHT ? WIDTH : HEIGHT) / total;
    if (scale < 1) {
        ctx->result = ESP_ERR_INVALID_SIZE;
        return;
    }
    int square = total * scale;
    int origin_x = (WIDTH - square) / 2 + QUIET_MODULES * scale;
    int origin_y = (HEIGHT - square) / 2 + QUIET_MODULES * scale;
    for (int my = 0; my < modules; ++my) {
        for (int mx = 0; mx < modules; ++mx) {
            if (!esp_qrcode_get_module(qr, mx, my)) {
                continue;
            }
            int x0 = origin_x + mx * scale;
            int y0 = origin_y + my * scale;
            for (int y = y0; y < y0 + scale; ++y) {
                for (int x = x0; x < x0 + scale; ++x) {
                    set_black(ctx->framebuffer, x, y);
                }
            }
        }
    }
    ctx->result = fp_panel_draw(ctx->framebuffer);
}

esp_err_t fp_qr_display(const char *payload)
{
    if (!payload || payload[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = fp_qr_invalidate_cached_poster();
    if (err != ESP_OK) {
        /* Never put a QR on glass while the old poster hash can still make
         * the recovery poll skip the redraw. */
        return err;
    }
    uint8_t *buf = heap_caps_malloc(FP_IMAGE_BYTES,
                                    MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!buf) {
        return ESP_ERR_NO_MEM;
    }
    memset(buf, 0x11, FP_IMAGE_BYTES); /* Spectra code 1 = white */
    qr_context_t ctx = {
        .framebuffer = buf,
        .result = ESP_FAIL,
    };
    esp_qrcode_config_t cfg = ESP_QRCODE_CONFIG_DEFAULT();
    cfg.display_func = render_qr;
    cfg.max_qrcode_version = 20;
    cfg.qrcode_ecc_level = ESP_QRCODE_ECC_QUART;
    /* The upstream 0.1.x component logs the encoded text at INFO. QR
     * payloads contain live credentials/nonces and must never be logged. */
    esp_log_level_set("QRCODE", ESP_LOG_NONE);
    s_context = &ctx;
    err = esp_qrcode_generate(&cfg, payload);
    s_context = NULL;
    if (err == ESP_OK) {
        err = ctx.result;
    }
    heap_caps_free(buf);
    return err;
}
