#!/usr/bin/env bash
set -euo pipefail
ecf_project_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "${ECF_PYTHON:-python3}" "$ecf_project_root/tools/build.py" "$@"
