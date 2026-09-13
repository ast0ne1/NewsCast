#!/bin/bash
set -euo pipefail

cd /opt/newscast

if [[ -f /opt/newscast/.env ]]; then
  set -a
  # shellcheck disable=SC1090
  source <(sed 's/\r$//' /opt/newscast/.env)
  set +a
fi

exec /opt/newscast/.venv/bin/uvicorn app.main:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8080}"
