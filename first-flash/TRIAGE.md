# If it doesn't draw

One page. Start at the top and stop when the failed checkpoint becomes green.

| Check | What good looks like | If bad |
|---|---|---|
| Power | Switch ON, USB data cable attached, charge LED/status response | Try the supplied cable and another port; wake with Refresh; do not diagnose software below a stable supply. |
| Serial | `/dev/cu.wchusbserial*` or `/dev/cu.usbmodem*`; 115200 logs | Install/enable the CH34x driver if needed. Hold Boot, tap Reset, release Boot. Confirm the cable carries data. |
| PSRAM | No `ps_malloc failed`; 960,000-byte allocation succeeds | Board must be XIAO ESP32-S3 with OPI PSRAM selected. Recompile after changing the board menu. |
| SD presence | Card detect is low and the mount succeeds | Power OFF, reseat the card until it clicks. Use the supplied card first. |
| SD format | FAT32, ≤32 GB for the ritual, readable on the laptop | Reformat FAT32 with one partition. Do not use exFAT. |
| SD file | Root `/flightportrait.bin`, exactly 960,000 bytes, manifest hash green | Re-run `make_sd.py --src ... --volume ...`; safely eject before moving the card. |
| FPC seating | Full image, no blank half, stable colors | Power OFF. Do not hot-seat. Open only if Seeed support/service instructions allow it; inspect both ends for square, fully inserted seating and closed latches. Photograph before touching. |
| SPI/controllers | Geometry border complete; center seam uninterrupted | A blank half points to CS/CS1 or a panel FPC. Recheck GPIO10 master CS, GPIO2 slave CS, shared SPI pins 7/8/9, DC11, RST38, BUSY13, EN12. |
| Palette/order | Six solid bands; nibble test has left red/right blue at top | Wrong colors suggest palette mapping. Reversed one-pixel pairs suggest high/low nibble reversal. Do not compensate in artwork. |
| Refresh | `blit + refresh` then `done` in about 30–40 s | If BUSY never releases, stop power-cycling and inspect BUSY/RST/EN plus FPC seating. Wait ≥180 s before another attempt. |
| WiFi | Dedicated 2.4 GHz SSID joins in ≤15 s | E1004 is 2.4 GHz only. Avoid captive portals and WPA-enterprise. Distinguish wrong password from AP-not-found. |
| Cloud | Setup 200, display 200, 960,000 bytes, SHA-256 match | Check the unit-specific setup credential, DNS/time sync, stable endpoint, and fleet containers. Run the simulator before blaming the unit. |

Never power off mid-refresh, hot-seat an FPC, or erase/reflash repeatedly without
capturing the serial log and the last known-good checkpoint.
