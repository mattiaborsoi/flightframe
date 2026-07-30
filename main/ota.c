/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "ota.h"

#include <stdio.h>
#include <string.h>

#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "mbedtls/sha256.h"

#include "pairing_contract.h"

static const char *TAG = "fp_ota";

#define CHUNK 4096

esp_err_t fp_ota_apply(const char *url, const char *sha256_hex)
{
    if (!fp_http_url_fits(url, 768) ||
        !fp_ota_hash_valid(sha256_hex)) {
        return ESP_ERR_INVALID_ARG;
    }
    const esp_partition_t *target = esp_ota_get_next_update_partition(NULL);
    if (target == NULL) {
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "writing OTA to %s", target->label);

    esp_http_client_config_t cfg = {
        .url = url,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 30000,
    };
    esp_http_client_handle_t http = esp_http_client_init(&cfg);
    if (!http) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = esp_http_client_open(http, 0);
    if (err != ESP_OK) {
        esp_http_client_cleanup(http);
        return err;
    }
    esp_http_client_fetch_headers(http);

    esp_ota_handle_t ota;
    err = esp_ota_begin(target, OTA_SIZE_UNKNOWN, &ota);
    if (err != ESP_OK) {
        esp_http_client_cleanup(http);
        return err;
    }

    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    mbedtls_sha256_starts(&sha, 0);

    static uint8_t buf[CHUNK];
    int total = 0;
    while (1) {
        int n = esp_http_client_read(http, (char *)buf, CHUNK);
        if (n < 0) {
            err = ESP_FAIL;
            break;
        }
        if (n == 0) {
            break;
        }
        mbedtls_sha256_update(&sha, buf, n);
        err = esp_ota_write(ota, buf, n);
        if (err != ESP_OK) {
            break;
        }
        total += n;
    }
    int status = esp_http_client_get_status_code(http);
    esp_http_client_close(http);
    esp_http_client_cleanup(http);

    unsigned char digest[32];
    mbedtls_sha256_finish(&sha, digest);
    mbedtls_sha256_free(&sha);
    char hex[65];
    for (int i = 0; i < 32; i++) {
        snprintf(hex + i * 2, 3, "%02x", digest[i]);
    }

    if (err != ESP_OK || status != 200 || total == 0 ||
        strcmp(hex, sha256_hex) != 0) {
        ESP_LOGE(TAG, "OTA rejected: err=%d http=%d bytes=%d sha_ok=%d",
                 err, status, total, strcmp(hex, sha256_hex) == 0);
        esp_ota_abort(ota);
        return ESP_FAIL;      /* never boot an unverified image */
    }
    err = esp_ota_end(ota);   /* validates image header/structure */
    if (err != ESP_OK) {
        return err;
    }
    err = esp_ota_set_boot_partition(target);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "OTA verified (%d bytes), boot set to %s",
                 total, target->label);
    }
    return err;
}

void fp_ota_confirm_if_pending(void)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(running, &state) == ESP_OK
            && state == ESP_OTA_IMG_PENDING_VERIFY) {
        esp_ota_mark_app_valid_cancel_rollback();
        ESP_LOGI(TAG, "new firmware confirmed (rollback cancelled)");
    }
}
