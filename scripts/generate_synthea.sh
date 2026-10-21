#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
destination="${1:-data/synthea-v3.3.0-seed42}"
population="${2:-100}"
seed="${3:-42}"
if [[ ! "$population" =~ ^[1-9][0-9]{0,4}$ || ! "$seed" =~ ^[0-9]{1,9}$ ]]; then
  echo "Usage: $0 [new-output-directory] [population: 1-99999] [numeric-seed]" >&2
  exit 1
fi
if [[ -e "$destination" ]]; then
  echo "Refusing to overwrite $destination. Reuse it for validation or choose a new directory." >&2
  exit 1
fi
mkdir -p data/tools
artifact=data/tools/synthea-v3.3.0.jar
expected=8ba04f7d73abadd5a377e41edf24c5c83935a1cb07c6d982cd5db731ef1cf445
if [[ ! -f "$artifact" ]]; then
  curl -fL --retry 2 https://github.com/synthetichealth/synthea/releases/download/v3.3.0/synthea-with-dependencies.jar -o "$artifact.partial"
  printf '%s  %s\n' "$expected" "$artifact.partial" | sha256sum --check --status
  mv "$artifact.partial" "$artifact"
fi
printf '%s  %s\n' "$expected" "$artifact" | sha256sum --check --status
java -Xmx2g -jar "$artifact" -s "$seed" -cs "$seed" -p "$population" -r 20250101 -e 20250101 \
  --generate.thread_pool_size=2 --exporter.baseDirectory="$destination" --exporter.fhir.export=true
