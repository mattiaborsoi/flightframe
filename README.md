# flightframe

A cloud service that draws the sky above your house on a six-colour e-ink
frame. Aircraft positions come from [adsb.lol](https://adsb.lol), routes from
[adsbdb](https://adsbdb.com); the server renders 1200×1600 posters, and a
battery-powered ESP32 frame wakes every few minutes, downloads the latest
one, and goes back to sleep.

Multi-tenant: each household gets an isolated dashboard (location, poster
design, refresh cadence, awake window, timezone, flight tracking) behind an
invite-only login.

## Layout

    flightframe/       server: collector, renderers, web dashboard, device API
    tools/provision/   one-time BLE provisioning of a frame (Wi-Fi + server)
    tools/simulate_frame.py   protocol conformance check, no hardware needed
    caddy/             TLS termination for cloud deployment
    tests/             offline test suite

## Run (development)

    python3 -m venv .venv && .venv/bin/pip install pillow cairosvg bleak
    cp .env.example .env
    .venv/bin/python -m flightframe.cli tenant add t1 --name You \
        --lat 51.5154 --lon -0.1410 --label "Oxford Circus"
    .venv/bin/python -m flightframe.cli user add t1 you@example.com
    .venv/bin/python -m flightframe.cli serve --host 127.0.0.1

Then in two more terminals: `run-collector --loop 60` and
`run-renderer --loop 180`.

## Run (production)

    docker compose --profile cloud up -d --build

See `caddy/Caddyfile` for the hostname, and `deploy.sh` for updates.

## Tests

    .venv/bin/python -m unittest discover tests

Everything runs offline; the suite includes multi-tenant isolation tests
over live HTTP and a device-protocol simulator.

## Licence

Server code © Mattia Borsoi. The BLE provisioning tool vendors Espressif's
`esp_prov` modules under Apache-2.0 (see `tools/provision/vendor/`).
