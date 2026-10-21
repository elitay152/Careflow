#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ $# != 0 ]]; then
  echo 'Usage: bash scripts/portfolio_demo.sh' >&2
  exit 2
fi
if [[ ! -x .venv/bin/python ]]; then
  echo 'Install dependencies first: bash scripts/install.sh' >&2
  exit 1
fi
if [[ -f .env ]]; then
  set -a
  . ./.env
  set +a
fi
: "${DATABASE_URL:?Set DATABASE_URL or configure the local .env first}"
exec .venv/bin/python -m careflow.portfolio_demo
