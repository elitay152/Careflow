#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python -m venv .venv
.venv/bin/python -m pip install 'pip==26.2.2'
.venv/bin/python -m pip install -c security-constraints.txt -e '.[dev,analytics,assistant]'
