# Deploying

One DigitalOcean droplet, Cloudflare in front, Caddy terminating TLS.

## 1. DigitalOcean

1. Create a droplet: **Ubuntu 24.04 LTS**, Basic / Regular, **1 vCPU · 2 GB
   ($12/mo)**, region **London (LON1)**, authentication by SSH key.
2. First login, base setup:

       ssh root@<droplet-ip>
       adduser frame && usermod -aG sudo frame
       rsync -a ~/.ssh /home/frame/ && chown -R frame:frame /home/frame/.ssh
       ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
       apt update && apt install -y docker.io docker-compose-v2 git
       usermod -aG docker frame

3. As `frame`, clone and configure:

       git clone https://github.com/mattiaborsoi/flightframe.git ~/flightframe
       cd ~/flightframe
       cp .env.example .env      # set APP_HOSTS and FRAME_DOMAIN to your hostname
       docker compose --profile cloud up -d --build

4. Create the first tenant and login (inside the web container so paths match):

       docker compose exec web python -m flightframe.cli tenant add t1 \
           --name "Your Name" --lat <LAT> --lon <LON> --label "<Place>"
       docker compose exec -it web python -m flightframe.cli user add t1 you@example.com --admin

   Note the printed **provisioning secret** — it binds a frame to this tenant.

## 2. Cloudflare

1. Add your domain to Cloudflare if it is not already there (free plan),
   and point the domain's nameservers at Cloudflare.
2. DNS: add an **A record** `frame` → `<droplet-ip>`, **Proxied** (orange).
3. SSL/TLS → Overview: set mode **Full (strict)**. (Caddy fetches its own
   Let's Encrypt certificate on first request — no cert work needed.)
4. Security → WAF → Rate limiting rules: add one rule,
   `(http.request.uri.path eq "/login")` → 10 requests / 1 minute → Block.
5. **This record runs DNS-only (grey cloud), deliberately.** The zone has
   Bot Fight Mode enabled for another site, and it blocks non-browser user
   agents (error 1010) — including the frames' ESP32 HTTP client. DNS-only
   sends traffic straight to Caddy, whose Let's Encrypt certificate is
   publicly valid on its own. Do not re-enable the proxy on this record
   while Bot Fight Mode is on zone-wide.

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

    ./deploy.sh frame@<droplet-ip>

## Backups

Nightly, on the droplet itself: `tools/backup.sh` runs from
`/etc/cron.d/flightframe-backup` at 03:10 and writes dated archives of
`/root/flightframe/data` (registry, tenant state, history) to

    /root/backups/flightframe/

Retention is capped twice over — 14 days and at most 20 archives — so the
disk cannot fill. Restore = stop the stack, untar over `data/`, start it.

These backups share the droplet's disk by choice; they cover bad deploys and
accidental deletion, not the loss of the droplet. Enable DigitalOcean
Snapshots (~$1.20/mo) if off-machine copies are ever wanted.
