/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Host-side unit test for BYOS URL normalization (PROTOCOL.md §5).
 * These semantics are frozen once units ship: blank
 * restores the default, http/https only, trailing slashes stripped,
 * scheme lowercased, everything else rejected at provisioning.
 *
 *   cc main/api_base.c tests/test_api_base.c -o /tmp/tab && /tmp/tab
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../main/api_base.h"

static const char *norm(const char *raw)
{
    static char out[FP_API_BASE_MAX];
    memset(out, 0, sizeof(out));
    return fp_api_base_normalize(raw, out, sizeof(out)) == 0 ? out : NULL;
}

int main(void)
{
    /* the TRMNL-style happy path: a Pi on the LAN, no domain, no cert */
    assert(!strcmp(norm("http://192.168.0.2:2300"),
                   "http://192.168.0.2:2300"));
    assert(!strcmp(norm("https://api.example.com"),
                   "https://api.example.com"));

    /* normalization, not footgun documentation */
    assert(!strcmp(norm("https://api.example.com/"),
                   "https://api.example.com"));
    assert(!strcmp(norm("https://api.example.com///"),
                   "https://api.example.com"));
    assert(!strcmp(norm("  http://10.0.0.5:8000  "),
                   "http://10.0.0.5:8000"));
    assert(!strcmp(norm("HTTPS://Api.Example.com"),
                   "https://Api.Example.com"));   /* scheme only is cased */
    assert(!strcmp(norm("https://example.com/flightportrait/"),
                   "https://example.com/flightportrait")); /* subpath is fine */

    /* blank = restore the compiled-in default (no separate reset action) */
    assert(!strcmp(norm(""), ""));
    assert(!strcmp(norm("   "), ""));

    /* rejected, never stored */
    assert(norm("ftp://example.com") == NULL);
    assert(norm("example.com") == NULL);          /* schemeless */
    assert(norm("http://") == NULL);              /* scheme, no host */
    assert(norm("http:///") == NULL);
    assert(norm("https:example.com") == NULL);
    {   /* longer than the NVS cap */
        char long_url[200] = "http://";
        memset(long_url + 7, 'a', 150);
        long_url[157] = 0;
        assert(norm(long_url) == NULL);
    }
    {   /* exactly at the cap boundary: 127 chars fits, 128 does not */
        char url[200] = "http://";
        memset(url + 7, 'a', 120);
        url[127] = 0;
        assert(norm(url) != NULL);
        memset(url + 7, 'a', 121);
        url[128] = 0;
        assert(norm(url) == NULL);
    }

    printf("api_base: all cases pass\n");
    return 0;
}
