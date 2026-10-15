/* SPDX-FileCopyrightText: 2026 flightframe
 * SPDX-License-Identifier: Apache-2.0 */
#include "frame.h"

#include <string.h>
#include <strings.h>
#include <sys/time.h>

#include "esp_app_desc.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "lwip/netdb.h"
#include "nvs.h"
#include "sdkconfig.h"

#include "api_base.h"
#include "api_client.h"
#include "nvs_schema.h"
#include "pairing_contract.h"
#include "target_contract.h"

static const char *TAG = "frame";

#define NVS_ATTN     "ff_attn"      /* u8 fp_attention_t, pending page   */
#define NVS_LASTFAIL "ff_lastfail"  /* u8 fp_attention_t, last failure   */
#define NVS_FWBAD    "ff_fwbad"     /* str, version rolled back from     */

static bool has_str(nvs_handle_t nvs, const char *key)
{
    size_t len = 0;
    return nvs_get_str(nvs, key, NULL, &len) == ESP_OK && len > 1;
}

static bool has_blob(nvs_handle_t nvs, const char *key)
{
    size_t len = 0;
    return nvs_get_blob(nvs, key, NULL, &len) == ESP_OK && len > 0;
}

static bool clear_u8(nvs_handle_t nvs, const char *key, const char *what)
{
    uint8_t v = 0;
    if (nvs_get_u8(nvs, key, &v) == ESP_OK && v != 0) {
        ESP_LOGW(TAG, "boot repair: cleared %s (%u)", what, v);
        return nvs_erase_key(nvs, key) == ESP_OK;
    }
    return false;
}

void fp_frame_boot_repair(void)
{
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs) != ESP_OK) {
        return;
    }
    if (!has_str(nvs, FP_NVS_DEVICE_TOKEN)) {
        nvs_close(nvs);          /* never registered: normal setup applies */
        return;
    }
    bool dirty = false;

    /* 1. The server. Earlier builds compiled it in as the vendor default and
     * stored nothing; this build compiles a dead address there. Without
     * this the frame would lose its server on upgrade. The legacy key is
     * the original firmware's own read-only upgrade fallback, and writing
     * it directly (not through the target journal) avoids staging a
     * vendor-cloud "departure" that would then be retried every wake. */
    const char *server = CONFIG_FP_DEPLOYMENT_SERVER;
    if (server[0] && !has_blob(nvs, FP_NVS_TARGET_BLOB) &&
        !has_str(nvs, FP_NVS_API_BASE)) {
        if (nvs_set_str(nvs, FP_NVS_API_BASE, server) == ESP_OK) {
            ESP_LOGW(TAG, "boot repair: stored own server %s", server);
            dirty = true;
        }
    }

    /* 2. Provisioning state. A token means the frame is registered. Any
     * other state is left over from a vendor flow (re-pairing, which this
     * deployment cannot complete) or reads as EMPTY when missing, and
     * either one diverts every boot away from polling. */
    uint8_t st = FP_PROV_STATE_EMPTY;
    if (nvs_get_u8(nvs, FP_NVS_PROV_STATE, &st) != ESP_OK ||
        st != FP_PROV_STATE_COMPLETE) {
        ESP_LOGW(TAG, "boot repair: provisioning state %u -> complete", st);
        nvs_set_u8(nvs, FP_NVS_PROV_STATE, FP_PROV_STATE_COMPLETE);
        dirty = true;
    }

    /* 3. Vendor-cloud journals. Never staged legitimately here, and a set
     * departure flag forces the target phase to NEEDS_RESUBMIT, which
     * refuses the bearer token without logging why. */
    dirty |= clear_u8(nvs, FP_NVS_DEPART_CLEANUP, "departure journal");

    /* 4. An interrupted target write that never landed anything. */
    uint8_t phase = FP_TARGET_PHASE_READY;
    if (nvs_get_u8(nvs, FP_NVS_TARGET_PHASE, &phase) == ESP_OK &&
        phase == FP_TARGET_PHASE_NEEDS_RESUBMIT &&
        !has_blob(nvs, FP_NVS_TARGET_BLOB)) {
        ESP_LOGW(TAG, "boot repair: cleared empty target journal");
        nvs_set_u8(nvs, FP_NVS_TARGET_PHASE, FP_TARGET_PHASE_READY);
        dirty = true;
    }

    /* 5. QR-on-glass flags from abandoned flows. */
    dirty |= clear_u8(nvs, FP_NVS_PAIR_QR_DRAWN, "pairing QR flag");

    if (dirty) {
        nvs_commit(nvs);
    }
    nvs_close(nvs);
}

