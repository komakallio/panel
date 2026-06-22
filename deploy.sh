#!/usr/bin/env bash
# Deploy the latest main on the VPS. Run from the repo directory:  ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

git pull --ff-only
docker compose up -d --build
docker image prune -f
echo "Deployed. Recent app logs:"
docker compose logs --tail=20 app
