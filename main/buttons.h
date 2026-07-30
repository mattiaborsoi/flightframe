/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "pairing_contract.h"

/* Decode the E1004's PCB-labelled controls. Seeed wires the circular Refresh
 * icon to KEY2/GPIO5, left arrow to KEY1/GPIO4, and right arrow to KEY0/GPIO3.
 * Refresh and re-pair are immediate; the right-arrow admin control is ignored
 * unless held long enough for a recovery action. */
fp_button_action_t fp_buttons_wake_action(void);

/* Arm all three active-low keys as deep-sleep wake sources. */
esp_err_t fp_buttons_arm_wakeup(void);
