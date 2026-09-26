/* SPDX-FileCopyrightText: 2026 flightframe
 * SPDX-License-Identifier: Apache-2.0 */
#include "portal.h"

#include <string.h>

#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "nvs.h"
#include "sdkconfig.h"

#include "api_client.h"
#include "nvs_schema.h"
#include "identity.h"
#include "provisioning.h"
#include "qr_display.h"
#include "panel.h"
#include "wifi.h"

static const char *TAG = "portal";

#define PORTAL_TIMEOUT_S (15 * 60)   /* then sleep; button wake reopens */
#define AP_IP "192.168.4.1"
#define SCAN_MAX 12

static EventGroupHandle_t s_events;
#define EVT_SAVED  BIT0

static wifi_ap_record_t s_scan[SCAN_MAX];
static uint16_t s_scan_n;

/* -------------------------------------------------------------- DNS hijack */

/* Minimal DNS responder: every A query resolves to the portal address, which
 * is what makes phones pop the "sign in to network" sheet. */
static void dns_task(void *arg)
{
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(53),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (sock < 0 || bind(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        ESP_LOGE(TAG, "dns bind failed");
        vTaskDelete(NULL);
        return;
    }
    uint8_t buf[256];
    uint32_t portal_ip = inet_addr(AP_IP);
    while (true) {
        struct sockaddr_in from;
        socklen_t from_len = sizeof(from);
        int len = recvfrom(sock, buf, sizeof(buf) - 16, 0,
                           (struct sockaddr *)&from, &from_len);
        if (len < 12) {
            continue;
        }
        buf[2] = 0x81;                    /* response, recursion available */
        buf[3] = 0x80;
        buf[6] = 0; buf[7] = 1;           /* one answer */
        buf[8] = buf[9] = buf[10] = buf[11] = 0;
        /* answer: pointer to the query name, A/IN, TTL 60, the portal IP */
        uint8_t answer[] = { 0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 60, 0, 4 };
        memcpy(buf + len, answer, sizeof(answer));
        memcpy(buf + len + sizeof(answer), &portal_ip, 4);
        sendto(sock, buf, len + sizeof(answer) + 4, 0,
               (struct sockaddr *)&from, from_len);
    }
}

/* ------------------------------------------------------------------- pages */

static const char PAGE_HEAD[] =
    "<!doctype html><meta charset=utf-8>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>Frame</title><style>"
    "body{font:17px/1.5 -apple-system,system-ui,sans-serif;margin:0;"
    "background:#e8e6df;color:#1a1a1a;display:flex;justify-content:center}"
    ".card{max-width:420px;padding:28px 22px}"
    "h1{font-size:24px;margin:0 0 4px} .sub{color:#3a5a8c;margin:0 0 22px}"
    "select,input,button{width:100%;box-sizing:border-box;font-size:17px;"
    "padding:12px;margin:6px 0 14px;border:1px solid #999;border-radius:8px}"
    "button{background:#3a5a8c;color:#fff;border:0;font-weight:600}"
    "label{font-size:14px;color:#3a5a8c}"
    "details{margin-top:10px;font-size:14px}</style><div class=card>";

static esp_err_t root_get(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_send_chunk(req, PAGE_HEAD, HTTPD_RESP_USE_STRLEN);
    httpd_resp_send_chunk(req,
        "<h1>Cornice / Frame</h1>"
        "<p class=sub>Collega la cornice al Wi-Fi di casa &middot; "
        "Connect the frame to your home Wi-Fi</p>"
        "<form method=POST action=/save>"
        "<label>Rete / Network</label><select name=ssid>",
        HTTPD_RESP_USE_STRLEN);
    for (int i = 0; i < s_scan_n; i++) {
        char row[96], ssid[33];
        strlcpy(ssid, (const char *)s_scan[i].ssid, sizeof(ssid));
        if (!ssid[0]) {
            continue;
        }
        snprintf(row, sizeof(row), "<option>%s</option>", ssid);
        httpd_resp_send_chunk(req, row, HTTPD_RESP_USE_STRLEN);
    }
    httpd_resp_send_chunk(req,
        "</select>"
        "<label>Password Wi-Fi</label>"
        "<input name=pass type=password autocomplete=off>"
        "<button>Salva / Save</button>"
        "<details><summary>Avanzate / Advanced</summary>"
        "<label>Server</label><input name=server value='" CONFIG_FP_API_BASE
        "'><label>Codice / Secret</label>"
        "<input name=secret value='" CONFIG_FP_DEV_PROVISION_SECRET "'>"
        "</details></form>",
        HTTPD_RESP_USE_STRLEN);
    return httpd_resp_send_chunk(req, NULL, 0);
}

static int form_field(const char *body, const char *key, char *out,
                      size_t cap)
{
    /* x-www-form-urlencoded, tiny fields, plus-and-percent decoding. */
    size_t klen = strlen(key);
    const char *p = body;
    out[0] = 0;
    while (p && *p) {
        if (!strncmp(p, key, klen) && p[klen] == '=') {
            const char *v = p + klen + 1;
            size_t o = 0;
            while (*v && *v != '&' && o + 1 < cap) {
                if (*v == '+') {
                    out[o++] = ' '; v++;
                } else if (*v == '%' && v[1] && v[2]) {
                    char hex[3] = { v[1], v[2], 0 };
                    out[o++] = (char)strtol(hex, NULL, 16); v += 3;
                } else {
                    out[o++] = *v++;
                }
            }
            out[o] = 0;
            return 0;
        }
        p = strchr(p, '&');
        if (p) {
            p++;
        }
    }
    return -1;
}

static esp_err_t save_post(httpd_req_t *req)
{
    char body[512];
    int len = httpd_req_recv(req, body, sizeof(body) - 1);
    if (len <= 0) {
        return httpd_resp_send_500(req);
    }
    body[len] = 0;

    char ssid[33], pass[65], server[160], secret[80];
    if (form_field(body, "ssid", ssid, sizeof(ssid)) != 0 || !ssid[0]) {
        return httpd_resp_send_500(req);
    }
    form_field(body, "pass", pass, sizeof(pass));
    if (form_field(body, "server", server, sizeof(server)) != 0 ||
        !server[0]) {
        strlcpy(server, CONFIG_FP_API_BASE, sizeof(server));
    }
    if (form_field(body, "secret", secret, sizeof(secret)) != 0 ||
        !secret[0]) {
        strlcpy(secret, CONFIG_FP_DEV_PROVISION_SECRET, sizeof(secret));
    }

    wifi_sta_config_t sta = { 0 };
    strlcpy((char *)sta.ssid, ssid, sizeof(sta.ssid));
    strlcpy((char *)sta.password, pass, sizeof(sta.password));
    esp_err_t err = fp_wifi_store_credentials(&sta);
    /* The target store refuses a secret paired with the compiled-default
     * URL (upstream treats that pairing as an error) — and with compiled
     * defaults there is nothing to store anyway: cloud_setup falls back
     * to the built-in secret. Store a target only when actually custom. */
    if (err == ESP_OK && strcmp(server, CONFIG_FP_API_BASE) != 0) {
        err = fp_api_provisioning_target_set(server, secret);
    }
    memset(pass, 0, sizeof(pass));
    memset(secret, 0, sizeof(secret));
    memset(&sta, 0, sizeof(sta));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "save failed: %s", esp_err_to_name(err));
        return httpd_resp_send_500(req);
    }

    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_send_chunk(req, PAGE_HEAD, HTTPD_RESP_USE_STRLEN);
    httpd_resp_send_chunk(req,
        "<h1>Fatto! / Done!</h1><p class=sub>La cornice si riavvia e si "
        "collega da sola. Puoi chiudere questa pagina.<br>"
        "The frame restarts and connects by itself. You can close this "
        "page.</p>", HTTPD_RESP_USE_STRLEN);
    httpd_resp_send_chunk(req, NULL, 0);
    ESP_LOGI(TAG, "credentials saved, restarting");
    xEventGroupSetBits(s_events, EVT_SAVED);
    return ESP_OK;
}

