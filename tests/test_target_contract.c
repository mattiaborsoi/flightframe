/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* Host tests for the versioned target blob and its power-cut barrier.
 *
 *   cc -Wall -Wextra -Werror main/target_contract.c \
 *      tests/test_target_contract.c -o /tmp/ttarget && /tmp/ttarget
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "../main/target_contract.h"

typedef struct {
    uint8_t phase;
    uint8_t cleanup_state;
    uint8_t blob[FP_TARGET_BLOB_MAX];
    size_t blob_len;
    bool scoped_credentials_present;
} model_t;

typedef struct {
    uint8_t reset_cleanup_state;
    bool wifi_credentials_present;
    bool app_state_present;
} reset_model_t;

static uint8_t effective_phase(const model_t *m)
{
    return m->phase == FP_TARGET_PHASE_READY && m->cleanup_state == 1
        ? FP_TARGET_PHASE_NEEDS_RESUBMIT : m->phase;
}

static void assert_target(const model_t *m, const char *url,
                          const char *secret)
{
    char actual_url[FP_TARGET_URL_BYTES_MAX + 1];
    char actual_secret[FP_TARGET_SECRET_BYTES_MAX + 1];
    assert(fp_target_blob_decode(m->blob, m->blob_len,
                                 actual_url, sizeof(actual_url),
                                 actual_secret, sizeof(actual_secret)));
    assert(strcmp(actual_url, url) == 0);
    assert(strcmp(actual_secret, secret) == 0);
}

static model_t after_cut(unsigned completed_steps)
{
    model_t m = {0};
    assert(fp_target_blob_encode("https://old.example", "old-secret",
                                 m.blob, sizeof(m.blob), &m.blob_len));
    m.scoped_credentials_present = true;
    if (completed_steps == 0) {
        return m;
    }

    /* Each numbered step represents a checked nvs_commit() boundary. A
     * commit failure returns an error and is equivalent to the prior cut. */
    m.phase = FP_TARGET_PHASE_NEEDS_RESUBMIT;
    if (completed_steps == 1) {
        return m;
    }
    assert(fp_target_blob_encode("https://new.example", "new-secret",
                                 m.blob, sizeof(m.blob), &m.blob_len));
    if (completed_steps == 2) {
        return m;
    }
    m.scoped_credentials_present = false;
    if (completed_steps == 3) {
        return m;
    }
    m.phase = FP_TARGET_PHASE_SETUP_REQUIRED;
    return m;
}

static model_t departure_after_cut(unsigned completed_steps)
{
    model_t m = {0};
    assert(fp_target_blob_encode("", "", m.blob, sizeof(m.blob),
                                 &m.blob_len));
    m.scoped_credentials_present = true;
    if (completed_steps == 0) {
        return m;
    }
    m.cleanup_state = 1; /* persisted before target mutation */
    if (completed_steps == 1) {
        return m;
    }
    m.phase = FP_TARGET_PHASE_NEEDS_RESUBMIT;
    if (completed_steps == 2) {
        return m;
    }
    assert(fp_target_blob_encode("https://byos.example", "byos-secret",
                                 m.blob, sizeof(m.blob), &m.blob_len));
    if (completed_steps == 3) {
        return m;
    }
    m.scoped_credentials_present = false;
    if (completed_steps == 4) {
        return m;
    }
    m.cleanup_state = 2;
    if (completed_steps == 5) {
        return m;
    }
    m.phase = FP_TARGET_PHASE_SETUP_REQUIRED;
    return m;
}

static model_t return_to_official_after_cut(unsigned completed_steps)
{
    model_t m = {0};
    m.cleanup_state = 2; /* a prior official unlink is still pending */
    assert(fp_target_blob_encode("https://byos.example", "byos-secret",
                                 m.blob, sizeof(m.blob), &m.blob_len));
    m.scoped_credentials_present = true;
    if (completed_steps == 0) {
        return m;
    }
    /* Critically, state 2 is not cleared before this barrier. */
    m.phase = FP_TARGET_PHASE_NEEDS_RESUBMIT;
    if (completed_steps == 1) {
        return m;
    }
    assert(fp_target_blob_encode("", "", m.blob, sizeof(m.blob),
                                 &m.blob_len));
    if (completed_steps == 2) {
        return m;
    }
    m.scoped_credentials_present = false;
    if (completed_steps == 3) {
        return m;
    }
    m.phase = FP_TARGET_PHASE_SETUP_REQUIRED;
    return m;
}

