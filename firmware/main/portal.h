/* SPDX-FileCopyrightText: 2026 flightframe
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "esp_err.h"

/* Captive-portal provisioning: the phone-only replacement for the BLE flow.
 *
 * With no Wi-Fi credentials stored, the frame draws a QR that joins the
 * phone to the frame's own open hotspot; the OS captive-portal check then
 * pops a page that lists nearby networks and asks for the home password.
 * Saving stores the credentials plus the compiled-in server target and
 * reboots. On the next boot (credentials present, no device token) this
 * function connects, performs the protocol setup call, and returns ESP_OK
 * so the normal poll continues immediately.
 *
 * Returns ESP_OK when the frame is ready to poll; an error to back off. */
esp_err_t fp_portal_provision(void);
