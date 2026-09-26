/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Firmware-owned failure backoff (PROTOCOL.md §3). */
#pragma once
#include <stdint.h>

/* min(2^n * 5 min, 6 h), in seconds. n = consecutive failures. */
uint32_t fp_backoff_seconds(uint8_t n);
