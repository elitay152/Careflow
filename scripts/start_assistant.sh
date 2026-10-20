#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ -f .env ]]; then
  set -a
  . ./.env
  set +a
fi
: "${CAREFLOW_ASSISTANT_DATABASE_URL:?Configure the local reader first; see docs/assistant.md}"
exec .venv/bin/uvicorn careflow.api:create_app --factory --host 127.0.0.1 --port 8000
