#!/bin/bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd -- "$script_dir/.." && pwd)"
action="$(basename -- "$0" .sh)"
python="$root/backend/.venv/bin/python"
if [ ! -x "$python" ]; then
    python=python3
fi
exec "$python" "$script_dir/cbc.py" "$action" "$@"
