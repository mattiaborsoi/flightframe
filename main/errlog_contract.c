/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "errlog_contract.h"

#include <stdio.h>
#include <string.h>

static uint8_t *slot(uint8_t *blob, unsigned i)
{
    return blob + 1 + i * FP_ERRLOG_ENTRY_SIZE;
}

static const uint8_t *cslot(const uint8_t *blob, unsigned i)
{
    return blob + 1 + i * FP_ERRLOG_ENTRY_SIZE;
}

void fp_errlog_blob_init(uint8_t *blob)
{
    memset(blob, 0, FP_ERRLOG_BLOB_SIZE);
    blob[0] = FP_ERRLOG_VERSION;
}

bool fp_errlog_blob_valid(const uint8_t *blob, size_t len)
{
    return blob && len == FP_ERRLOG_BLOB_SIZE &&
           blob[0] == FP_ERRLOG_VERSION;
}

void fp_errlog_blob_append(uint8_t *blob, uint8_t *pos, int64_t ts,
                           fp_errlog_level_t level, const char *msg)
{
    if (!blob || !pos || !msg || !msg[0]) {
        return;
    }
    uint8_t *e = slot(blob, *pos % FP_ERRLOG_ENTRIES);
    for (int i = 0; i < 8; i++) {
        e[i] = (uint8_t)((uint64_t)ts >> (8 * i));
    }
    e[8] = (uint8_t)level;
    snprintf((char *)e + 9, FP_ERRLOG_MSG_MAX, "%s", msg);
    *pos = (uint8_t)((*pos + 1) % FP_ERRLOG_ENTRIES);
}

bool fp_errlog_blob_has_entries(const uint8_t *blob)
{
    for (unsigned i = 0; i < FP_ERRLOG_ENTRIES; i++) {
        if (cslot(blob, i)[9] != 0) {
            return true;
        }
    }
    return false;
}

/* Messages are fixed firmware strings, but escape defensively anyway. */
static size_t json_escape(const char *in, char *out, size_t cap)
{
    size_t n = 0;
    for (const char *p = in; *p; p++) {
        unsigned char c = (unsigned char)*p;
        const char *simple = NULL;
        if (c == '"') simple = "\\\"";
        else if (c == '\\') simple = "\\\\";
        else if (c == '\n') simple = "\\n";
        if (simple) {
            if (n + 2 >= cap) return (size_t)-1;
            out[n++] = simple[0];
            out[n++] = simple[1];
        } else if (c < 0x20) {
            if (n + 6 >= cap) return (size_t)-1;
            n += (size_t)snprintf(out + n, cap - n, "\\u%04x", c);
        } else {
            if (n + 1 >= cap) return (size_t)-1;
            out[n++] = (char)c;
        }
    }
    out[n] = 0;
    return n;
}

size_t fp_errlog_blob_to_json(const uint8_t *blob, uint8_t pos,
                              char *out, size_t cap)
{
    if (!blob || !out || cap == 0) {
        return 0;
    }
    size_t n = (size_t)snprintf(out, cap, "{\"logs\":[");
    if (n >= cap) {
        return 0;
    }
    bool wrote = false;
    for (unsigned k = 0; k < FP_ERRLOG_ENTRIES; k++) {
        const uint8_t *e = cslot(blob, (pos + k) % FP_ERRLOG_ENTRIES);
        if (e[9] == 0) {
            continue;
        }
        int64_t ts = 0;
        for (int i = 7; i >= 0; i--) {
            ts = (int64_t)(((uint64_t)ts << 8) | e[i]);
        }
        char msg[FP_ERRLOG_MSG_MAX * 6];
        if (json_escape((const char *)e + 9, msg,
                        sizeof(msg)) == (size_t)-1) {
            return 0;
        }
        const char *level = e[8] == FP_ERRLOG_WARN ? "warn" : "error";
        int w;
        if (ts != 0) {
            w = snprintf(out + n, cap - n,
                         "%s{\"message\":\"%s\",\"level\":\"%s\","
                         "\"ts\":%lld}",
                         wrote ? "," : "", msg, level, (long long)ts);
        } else {
            w = snprintf(out + n, cap - n,
                         "%s{\"message\":\"%s\",\"level\":\"%s\"}",
                         wrote ? "," : "", msg, level);
        }
        if (w < 0 || (size_t)w >= cap - n) {
            return 0;
        }
        n += (size_t)w;
        wrote = true;
    }
    if (!wrote) {
        return 0;
    }
    size_t w = (size_t)snprintf(out + n, cap - n, "]}");
    if (w >= cap - n) {
        return 0;
    }
    return n + w;
}
