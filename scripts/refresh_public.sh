#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ $# != 4 || "$1" != --confirm-synthetic ]]; then
  echo 'Usage: bash scripts/refresh_public.sh --confirm-synthetic RAW_SCHEMA ANALYTICS_SCHEMA COHORT_LABEL' >&2
  exit 1
fi
for schema in "$2" "$3"; do
  if [[ ! "$schema" =~ ^[a-z][a-z0-9_]{0,62}$ ]]; then
    echo 'Schemas must be simple lowercase identifiers.' >&2
    exit 1
  fi
done
if [[ -f .env ]]; then
  set -a
  . ./.env
  set +a
fi
: "${DATABASE_URL:?Set DATABASE_URL or create .env first}"
mkdir -p data/public
exec 9>data/public/refresh.lock
flock -n 9 || { echo 'A public refresh is already running.' >&2; exit 1; }
export DBT_RAW_SCHEMA="$2" DBT_TARGET_SCHEMA="$3"
export DBT_TARGET_PATH
DBT_TARGET_PATH="$(mktemp -d "$PWD/data/public/dbt-refresh-XXXXXXXX")"
.venv/bin/careflow warehouse-build
.venv/bin/careflow export-public --confirm-synthetic \
  --raw-schema "$2" --analytics-schema "$3" --cohort-label "$4" \
  --dbt-artifacts "$DBT_TARGET_PATH" \
  --archive docs/validation-results.json --archive-date 2026-10-20 \
  --output data/public/careflow.json
