# Deploying

One DigitalOcean droplet, Caddy terminating TLS, Cloudflare doing DNS
and nothing else.

## 1. DigitalOcean

1. Create a droplet: **Ubuntu 24.04 LTS**, Basic / Regular, **1 vCPU · 2 GB
   ($12/mo)**, region **London (LON1)**, authentication by SSH key.
2. First login, base setup:

       ssh root@<droplet-ip>
       ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
       apt update && apt install -y docker.io docker-compose-v2 git

   Everything below runs as `root` out of `/root/flightframe`. That is a
   deliberate simplicity for a single-purpose box with nothing else on it,
   and the backup paths at the bottom of this file assume it.

3. Clone and configure:

       git clone https://github.com/mattiaborsoi/flightframe.git ~/flightframe
       cd ~/flightframe
       cp .env.example .env      # set APP_HOSTS and FRAME_DOMAIN to your hostname
       docker compose --profile cloud up -d --build

   The `cloud` profile is what adds Caddy in front; without it the web
   container would be unreachable from outside.

4. Create the first tenant and login (inside the web container so paths match):

       docker compose exec web python -m flightframe.cli tenant add t1 \
           --name "Your Name" --lat <LAT> --lon <LON> --label "<Place>"
       docker compose exec -it web python -m flightframe.cli user add t1 you@example.com --admin

   Note the printed **provisioning secret** — it binds a frame to this tenant.

## 2. Cloudflare (DNS only)

Cloudflare here is a nameserver and nothing more. Read this whole section
before touching anything: the obvious settings are the wrong ones.

1. Add your domain to Cloudflare if it is not already there (free plan),
   and point the domain's nameservers at Cloudflare.
2. DNS: add an **A record** `frame` → `<droplet-ip>`, **DNS only** (grey
   cloud, *not* orange).

   The proxy must stay off. Bot Fight Mode blocks non-browser user agents
   with error 1010, and the frames are an ESP32 HTTP client — so proxying
   this record takes every frame offline in a way that looks exactly like
   a server fault. Grey cloud sends traffic straight to Caddy, whose Let's
   Encrypt certificate is publicly valid on its own, which is the only
   certificate the frame firmware will accept anyway.

3. **Change no zone-wide setting.** In particular leave SSL/TLS on
   whatever the zone already uses. The setting is shared by every site on
   the domain, so "hardening" it here can break something else entirely —
   and with this record on DNS only, Cloudflare is not in the TLS path for
   the frame at all, so there is nothing to gain by changing it.

4. Do not bother with a Cloudflare rate-limiting rule on `/login`: traffic
   never reaches Cloudflare. The application limits logins itself, to ten
   attempts per five minutes per address (`flightframe/auth.py`).

## 3. Point a frame at the cloud

From a laptop next to the frame (press a side button if it is asleep so the
provisioning QR appears; the values below come from that QR):

    .venv/bin/python tools/provision/provision.py \
        --name PROV_XXXXXX --username <from-qr> --pop <from-qr> \
        --url https://frame.example.com \
        --secret <tenant provisioning secret> \
        --ssid <your-wifi>

The frame re-registers against the cloud on its next wake. Verify with:

    docker compose logs -f web     # expect "frame setup: token issued"

## 4. Updates

From your laptop, after pushing to GitHub:

    ./deploy.sh root@<droplet-ip>

## Backups

Nightly, on the droplet itself: `tools/backup.sh` runs from
`/etc/cron.d/flightframe-backup` at 03:10 and writes dated archives of
`/root/flightframe/data` (the registry and each tenant's state) to

    /root/backups/flightframe/

Retention is capped twice over — 14 days and at most 20 archives — so the
disk cannot fill. Restore = stop the stack, untar over `data/`, start it.

These backups share the droplet's disk by choice; they cover bad deploys and
accidental deletion, not the loss of the droplet. Enable DigitalOcean
Snapshots (~$1.20/mo) if off-machine copies are ever wanted.