static void set_u8(const char *key, uint8_t v)
{
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs) == ESP_OK) {
        nvs_set_u8(nvs, key, v);
        nvs_commit(nvs);
        nvs_close(nvs);
    }
}

static uint8_t get_u8(const char *key)
{
    uint8_t v = 0;
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs) == ESP_OK) {
        nvs_get_u8(nvs, key, &v);
        nvs_close(nvs);
    }
    return v;
}

void fp_frame_attention_request(fp_attention_t why)
{
    ESP_LOGW(TAG, "setup page requested for next boot (reason %d)", why);
    set_u8(NVS_ATTN, (uint8_t)why);
}

fp_attention_t fp_frame_attention_take(void)
{
    uint8_t why = get_u8(NVS_ATTN);
    if (why != FP_ATTN_NONE) {
        nvs_handle_t nvs;
        if (nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs) == ESP_OK) {
            nvs_erase_key(nvs, NVS_ATTN);
            nvs_commit(nvs);
            nvs_close(nvs);
        }
    }
    return (fp_attention_t)why;
}

void fp_frame_failure_note(fp_attention_t why)
{
    if (get_u8(NVS_LASTFAIL) != (uint8_t)why) {
        set_u8(NVS_LASTFAIL, (uint8_t)why);
    }
}

fp_attention_t fp_frame_failure_last(void)
{
    return (fp_attention_t)get_u8(NVS_LASTFAIL);
}

static void fw_rejected_get(char *out, size_t cap)
{
    out[0] = 0;
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READONLY, &nvs) == ESP_OK) {
        size_t len = cap;
        if (nvs_get_str(nvs, NVS_FWBAD, out, &len) != ESP_OK) {
            out[0] = 0;
        }
        nvs_close(nvs);
    }
}

void fp_frame_ota_note_rollback(void)
{
    const esp_partition_t *bad = esp_ota_get_last_invalid_partition();
    if (!bad) {
        return;
    }
    esp_app_desc_t desc;
    if (esp_ota_get_partition_description(bad, &desc) != ESP_OK) {
        return;
    }
    char current[sizeof(desc.version)];
    fw_rejected_get(current, sizeof(current));
    if (strcmp(current, desc.version) == 0) {
        return;
    }
    ESP_LOGW(TAG, "firmware %s was rolled back; it will not be taken again",
             desc.version);
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs) == ESP_OK) {
        nvs_set_str(nvs, NVS_FWBAD, desc.version);
        nvs_commit(nvs);
        nvs_close(nvs);
    }
}

bool fp_frame_fw_offer_ok(const char *offered_version)
{
    char rejected[32];
    fw_rejected_get(rejected, sizeof(rejected));
    bool ok = fp_fw_offer_acceptable(
        offered_version, esp_app_get_description()->version, rejected);
    if (!ok) {
        ESP_LOGW(TAG, "declining firmware offer %s (running %s, rejected %s)",
                 offered_version ? offered_version : "?",
                 esp_app_get_description()->version,
                 rejected[0] ? rejected : "none");
    }
    return ok;
}

/* The server's host name from the effective API base, without scheme,
 * port or path. */
static bool api_host(char *name, size_t size)
{
    char base[FP_API_BASE_MAX];
    fp_api_base_get(base, sizeof(base));
    const char *host = strstr(base, "://");
    host = host ? host + 3 : base;
    size_t n = strcspn(host, ":/");
    if (n == 0 || n >= size) {
        return false;
    }
    memcpy(name, host, n);
    name[n] = 0;
    return true;
}

