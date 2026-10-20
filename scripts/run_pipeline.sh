#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ -f .env ]]; then
  set -a
  . ./.env
  set +a
fi
: "${DATABASE_URL:?Set DATABASE_URL or create .env first}"
export DAGSTER_HOME="$PWD/.dagster"
mkdir -p "$DAGSTER_HOME"
if [[ ! -e "$DAGSTER_HOME/dagster.yaml" ]]; then
  cp config/dagster.yaml "$DAGSTER_HOME/dagster.yaml"
fi
exec .venv/bin/dagster job execute -m careflow.orchestration -j daily_pipeline
