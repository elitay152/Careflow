#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ -f .env ]]; then
  set -a
  . ./.env
  set +a
fi
: "${CAREFLOW_ASSISTANT_DATABASE_URL:?Configure the local reader first; see docs/assistant.md}"
# Trust only this Codespace's exact forwarded hostname, keeping DNS rebinding
# protection enabled. Other tunnels must set CAREFLOW_ALLOWED_HOSTS explicitly.
if [[ -z "${CAREFLOW_ALLOWED_HOSTS:-}" && -n "${CODESPACE_NAME:-}" && -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
  export CAREFLOW_ALLOWED_HOSTS="localhost,127.0.0.1,[::1],${CODESPACE_NAME}-8000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
fi
exec .venv/bin/uvicorn careflow.api:create_app --factory --host 127.0.0.1 --port 8000
