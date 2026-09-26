/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* BYOS server-URL normalization — pure string code, no ESP deps, so the
 * host test (tests/test_api_base.c) can compile it with plain cc.
 *
 * The rules are part of the frozen device contract (PROTOCOL.md §5):
 * provisioning normalizes instead of documenting
 * footguns. Blank restores the compiled-in default; there is no separate
 * reset action.
 */
#pragma once

#include <stddef.h>

/* NVS str capacity for the hand-set base URL: 127 chars + NUL. */
#define FP_API_BASE_MAX 128

/* Normalize a hand-entered server base URL:
 *   - trims leading/trailing spaces and tabs
 *   - blank after trim -> out = "" (means: erase override, use default)
 *   - scheme must be http:// or https:// (lowercased in the output;
 *     everything after the scheme is preserved byte-for-byte)
 *   - trailing slashes stripped
 *   - something must remain after the scheme; result must fit cap
 * Returns 0 on success (out valid), -1 on rejection (out untouched --
 * provisioning keeps the previous value and reports the error).
 */
int fp_api_base_normalize(const char *raw, char *out, size_t cap);
