/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <stdbool.h>

#include "esp_err.h"

/* Complete BLE Security-2 transaction. If reprovision_wifi is true, a token
 * is retained when the effective cloud endpoint is unchanged. */
esp_err_t fp_provisioning_run(bool reprovision_wifi);

/* Manufacturing image path: generate per-unit runtime Security-2 material
 * and leave its standard Espressif QR on the panel. */
esp_err_t fp_provisioning_factory_prep(void);
