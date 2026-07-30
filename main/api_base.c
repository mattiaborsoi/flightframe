/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
#include "api_base.h"

#include <ctype.h>
#include <string.h>

int fp_api_base_normalize(const char *raw, char *out, size_t cap)
{
    while (*raw == ' ' || *raw == '\t') {
        raw++;
    }
    size_t len = strlen(raw);
    while (len && (raw[len - 1] == ' ' || raw[len - 1] == '\t')) {
        len--;
    }
    if (!len) {
        if (!cap) {
            return -1;
        }
        out[0] = 0;               /* blank = restore the compiled default */
        return 0;
    }

    size_t scheme = 0;
    if (len >= 7 && !strncasecmp(raw, "http://", 7)) {
        scheme = 7;
    } else if (len >= 8 && !strncasecmp(raw, "https://", 8)) {
        scheme = 8;
    } else {
        return -1;                /* not http/https: rejected, not stored */
    }

    while (len > scheme && raw[len - 1] == '/') {
        len--;                    /* trailing slashes are ours to remove */
    }
    if (len <= scheme || len >= cap) {
        return -1;                /* empty host, or does not fit NVS cap */
    }

    memcpy(out, raw, len);
    out[len] = 0;
    for (size_t i = 0; i < scheme; i++) {
        out[i] = (char)tolower((unsigned char)out[i]);
    }
    return 0;
}