static esp_err_t redirect_get(httpd_req_t *req)
{
    /* Captive-portal probes (Android generate_204, Apple hotspot-detect,
     * Windows connecttest) all land here and get bounced to the form. */
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://" AP_IP "/");
    return httpd_resp_send(req, NULL, 0);
}

/* --------------------------------------------------------------- lifecycle */

static esp_err_t run_portal_ui(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    esp_err_t loop = esp_event_loop_create_default();
    if (loop != ESP_OK && loop != ESP_ERR_INVALID_STATE) {
        return loop;
    }

    /* Scan from STA first: an AP-mode scan misses 2.4 GHz channels. */
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    wifi_scan_config_t scan = { .show_hidden = false };
    if (esp_wifi_scan_start(&scan, true) == ESP_OK) {
        s_scan_n = SCAN_MAX;
        esp_wifi_scan_get_ap_records(&s_scan_n, s_scan);
    }
    ESP_ERROR_CHECK(esp_wifi_stop());

    /* Open hotspot, named per-unit so two frames on one table don't clash. */
    esp_netif_create_default_wifi_ap();
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    wifi_config_t ap = { 0 };
    snprintf((char *)ap.ap.ssid, sizeof(ap.ap.ssid),
             CONFIG_FP_PORTAL_AP_SSID "-%02X%02X", mac[4], mac[5]);
    ap.ap.ssid_len = strlen((char *)ap.ap.ssid);
    ap.ap.max_connection = 2;
    ap.ap.authmode = WIFI_AUTH_OPEN;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap));
    ESP_ERROR_CHECK(esp_wifi_start());

    /* The QR joins the phone to that hotspot; the portal then self-opens. */
    char qr[96];
    snprintf(qr, sizeof(qr), "WIFI:T:nopass;S:%s;;", (char *)ap.ap.ssid);
    nvs_handle_t nvs;
    if (nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs) == ESP_OK) {
        uint8_t drawn = 0;
        nvs_get_u8(nvs, FP_NVS_PROV_QR_DRAWN, &drawn);
        if (!drawn && fp_panel_wait_seconds() == 0) {
            if (fp_qr_invalidate_cached_poster() == ESP_OK &&
                fp_qr_display(qr) == ESP_OK) {
                nvs_set_u8(nvs, FP_NVS_PROV_QR_DRAWN, 1);
                nvs_commit(nvs);
            }
        }
        nvs_close(nvs);
    }

    s_events = xEventGroupCreate();
    xTaskCreate(dns_task, "portal_dns", 3072, NULL, 5, NULL);

    httpd_handle_t server = NULL;
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.max_uri_handlers = 4;
    cfg.uri_match_fn = httpd_uri_match_wildcard;
    ESP_ERROR_CHECK(httpd_start(&server, &cfg));
    httpd_uri_t save = { .uri = "/save", .method = HTTP_POST,
                         .handler = save_post };
    httpd_uri_t root = { .uri = "/", .method = HTTP_GET,
                         .handler = root_get };
    httpd_uri_t any = { .uri = "/*", .method = HTTP_GET,
                        .handler = redirect_get };
    httpd_register_uri_handler(server, &save);
    httpd_register_uri_handler(server, &root);
    httpd_register_uri_handler(server, &any);

    ESP_LOGI(TAG, "portal up: join \"%s\", form at http://%s/",
             (char *)ap.ap.ssid, AP_IP);
    EventBits_t bits = xEventGroupWaitBits(
        s_events, EVT_SAVED, pdFALSE, pdFALSE,
        pdMS_TO_TICKS(PORTAL_TIMEOUT_S * 1000));
    if (bits & EVT_SAVED) {
        vTaskDelay(pdMS_TO_TICKS(1500));   /* let the reply flush */
        esp_restart();
    }
    ESP_LOGI(TAG, "portal timed out");
    return ESP_ERR_TIMEOUT;               /* caller sleeps; button reopens */
}

