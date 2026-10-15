/* SPDX-FileCopyrightText: 2026 flightframe
 * SPDX-License-Identifier: Apache-2.0 */
#include "frame_policy.h"

#include <stdio.h>
#include <string.h>

fp_button_action_t fp_gesture_classify(bool from_sleep, uint32_t held_ms,
                                       uint32_t wifi_hold_ms,
                                       uint32_t reset_combo_ms)
{
    if (from_sleep) {
        if (wifi_hold_ms != 0 && held_ms >= wifi_hold_ms) {
            return FP_BUTTON_WIFI;
        }
        return FP_BUTTON_POLL;
    }
    /* Reset or power-on with a key down. Only a deliberate hold through
     * boot counts; a key brushed while plugging in USB does nothing. */
    if (reset_combo_ms != 0 && held_ms >= reset_combo_ms) {
        return FP_BUTTON_FACTORY_RESET;
    }
    return FP_BUTTON_NONE;
}

bool fp_auto_portal_due(uint8_t consecutive_failures, uint8_t threshold)
{
    return threshold != 0 && consecutive_failures >= threshold;
}

bool fp_fw_offer_acceptable(const char *offered, const char *running,
                            const char *rejected)
{
    if (!offered || !offered[0]) {
        return false;
    }
    if (running && strcmp(offered, running) == 0) {
        return false;
    }
    if (rejected && rejected[0] && strcmp(offered, rejected) == 0) {
        return false;
    }
    return true;
}

static const char *const MONTHS[12] = {
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
};

static int month_index(const char *m)
{
    for (int i = 0; i < 12; ++i) {
        if (strncmp(m, MONTHS[i], 3) == 0) {
            return i + 1;
        }
    }
    return 0;
}

/* Days from 1970-01-01 to a proleptic Gregorian date (Howard Hinnant's
 * days_from_civil), so no libc time zone state is involved. */
static int64_t days_from_civil(int64_t y, int m, int d)
{
    y -= m <= 2;
    int64_t era = (y >= 0 ? y : y - 399) / 400;
    int64_t yoe = y - era * 400;
    int64_t doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    int64_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}

static bool valid_date(int y, int m, int d)
{
    static const int mdays[12] = {31, 29, 31, 30, 31, 30,
                                  31, 31, 30, 31, 30, 31};
    return y >= 1970 && y <= 9999 && m >= 1 && m <= 12 &&
           d >= 1 && d <= mdays[m - 1];
}

bool fp_http_date_parse(const char *text, int64_t *out)
{
    /* "Wed, 15 Oct 2026 16:40:00 GMT": the weekday is ignored (it is
     * redundant), the zone must be GMT, every field must be in range. */
    char mon[4] = {0}, zone[4] = {0};
    int d, y, hh, mm, ss;
    if (!text || !out) {
        return false;
    }
    const char *comma = strchr(text, ',');
    if (!comma || comma - text != 3 ||
        sscanf(comma + 1, " %d %3s %d %d:%d:%d %3s",
               &d, mon, &y, &hh, &mm, &ss, zone) != 7 ||
        strcmp(zone, "GMT") != 0) {
        return false;
    }
    int m = month_index(mon);
    if (!m || !valid_date(y, m, d) || hh < 0 || hh > 23 || mm < 0 ||
        mm > 59 || ss < 0 || ss > 60) {
        return false;
    }
    *out = days_from_civil(y, m, d) * 86400 + hh * 3600 + mm * 60 + ss;
    return true;
}

bool fp_build_date_parse(const char *text, int64_t *out)
{
    char mon[4];
    int d, y;
    if (!text || !out || sscanf(text, "%3s %d %d", mon, &d, &y) != 3) {
        return false;
    }
    int m = month_index(mon);
    if (!m || !valid_date(y, m, d)) {
        return false;
    }
    *out = days_from_civil(y, m, d) * 86400;
    return true;
}

bool fp_clock_plausible(int64_t candidate, int64_t build_floor)
{
    const int64_t twenty_years = (int64_t)20 * 366 * 86400;
    return build_floor > 0 && candidate >= build_floor &&
           candidate < build_floor + twenty_years;
}