static reset_model_t factory_reset_after_cut(unsigned completed_steps,
                                             bool cloud_required)
{
    reset_model_t m = {
        .wifi_credentials_present = true,
        .app_state_present = true,
    };
    if (completed_steps == 0) {
        return m;
    }
    /* The protected erase bit precedes every destructive local step. */
    m.reset_cleanup_state = cloud_required
        ? FP_RESET_CLEANUP_ERASE_AND_CLOUD_PENDING
        : FP_RESET_CLEANUP_ERASE_PENDING;
    if (completed_steps == 1) {
        return m;
    }
    m.wifi_credentials_present = false;
    if (completed_steps == 2) {
        return m;
    }
    m.app_state_present = false;
    if (completed_steps == 3) {
        return m;
    }
    m.reset_cleanup_state = cloud_required
        ? FP_RESET_CLEANUP_CLOUD_PENDING
        : FP_RESET_CLEANUP_NONE;
    return m;
}

static reset_model_t resume_factory_reset(reset_model_t m)
{
    if (fp_reset_cleanup_resume_erase(m.reset_cleanup_state)) {
        bool cloud_required =
            fp_reset_cleanup_cloud_required(m.reset_cleanup_state);
        m.wifi_credentials_present = false;
        m.app_state_present = false;
        m.reset_cleanup_state = cloud_required
            ? FP_RESET_CLEANUP_CLOUD_PENDING
            : FP_RESET_CLEANUP_NONE;
    }
    return m;
}

