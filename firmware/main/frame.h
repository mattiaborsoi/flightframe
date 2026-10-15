/* SPDX-FileCopyrightText: 2026 flightframe
 * SPDX-License-Identifier: Apache-2.0 */
/* flightframe additions that touch NVS, the network or OTA state. The pure
 * rules they apply live in frame_policy.h, where host tests cover them. */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "frame_policy.h"

/* Bring a frame registered by any earlier build into a state where it polls.
 * Runs every boot and is idempotent: it only acts on a frame that holds a
 * device token, and only on state this deployment can never legitimately
 * produce (vendor re-pairing, vendor-cloud journals, a missing server). */
void fp_frame_boot_repair(void);

/* Ask for the setup page on the next boot. The page must open on a clean
 * boot: the portal brings Wi-Fi up from scratch and aborts if it is already
 * running, so a failed poll cannot open it mid-wake. */
void fp_frame_attention_request(fp_attention_t why);

/* The pending request, cleared as it is read so a crash on the page cannot
 * loop the frame back into it forever. */
fp_attention_t fp_frame_attention_take(void);

/* What went wrong on the last failed wake, shown on the setup page. */
void fp_frame_failure_note(fp_attention_t why);
fp_attention_t fp_frame_failure_last(void);

/* Record the version of an image the bootloader rolled back from, so the
 * same offer is never taken twice. Call once per boot. */
void fp_frame_ota_note_rollback(void);

/* Whether to take a firmware offer naming this version. */
bool fp_frame_fw_offer_ok(const char *offered_version);

/* Resolve the server's host name. Splits "the network is down" from "the
 * name doesn't resolve" in the failure report: Sunday's outage looked like a
 * generic poll failure for three days. */
bool fp_frame_dns_ok(void);
