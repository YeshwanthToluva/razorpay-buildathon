#!/usr/bin/env bash
# Sequential, because every run resets the shared dataset.
set -u
V=/home/nitin/Documents/ai-planet/platform/aiplanet_platform/venv/bin/python
cd /home/nitin/Documents/Agents-Influence-in-Finance
export PYTHONPATH=src
$V -u -m afin.experiment.run --arm baseline > data/run_baseline.log 2>&1
echo "baseline exit=$?"
for prof in minimaxnv nemotron; do
  $V -u -m afin.experiment.run --arm agent --profile "$prof" > "data/run_agent_$prof.log" 2>&1
  echo "$prof exit=$?"
done
echo ALL_DONE
