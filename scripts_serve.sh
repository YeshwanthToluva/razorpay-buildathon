#!/usr/bin/env bash
# Site on :3000, live API on :8000.
set -u
cd "$(dirname "$0")"
PY="${PY:-python}"
( cd ui && exec "$PY" -m http.server 3000 --bind 127.0.0.1 ) &
PYTHONPATH=src:. exec "$PY" -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
