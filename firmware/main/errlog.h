/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Persist warn/error events across sleeps and drain them to
 * POST /device/v1/log on a later connected wake. Best-effort on both
 * sides: a full ring overwrites its oldest entry, a failed record or
 * drain never fails the wake, and a drain clears the ring after ONE
 * post attempt regardless of outcome (PROTOCOL.md §2: fire-and-forget,
 * never retried at the cost of battery). Messages must be fixed
 * firmware strings — never credentials, nonces, tokens, or URLs. */
#pragma once

#include "errlog_contract.h"

void fp_errlog_record(fp_errlog_level_t level, const char *msg);
void fp_errlog_drain(const char *boot_reason);
