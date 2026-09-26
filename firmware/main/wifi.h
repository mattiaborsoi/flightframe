/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "esp_err.h"
#include "esp_wifi_types.h"

/* Shared ownership for normal polling and wifi_prov_mgr. Safe to call more
 * than once during one boot. */
esp_err_t fp_wifi_platform_init(void);

/* Mirror credentials received by Unified Provisioning into the explicit
 * FlightPortrait NVS schema (the manager also maintains its own Wi-Fi NVS). */
esp_err_t fp_wifi_store_credentials(const wifi_sta_config_t *cfg);

/* Load the explicit app-NVS mirror without starting the radio. Provisioning
 * recovery uses this to feed a prior credential back through the live
 * Unified Provisioning manager after that manager clears its RAM STA config. */
esp_err_t fp_wifi_load_credentials(wifi_sta_config_t *out);

/* TLS prerequisite after provisioning or a full power loss. */
esp_err_t fp_wifi_sync_time(void);

/* Connect using credentials received through Unified Provisioning and mirrored
 * to app NVS. Blocks up to timeout_ms. The frame's whole awake budget is
 * ~45 s; a WiFi that won't join in 15 s is a failed poll. */
esp_err_t fp_wifi_connect(int timeout_ms);
int fp_wifi_rssi(void);            /* 0 if unknown */
/* Clear ESP-IDF's own persistent Wi-Fi configuration. The caller erases the
 * FlightPortrait app namespace only after this succeeds. */
esp_err_t fp_wifi_factory_reset(void);
void fp_wifi_stop(void);           /* radio off before deep sleep */
