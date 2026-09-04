#!/usr/bin/env bash
# Arms run CONCURRENTLY: each has a private dataset copy, and different providers
# do not share a rate limit.
set -u
PY="${PY:-python}"
cd "$(dirname "$0")"
export PYTHONPATH=src
"$PY" -u -m afin.experiment.run --arm baseline > data/run_baseline.log 2>&1 &
for prof in "$@"; do
  "$PY" -u -m afin.experiment.run --arm agent --profile "$prof" > "data/run_agent_$prof.log" 2>&1 &
done
wait
echo ALL_DONE
