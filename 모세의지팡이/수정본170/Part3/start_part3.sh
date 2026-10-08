#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1
export PYTHONDONTWRITEBYTECODE=1
exec python3 run.py "$@"
