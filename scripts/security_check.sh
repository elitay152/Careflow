#!/usr/bin/env bash
# Advisory lookup and tracked-file secret checks; no provider/model calls.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
audit_tools="$(mktemp -d)"
trap 'rm -rf "$audit_tools"' EXIT
python -m venv "$audit_tools"
"$audit_tools/bin/python" -m pip install 'pip==26.2.2'
"$audit_tools/bin/python" -m pip install 'pip-audit==2.10.1' 'detect-secrets==1.5.0'
site_packages="$(.venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
"$audit_tools/bin/pip-audit" --cache-dir "$audit_tools/cache" --path "$site_packages" --skip-editable
git ls-files -z | xargs -0 "$audit_tools/bin/detect-secrets-hook" --baseline .secrets.baseline
