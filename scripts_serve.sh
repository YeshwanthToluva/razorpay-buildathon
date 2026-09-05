#!/usr/bin/env bash
# Console on :3000 (no-cache), live API on :8000.
#
# AFIN_LOG_SPANS=1 prints every model, policy and tool call to this terminal as
# it happens. The trace already reaches the browser and the run file; this puts
# the same thing where a person watching a demo is actually looking.
set -u
cd "$(dirname "$0")"
PY="${PY:-python}"
export AFIN_LOG_SPANS="${AFIN_LOG_SPANS:-1}"
"$PY" apps/serve_ui.py 3000 &
UI=$!
trap 'kill "$UI" 2>/dev/null' EXIT
PYTHONPATH=src:. exec "$PY" -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
