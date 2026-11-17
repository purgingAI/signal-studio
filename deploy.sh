#!/usr/bin/env bash
# Deploy Signal Studio to the AWS box as its own container (127.0.0.1:8083). Leaves other containers alone.
set -euo pipefail
cd "$(dirname "$0")"
IP=13.233.175.201; O="-i $HOME/.ssh/duet-key.pem -o ConnectTimeout=10"
rsync -az -e "ssh $O" --exclude .venv --exclude data --exclude .env --exclude __pycache__ --exclude .git ./ ubuntu@$IP:/home/ubuntu/signal-studio/
ssh $O ubuntu@$IP 'cd signal-studio && sudo docker build -q -t signal-studio . && sudo mkdir -p /srv/signal/data \
 && sudo docker rm -f signal-studio >/dev/null 2>&1; sudo docker run -d --name signal-studio --restart unless-stopped \
    -p 127.0.0.1:8083:8099 -v /srv/signal/data:/app/data --env-file /srv/signal/.env signal-studio'
echo "deployed → https://signal.13-233-175-201.sslip.io"