static esp_err_t cloud_setup(void)
{
    esp_err_t err = fp_wifi_platform_init();
    if (err != ESP_OK) {
        return err;
    }
    err = fp_wifi_connect(20000);
    if (err != ESP_OK) {
        return err;
    }
    fp_wifi_sync_time();

    char secret[80];
    err = fp_api_byos_setup_get(secret, sizeof(secret));
    if (err != ESP_OK) {
        /* No stored target: compiled defaults are the deployment. */
        strlcpy(secret, CONFIG_FP_DEV_PROVISION_SECRET, sizeof(secret));
        if (!secret[0]) {
            return ESP_ERR_NOT_FOUND;
        }
    }
    fp_setup_result_t result;
    memset(&result, 0, sizeof(result));
    err = fp_api_setup(secret, NULL, &result);
    memset(secret, 0, sizeof(secret));
    memset(&result, 0, sizeof(result));
    if (err != ESP_OK) {
        return err;
    }
    /* fp_pairing_complete() is the BLE flow's lifecycle bookkeeping and
     * returns INVALID_STATE when no BLE pairing ever started — which is
     * every portal provisioning. The credential is already stored; what
     * matters is marking provisioning complete so the next boot polls
     * instead of registering again. */
    /* Completion, written plainly. The BLE flow's completion helpers sit
     * behind guards tuned for that flow's states; three portal attempts
     * hit three different guards. What completion MEANS is three NVS
     * facts — provisioning complete, setup no longer required, bearer
     * usable — so write exactly those, the same values mark_complete's
     * own tail writes, without the preambles. */
    (void)fp_pairing_complete();
    nvs_handle_t done = 0;
    err = nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &done);
    if (err == ESP_OK) {
        err = nvs_set_u8(done, FP_NVS_PROV_STATE,
                         (uint8_t)FP_PROV_STATE_COMPLETE);
    }
    if (err == ESP_OK) {
        err = nvs_set_u8(done, FP_NVS_SETUP_REQUIRED, 0);
    }
    if (err == ESP_OK) {
        err = nvs_set_u8(done, FP_NVS_TARGET_PHASE,
                         FP_TARGET_PHASE_READY);
    }
    if (err == ESP_OK) {
        err = nvs_commit(done);
    }
    if (done != 0) {
        nvs_close(done);
    }
    ESP_LOGI(TAG, "completion writes: %s (state %d, phase %d)",
             esp_err_to_name(err), fp_prov_state_get(),
             fp_api_target_phase());
    if (err == ESP_OK) {
        nvs_handle_t nvs;
        if (nvs_open(FP_NVS_NAMESPACE, NVS_READWRITE, &nvs) == ESP_OK) {
            nvs_set_u8(nvs, FP_NVS_PROV_QR_DRAWN, 0);
            nvs_commit(nvs);
            nvs_close(nvs);
        }
    }
    return err;
}

esp_err_t fp_portal_provision(void)
{
    wifi_sta_config_t creds;
    bool have_wifi = fp_wifi_load_credentials(&creds) == ESP_OK &&
                     creds.ssid[0];
    memset(&creds, 0, sizeof(creds));
    if (!have_wifi) {
        return run_portal_ui();           /* restarts on success */
    }
    esp_err_t err = cloud_setup();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "setup failed: %s", esp_err_to_name(err));
    }
    return err;
}
