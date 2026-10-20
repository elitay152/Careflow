#!/usr/bin/env bash
# Prepare the complete demo without using the cloud environment settings UI.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ "${1:-}" != "" && "${1:-}" != "--existing-database" ]]; then
  echo 'Usage: bash scripts/bootstrap.sh [--existing-database]' >&2
  exit 2
fi
bash scripts/install.sh
if [[ "${1:-}" != "--existing-database" && ! -e .env ]]; then
  .venv/bin/python - <<'PY'
from pathlib import Path
import secrets
password = secrets.token_hex(24)
with Path('.env').open('x') as stream:
    stream.write(f'POSTGRES_PASSWORD={password}\nDATABASE_URL=postgresql+psycopg://careflow:{password}@localhost:5432/careflow\n')
Path('.env').chmod(0o600)
PY
fi
if [[ -f .env ]]; then
  set -a
  . ./.env
  set +a
fi
: "${DATABASE_URL:?Set DATABASE_URL when using --existing-database}"
if [[ "${1:-}" != "--existing-database" ]]; then
  docker compose --env-file .env up -d --wait
fi
if ! compgen -G 'data/demo/*.json' > /dev/null; then
  .venv/bin/careflow generate-demo --extended
fi
bash scripts/run_pipeline.sh
.venv/bin/python scripts/configure_assistant.py
echo 'Demo ready. Run bash scripts/start_assistant.sh for the baseline browser interface.'
echo 'No model API request was made by this setup.'
