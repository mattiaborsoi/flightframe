/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include "esp_err.h"

/* Draw one full-screen black/white QR, with a standards-compliant quiet zone.
 * The payload is never logged. */
esp_err_t fp_qr_display(const char *payload);

/* Durably invalidate the poster hash before a QR can replace it on glass.
 * Expiry also calls this idempotently before dismissing QR lifecycle state. */
esp_err_t fp_qr_invalidate_cached_poster(void);
