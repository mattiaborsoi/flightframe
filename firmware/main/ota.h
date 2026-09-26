/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* OTA apply: stream the image into the inactive partition, verifying
 * sha256 as it arrives (PROTOCOL.md §2). On success the boot flag is set
 * and the caller restarts; the new image must confirm within one poll or
 * the bootloader rolls back (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE). */
#pragma once
#include "esp_err.h"

esp_err_t fp_ota_apply(const char *url, const char *sha256_hex);

/* Call after the first successful poll: cancels rollback for a
 * pending-verify image. No-op otherwise. */
void fp_ota_confirm_if_pending(void);
