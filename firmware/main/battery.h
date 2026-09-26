/* SPDX-FileCopyrightText: 2026 flightframe
 * SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdint.h>

/* Battery voltage in millivolts, measured through the board's divider.
 * 0 when no reading is available (ADC init failure, or absurd values that
 * mean the divider is floating). Safe to call on every wake: the sense
 * divider is behind a load switch and is only powered during the read. */
uint32_t fp_battery_mv(void);
