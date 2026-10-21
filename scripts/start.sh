#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ ! -f .env ]]; then
  echo 'Create .env from .env.example with a local database password first.' >&2
  exit 1
fi
set -a
. ./.env
set +a
docker compose --env-file .env up -d --wait
export DAGSTER_HOME="$PWD/.dagster"
mkdir -p "$DAGSTER_HOME"
if [[ ! -e "$DAGSTER_HOME/dagster.yaml" ]]; then
  cp config/dagster.yaml "$DAGSTER_HOME/dagster.yaml"
fi
exec .venv/bin/dagster dev -m careflow.orchestration --host 127.0.0.1 --port 3000