int main(void)
{
    uint8_t blob[FP_TARGET_BLOB_MAX];
    size_t blob_len = 0;
    char url[FP_TARGET_URL_BYTES_MAX + 1];
    char secret[FP_TARGET_SECRET_BYTES_MAX + 1];
    assert(fp_target_blob_encode("https://host/base", "p\xc3\xa4ss",
                                 blob, sizeof(blob), &blob_len));
    assert(fp_target_blob_decode(blob, blob_len, url, sizeof(url),
                                 secret, sizeof(secret)));
    assert(strcmp(url, "https://host/base") == 0);
    assert(strcmp(secret, "p\xc3\xa4ss") == 0);

    /* Strict UTF-8 and decoded NUL rejection. */
    assert(!fp_utf8_valid_no_nul((const uint8_t *)"\xc0\x80", 2));
    assert(!fp_utf8_valid_no_nul((const uint8_t *)"\xed\xa0\x80", 3));
    {
        const uint8_t value[] = {'a', 0, 'b'};
        assert(!fp_utf8_valid_no_nul(value, sizeof(value)));
    }
    {
        static const char nul_json[] =
            "{\"setup_secret\":\"a\\u0000b\"}";
        static const char text_json[] =
            "{\"setup_secret\":\"a\\\\u0000b\"}";
        assert(fp_json_contains_nul_escape(
            (const uint8_t *)nul_json, sizeof(nul_json) - 1));
        assert(!fp_json_contains_nul_escape(
            (const uint8_t *)text_json, sizeof(text_json) - 1));
    }

    /* At every power boundary the blob is old+old or new+new. Once target
     * mutation starts, the barrier denies bearer use until setup completes. */
    for (unsigned cut = 0; cut <= 4; ++cut) {
        model_t m = after_cut(cut);
        if (cut < 2) {
            assert_target(&m, "https://old.example", "old-secret");
        } else {
            assert_target(&m, "https://new.example", "new-secret");
        }
        if (cut == 0) {
            assert(fp_target_token_allowed(m.phase));
            assert(m.scoped_credentials_present);
        } else {
            assert(!fp_target_token_allowed(m.phase));
        }
        if (cut >= 3) {
            assert(!m.scoped_credentials_present);
        }
    }
    assert(!fp_target_token_allowed(99));

    for (unsigned cut = 0; cut <= 6; ++cut) {
        model_t m = departure_after_cut(cut);
        if (cut < 3) {
            assert_target(&m, "", "");
        } else {
            assert_target(&m, "https://byos.example", "byos-secret");
        }
        if (cut == 0) {
            assert(fp_target_token_allowed(effective_phase(&m)));
        } else {
            assert(!fp_target_token_allowed(effective_phase(&m)));
        }
    }
    for (unsigned cut = 0; cut <= 4; ++cut) {
        model_t m = return_to_official_after_cut(cut);
        assert(m.cleanup_state == 2);
        /* If the phone re-submits BYOS after any interrupted return, old
         * official-cloud provenance is still available for cleanup/retry. */
        assert(fp_departure_cleanup_should_run(
            m.cleanup_state, false));
    }

    assert(fp_departure_cleanup_should_stage(true, false, true));
    assert(!fp_departure_cleanup_should_stage(true, false, false));
    assert(!fp_departure_cleanup_should_stage(false, false, true));
    assert(!fp_departure_cleanup_should_stage(false, true, true));
    assert(fp_departure_cleanup_should_run(2, false));
    assert(!fp_departure_cleanup_should_run(1, false));
    assert(!fp_departure_cleanup_should_run(2, true));
    assert(fp_departure_cleanup_cancel_safe(
        1, true, true, false, FP_TARGET_PHASE_READY));
    assert(!fp_departure_cleanup_cancel_safe(
        2, false, true, true, FP_TARGET_PHASE_READY));
    assert(!fp_departure_cleanup_cancel_safe(
        1, true, true, false, FP_TARGET_PHASE_NEEDS_RESUBMIT));

    /* Physical and remote resets enter the same protected journal. Any cut
     * after state 1 resumes the idempotent local erase before provisioning.
     * Fresh BYOS then runs the fixed-host cleanup; fresh official setup clears
     * the state only after its successful acknowledgement. */
    assert(fp_reset_cleanup_cloud_should_stage(true, true, 0));
    assert(!fp_reset_cleanup_cloud_should_stage(true, false, 0));
    assert(!fp_reset_cleanup_cloud_should_stage(false, true, 0));
    assert(fp_reset_cleanup_cloud_should_stage(false, false, 1));
    assert(fp_reset_cleanup_cloud_should_stage(false, false, 2));
    for (unsigned cloud = 0; cloud <= 1; ++cloud) {
        for (unsigned cut = 0; cut <= 4; ++cut) {
            reset_model_t m = factory_reset_after_cut(
                cut, cloud != 0);
            if (cut == 0) {
                assert(!fp_reset_cleanup_resume_erase(
                    m.reset_cleanup_state));
                continue;
            }
            m = resume_factory_reset(m);
            assert(!m.wifi_credentials_present);
            assert(!m.app_state_present);
            assert(m.reset_cleanup_state ==
                   (cloud ? FP_RESET_CLEANUP_CLOUD_PENDING
                          : FP_RESET_CLEANUP_NONE));
            assert(fp_reset_cleanup_should_run(
                       m.reset_cleanup_state, false) ==
                   (cloud != 0));
            assert(!fp_reset_cleanup_should_run(
                m.reset_cleanup_state, true));
        }
    }
    assert(fp_reset_cleanup_resume_erase(
        FP_RESET_CLEANUP_ERASE_AND_CLOUD_PENDING));
    assert(fp_reset_cleanup_cloud_required(
        FP_RESET_CLEANUP_ERASE_AND_CLOUD_PENDING));
    assert(!fp_reset_cleanup_cloud_required(
        FP_RESET_CLEANUP_ERASE_PENDING));
    assert(fp_reset_cleanup_resume_erase(99));
    assert(fp_reset_cleanup_cloud_required(4));
    assert(!fp_reset_cleanup_should_run(
        FP_RESET_CLEANUP_ERASE_PENDING, false));

    /* HTTP cleanup failure retains state 2, but the coherent phase-0 BYOS
     * bearer remains allowed and the next connected wake can retry. */
    model_t cleanup_failed = departure_after_cut(6);
    cleanup_failed.phase = FP_TARGET_PHASE_READY;
    assert(cleanup_failed.cleanup_state == 2);
    assert(fp_target_token_allowed(effective_phase(&cleanup_failed)));

    puts("target_contract: all cases pass");
    return 0;
}
