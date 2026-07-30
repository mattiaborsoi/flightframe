idf_component_register(
    SRCS "app_main.c" "api_base.c" "backoff.c" "state_machine.c"
         "wifi.c" "api_client.c" "panel.c" "panel_guard.c" "epd13in3e.c" "ota.c"
         "pairing_contract.c" "identity.c" "buttons.c" "qr_display.c"
         "provisioning.c" "provisioning_contract.c" "target_contract.c"
         "errlog.c" "errlog_contract.c"
    INCLUDE_DIRS "."
    REQUIRES nvs_flash esp_wifi esp_netif esp_event esp_http_client
             esp-tls mbedtls json app_update esp_partition
             driver esp_timer wifi_provisioning protocomm bt)
