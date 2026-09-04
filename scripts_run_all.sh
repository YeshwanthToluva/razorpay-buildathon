#!/usr/bin/env bash
# Arms run CONCURRENTLY: each has a private dataset copy, and they hit different
# providers, so their rate limits do not compound.
set -u
V=/home/nitin/Documents/ai-planet/platform/aiplanet_platform/venv/bin/python
cd /home/nitin/Documents/Agents-Influence-in-Finance
export PYTHONPATH=src
$V -u -m afin.experiment.run --arm baseline > data/run_baseline.log 2>&1 &
for prof in minimaxnv nemotron; do
  $V -u -m afin.experiment.run --arm agent --profile "$prof" > "data/run_agent_$prof.log" 2>&1 &
done
wait
echo ALL_DONE
