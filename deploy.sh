#!/bin/sh
# Push the current commit to the droplet and restart what changed.
#   ./deploy.sh droplet-host
set -eu
HOST="${1:?usage: deploy.sh <ssh-host>}"
ssh "$HOST" 'cd ~/flightframe && git pull --ff-only && docker compose --profile cloud up -d --build'
