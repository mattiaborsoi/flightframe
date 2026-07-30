/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Error-ring blob format and its /device/v1/log JSON encoding — pure
 * string/byte code, no ESP deps, so the host test can compile it with
 * plain cc.
 *
 * The ring keeps the NEWEST FP_ERRLOG_ENTRIES events (older ones are
 * overwritten) in the `err_ring` NVS blob, with `err_pos` as the next
 * write slot. The blob is versioned: a size or version mismatch after an
 * OTA reads as empty rather than as garbage entries.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FP_ERRLOG_ENTRIES   8
#define FP_ERRLOG_MSG_MAX   48   /* including NUL */
#define FP_ERRLOG_VERSION   1
/* version byte + entries * (8-byte ts + 1-byte level + msg) */
#define FP_ERRLOG_ENTRY_SIZE (8 + 1 + FP_ERRLOG_MSG_MAX)
#define FP_ERRLOG_BLOB_SIZE  (1 + FP_ERRLOG_ENTRIES * FP_ERRLOG_ENTRY_SIZE)

typedef enum {
    FP_ERRLOG_ERROR = 0,
    FP_ERRLOG_WARN = 1,
} fp_errlog_level_t;

/* Zero every slot and stamp the version. */
void fp_errlog_blob_init(uint8_t *blob);

/* True when the blob is exactly one this firmware wrote. */
bool fp_errlog_blob_valid(const uint8_t *blob, size_t len);

/* Write one event at *pos and advance it (mod FP_ERRLOG_ENTRIES).
 * msg is truncated to fit; ts == 0 means "clock untrusted, omit". */
void fp_errlog_blob_append(uint8_t *blob, uint8_t *pos, int64_t ts,
                           fp_errlog_level_t level, const char *msg);

/* True when at least one slot holds an event. */
bool fp_errlog_blob_has_entries(const uint8_t *blob);

/* Encode the occupied slots, oldest first, as the /device/v1/log body:
 * {"logs":[{"message":…,"level":…,"ts":…}…]}. ts is omitted for
 * untrusted-clock entries. Returns the body length, or 0 when the ring
 * is empty or out lacks capacity. */
size_t fp_errlog_blob_to_json(const uint8_t *blob, uint8_t pos,
                              char *out, size_t cap);