bool fp_frame_dns_ok(void)
{
    char name[FP_API_BASE_MAX];
    if (!api_host(name, sizeof(name))) {
        return false;
    }
    struct addrinfo hints = { .ai_family = AF_INET,
                              .ai_socktype = SOCK_STREAM };
    struct addrinfo *res = NULL;
    int rc = getaddrinfo(name, "443", &hints, &res);
    if (res) {
        freeaddrinfo(res);
    }
    if (rc != 0) {
        ESP_LOGW(TAG, "cannot resolve %s (rc %d)", name, rc);
    }
    return rc == 0;
}

static void log_dns(esp_netif_t *sta, const char *when)
{
    esp_netif_dns_info_t main = {0}, backup = {0};
    esp_netif_get_dns_info(sta, ESP_NETIF_DNS_MAIN, &main);
    esp_netif_get_dns_info(sta, ESP_NETIF_DNS_BACKUP, &backup);
    ESP_LOGI(TAG, "dns %s: " IPSTR ", " IPSTR, when,
             IP2STR(&main.ip.u_addr.ip4), IP2STR(&backup.ip.u_addr.ip4));
}

static void set_dns(esp_netif_t *sta, esp_netif_dns_type_t which,
                    uint8_t a, uint8_t b, uint8_t c, uint8_t d)
{
    esp_netif_dns_info_t dns = {0};
    dns.ip.type = ESP_IPADDR_TYPE_V4;
    dns.ip.u_addr.ip4.addr = ESP_IP4TOADDR(a, b, c, d);
    esp_netif_set_dns_info(sta, which, &dns);
}

void fp_frame_dns_heal(void)
{
    esp_netif_t *sta = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if (!sta) {
        return;
    }
    log_dns(sta, "from DHCP");
    if (fp_frame_dns_ok()) {
        return;
    }
    /* The router's resolver is broken or wrong for this device (Oct 2026:
     * the London router handed the frame a resolver that never answered,
     * while every other device on the network was fine). Names are only a
     * route to the server; TLS still proves who answers. Use public
     * resolvers for the rest of this wake; DHCP sets them again next time. */
    set_dns(sta, ESP_NETIF_DNS_MAIN, 1, 1, 1, 1);
    set_dns(sta, ESP_NETIF_DNS_BACKUP, 8, 8, 8, 8);
    log_dns(sta, "fallback");
    ESP_LOGW(TAG, "router DNS failed; public resolvers %s",
             fp_frame_dns_ok() ? "work" : "fail too");
}

static int64_t s_http_date = -1;

static esp_err_t on_date_header(esp_http_client_event_t *e)
{
    int64_t t;
    if (e->event_id == HTTP_EVENT_ON_HEADER && e->header_key &&
        e->header_value && strcasecmp(e->header_key, "Date") == 0 &&
        fp_http_date_parse(e->header_value, &t)) {
        s_http_date = t;
    }
    return ESP_OK;
}

esp_err_t fp_frame_clock_from_http(void)
{
    int64_t floor = 0;
    char name[FP_API_BASE_MAX];
    char url[FP_API_BASE_MAX + 24];
    if (!fp_build_date_parse(esp_app_get_description()->date, &floor) ||
        !api_host(name, sizeof(name))) {
        return ESP_FAIL;
    }
    /* Plain HTTP on purpose: TLS is what the missing clock blocks. Any
     * answer will do, a redirect included; only its Date header is read. */
    snprintf(url, sizeof(url), "http://%s/healthz", name);
    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_HEAD,
        .timeout_ms = 8000,
        .disable_auto_redirect = true,
        .event_handler = on_date_header,
    };
    s_http_date = -1;
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = esp_http_client_perform(client);
    esp_http_client_cleanup(client);
    if (s_http_date < 0) {
        ESP_LOGW(TAG, "no Date header from %s (%s)", name,
                 esp_err_to_name(err));
        return ESP_FAIL;
    }
    if (!fp_clock_plausible(s_http_date, floor)) {
        ESP_LOGW(TAG, "ignoring implausible Date from %s", name);
        return ESP_FAIL;
    }
    struct timeval tv = { .tv_sec = (time_t)s_http_date };
    settimeofday(&tv, NULL);
    ESP_LOGW(TAG, "clock set from %s's Date header", name);
    return ESP_OK;
}
