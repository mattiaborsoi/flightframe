/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "errlog.h"

#include <string.h>
#include <time.h>

#include "nvs.h"

#include "api_client.h"
#include "nvs_schema.h"

static bool load_ring(nvs_handle_t nvs, uint8_t *blob, uint8_t *pos)
{
    size_t len = FP_ERRLOG_BLOB_SIZE;
    if (nvs_get_blob(nvs, FP_NVS_ERR_RING, blob, &len) != ESP_OK ||
        !fp_errlog_blob_valid(blob, len)) {
        fp_errlog_blob_init(blob);
    }
    if (nvs_get_u8(nvs, FP_NVS_ERR_RING_POS, pos) != ESP_OK ||
        *pos >= FP_ERRLOG_ENTRIES) {
        *pos = 0;
    }
    return true;
}

void fp_errlog_record(fp_errlog_level_t level, const char *msg)
{
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs) != ESP_OK) {
        return;
    }
    uint8_t blob[FP_ERRLOG_BLOB_SIZE];
    uint8_t pos = 0;
    load_ring(nvs, blob, &pos);
    time_t now = 0;
    time(&now);
    /* Same trusted-clock bar as the sleep planner: pre-SNTP is 1970. */
    int64_t ts = now >= 1600000000 ? (int64_t)now : 0;
    fp_errlog_blob_append(blob, &pos, ts, level, msg);
    if (nvs_set_blob(nvs, FP_NVS_ERR_RING, blob,
                     FP_ERRLOG_BLOB_SIZE) == ESP_OK &&
        nvs_set_u8(nvs, FP_NVS_ERR_RING_POS, pos) == ESP_OK) {
        nvs_commit(nvs);
    }
    nvs_close(nvs);
}

void fp_errlog_drain(const char *boot_reason)
{
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs) != ESP_OK) {
        return;
    }
    uint8_t blob[FP_ERRLOG_BLOB_SIZE];
    uint8_t pos = 0;
    load_ring(nvs, blob, &pos);
    if (!fp_errlog_blob_has_entries(blob)) {
        nvs_close(nvs);
        return;
    }
    char body[3072];
    size_t n = fp_errlog_blob_to_json(blob, pos, body, sizeof(body));
    if (n != 0) {
        fp_api_post_logs(body, boot_reason);
    }
    /* One attempt, then forget — success or not. */
    nvs_erase_key(nvs, FP_NVS_ERR_RING);
    nvs_erase_key(nvs, FP_NVS_ERR_RING_POS);
    nvs_commit(nvs);
    nvs_close(nvs);
}
