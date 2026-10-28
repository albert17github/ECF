#!/usr/bin/env bash
set -euo pipefail
project="$(cd -- "$(dirname -- "$0")" && pwd)"
exec "${ECF_PYTHON:-python3}" "$project/tools/build.py" "$@"
