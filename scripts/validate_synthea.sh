#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ -f .env ]]; then
  set -a
  . ./.env
  set +a
fi
: "${DATABASE_URL:?Set DATABASE_URL or create .env first}"
input="${1:-data/synthea-v3.3.0-seed42/fhir}"
schema="${2:-synthea_validation_$(date -u +%Y%m%d_%H%M%S)}"
exec .venv/bin/python -m careflow.validation --input "$input" --schema "$schema" --output "data/validation/$schema"
