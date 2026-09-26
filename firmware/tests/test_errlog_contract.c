/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Host tests for the error ring and its /device/v1/log JSON encoding.
 *
 *   cc -Wall -Wextra -Werror main/errlog_contract.c \
 *      tests/test_errlog_contract.c -o /tmp/terrlog && /tmp/terrlog
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../main/errlog_contract.h"

static uint8_t blob[FP_ERRLOG_BLOB_SIZE];
static uint8_t pos;
static char out[4096];

static void reset(void)
{
    fp_errlog_blob_init(blob);
    pos = 0;
}

int main(void)
{
    /* A fresh blob validates, an OTA'd stranger does not. */
    reset();
    assert(fp_errlog_blob_valid(blob, sizeof(blob)));
    assert(!fp_errlog_blob_valid(blob, sizeof(blob) - 1));
    blob[0] = 99;
    assert(!fp_errlog_blob_valid(blob, sizeof(blob)));

    /* Empty ring: nothing to report, no body. */
    reset();
    assert(!fp_errlog_blob_has_entries(blob));
    assert(fp_errlog_blob_to_json(blob, pos, out, sizeof(out)) == 0);

    /* One trusted-clock error entry, exact wire shape. */
    reset();
    fp_errlog_blob_append(blob, &pos, 1789700000, FP_ERRLOG_ERROR,
                          "image download failed");
    assert(pos == 1);
    assert(fp_errlog_blob_has_entries(blob));
    size_t n = fp_errlog_blob_to_json(blob, pos, out, sizeof(out));
    assert(n == strlen(out));
    assert(strcmp(out,
        "{\"logs\":[{\"message\":\"image download failed\","
        "\"level\":\"error\",\"ts\":1789700000}]}") == 0);

    /* Untrusted clock (ts 0) omits ts so the server stamps its own;
     * warn maps to \"warn\". */
    reset();
    fp_errlog_blob_append(blob, &pos, 0, FP_ERRLOG_WARN,
                          "wifi connect failed");
    fp_errlog_blob_to_json(blob, pos, out, sizeof(out));
    assert(strcmp(out,
        "{\"logs\":[{\"message\":\"wifi connect failed\","
        "\"level\":\"warn\"}]}") == 0);

    /* Oldest-first ordering, and a full wrap keeps only the newest
     * FP_ERRLOG_ENTRIES with the overwritten slot dropped. */
    reset();
    char msg[16];
    for (int i = 0; i < FP_ERRLOG_ENTRIES + 1; i++) {
        snprintf(msg, sizeof(msg), "event %d", i);
        fp_errlog_blob_append(blob, &pos, 1789700000 + i,
                              FP_ERRLOG_ERROR, msg);
    }
    assert(pos == 1);
    fp_errlog_blob_to_json(blob, pos, out, sizeof(out));
    assert(strstr(out, "event 0") == NULL);
    const char *first = strstr(out, "event 1");
    const char *last = strstr(out, "event 8");
    assert(first && last && first < last);

    /* Message truncation to the slot size, NUL kept. */
    reset();
    char big[2 * FP_ERRLOG_MSG_MAX];
    memset(big, 'x', sizeof(big) - 1);
    big[sizeof(big) - 1] = 0;
    fp_errlog_blob_append(blob, &pos, 0, FP_ERRLOG_ERROR, big);
    n = fp_errlog_blob_to_json(blob, pos, out, sizeof(out));
    assert(n > 0);
    const char *m = strstr(out, "xxx");
    assert(m);
    size_t run = strspn(m, "x");
    assert(run == FP_ERRLOG_MSG_MAX - 1);

    /* Quotes, backslashes, and control bytes never break the JSON. */
    reset();
    fp_errlog_blob_append(blob, &pos, 0, FP_ERRLOG_ERROR,
                          "a\"b\\c\nd\x01" "e");
    fp_errlog_blob_to_json(blob, pos, out, sizeof(out));
    assert(strstr(out, "a\\\"b\\\\c\\nd\\u0001e"));

    /* An empty message is refused, not stored. */
    reset();
    fp_errlog_blob_append(blob, &pos, 0, FP_ERRLOG_ERROR, "");
    assert(pos == 0 && !fp_errlog_blob_has_entries(blob));

    /* A too-small output buffer reports 0, never a truncated body. */
    reset();
    fp_errlog_blob_append(blob, &pos, 1789700000, FP_ERRLOG_ERROR,
                          "some event");
    assert(fp_errlog_blob_to_json(blob, pos, out, 24) == 0);

    printf("errlog_contract: all cases pass\n");
    return 0;
}
