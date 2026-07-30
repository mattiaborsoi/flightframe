/* SPDX-FileCopyrightText: 2026 YODE PTE LTD
 * SPDX-License-Identifier: Apache-2.0 */
/* FlightPortrait NVS schema — the COMPLETE list of what a frame remembers.
 * Canonical doc: docs/PROTOCOL.md §4. No account or location data ever lives
 * on the device.  Production builds use encrypted NVS.
 */
#pragma once

#define FP_NVS_NAMESPACE   "flightportrait"
#define FP_FACTORY_NVS_NAMESPACE "fp_factory" /* NVS names are max 15 chars */

/* Protected factory namespace. It is provisioned by the factory jig and is
 * NEVER erased by an app factory reset. Production units carry a unique
 * credential; the only later mutation is the minimal reset-cleanup journal. */
#define FP_FACTORY_SETUP_CREDENTIAL "setup_cred" /* str, high-entropy */
#define FP_FACTORY_RESET_CLEANUP "reset_clean" /* u8 bitset
                                                * fp_reset_cleanup_state_t;
                                                * survives app-state erase */

/* WiFi credentials (written during BLE provisioning) */
#define FP_NVS_WIFI_SSID   "wifi_ssid"     /* str  */
#define FP_NVS_WIFI_PASS   "wifi_pass"     /* str  */
#define FP_NVS_WIFI_BSSID  "wifi_bssid"    /* blob 6 — fast-connect hint  */
#define FP_NVS_WIFI_CHAN   "wifi_chan"     /* u8   — fast-connect hint    */

/* Device identity / protocol state */
#define FP_NVS_DEVICE_TOKEN "dev_token"    /* str 64 hex — bearer token   */
#define FP_NVS_SETUP_REQUIRED "setup_req"  /* legacy u8 setup barrier;
                                            * read for upgrade migration */
#define FP_NVS_TARGET_PHASE   "target_phase" /* u8 fp_target_phase_t:
                                              * 1 is a fail-closed mutation
                                              * journal, 2 needs setup */
#define FP_NVS_TARGET_BLOB    "target_v1"  /* versioned URL+secret blob    */
#define FP_NVS_DEPART_CLEANUP "depart_clean" /* u8: 1 intent precedes target
                                               * barrier; 2 coherent BYOS
                                               * target, old-cloud cleanup
                                               * remains best-effort pending */
#define FP_NVS_IMAGE_HASH   "image_hash"   /* str "sha256:<hex>" — skip   */
#define FP_NVS_API_BASE     "api_base"     /* legacy str ≤127 — migrated
                                            * into target_v1 on the next
                                            * provisioning target write.
                                            * Hand-set BYOS
                                            * server override; absent or
                                            * empty = compiled-in default.
                                            * Written by provisioning
                                            * flows ONLY, never by the
                                            * server (PROTOCOL.md §5). Factory
                                            * reset erases it.           */
#define FP_NVS_BYOS_SETUP    "byos_setup"   /* legacy encrypted str;
                                             * migrated with api_base     */

/* Public server reference and possession-pairing state. */
#define FP_NVS_DEVICE_REF    "device_ref"   /* str, opaque public ref      */
#define FP_NVS_PAIR_COUNTER  "pair_ctr"     /* u32, monotonic per key      */
#define FP_NVS_PROV_STATE    "prov_state"   /* u8 fp_prov_state_t          */
#define FP_NVS_PAIR_NONCE    "pair_nonce"   /* str 64 lowercase hex;
                                             * transient, encrypted NVS   */
#define FP_NVS_PAIR_HASH     "pair_hash"    /* str 64 lowercase hex        */
#define FP_NVS_PAIR_EXPIRES  "pair_exp"     /* RFC3339 UTC                 */

/* Runtime-generated P-256 possession-signing identity.  Factory reset erases
 * both values; the next setup generates a new key and counter starts at 1. */
#define FP_NVS_PAIR_PRIVATE  "pair_priv"    /* blob 32-byte scalar         */
#define FP_NVS_PAIR_PUBLIC   "pair_pub"     /* blob 65-byte SEC1 point     */

/* Runtime-generated Security-2 credentials.  Password is required to redraw
 * the on-glass QR after a power interruption, so it is retained only in
 * encrypted production NVS and never logged. */
#define FP_NVS_SEC2_USERNAME "sec2_user"    /* str                         */
#define FP_NVS_SEC2_PASSWORD "sec2_pass"    /* str                         */
#define FP_NVS_SEC2_SALT     "sec2_salt"    /* blob, 16 bytes              */
#define FP_NVS_SEC2_VERIFIER "sec2_ver"     /* blob, SRP-3072 verifier     */
#define FP_NVS_PROV_QR_DRAWN "prov_qr"      /* u8, QR currently on glass   */
#define FP_NVS_PAIR_QR_DRAWN "pair_qr"       /* u8, re-pair QR on glass      */

/* Failure handling */
#define FP_NVS_BACKOFF_N    "backoff_n"    /* u8 — consecutive failures   */

/* Counters / diagnostics */
#define FP_NVS_BOOT_COUNT   "boot_count"   /* u32 */
#define FP_NVS_ERR_RING     "err_ring"     /* blob — small error ring buf */
#define FP_NVS_ERR_RING_POS "err_pos"      /* u8   */

/* Lifecycle */
#define FP_NVS_SHIPPING     "shipping"     /* u8 1 = shipping mode: deepest
                                            * sleep, only USB-power wake;
                                            * 2 = prep complete, field
                                            * image must arm state 1      */

#define FP_SHIPPING_NONE                0u
#define FP_SHIPPING_CUSTOMER_ARMED      1u
#define FP_SHIPPING_FIELD_FLASH_PENDING 2u
