#!/usr/bin/env bash
# Console on :3000 (no-cache), live API on :8000.
set -u
cd "$(dirname "$0")"
PY="${PY:-python}"
"$PY" apps/serve_ui.py 3000 &
PYTHONPATH=src:. exec "$PY" -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
