#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p config/cache logs
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
node skills/fetch_devices.js --refresh
exec openclaw gateway run
