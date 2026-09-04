"""Baseline vs agent divergence analysis. READ-ONLY.

Reads recorded run metrics and the audit ledger. Imports nothing that mutates
state and is deliberately kept outside src/afin so it cannot be pulled into the
experiment path.

    PYTHONPATH=src python analysis/divergence.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")

from afin.metrics.recovery import RunMetrics, run_is_valid  # noqa: E402

RUNS = pathlib.Path("data/runs")


def valid_runs() -> tuple[list[dict], list[dict]]:
    """Complete runs that pass every validity check, split baseline/agent."""
    base, agent = [], []
    for f in sorted(RUNS.glob("*.json")):
        d = json.loads(f.read_text())
        m = RunMetrics(**{k: v for k, v in d.items() if k in RunMetrics.__dataclass_fields__})
        ok, _ = run_is_valid(m)
        if not (ok and d["payments_processed"] == 50 and d["actions_proposed"] >= 40):
            continue
        (base if d["run_id"].startswith("baseline") else agent).append(d)
    return base, agent


def recovery_by_category(base: dict, agents: list[dict]) -> str:
    out = [f"{'category':<22}{'at_risk':>10}{'base':>10}" + "".join(f"{a['run_id'][:8]:>10}" for a in agents)]
    for c in sorted(base["recovery_by_category"]):
        b = base["recovery_by_category"][c]
        out.append(
            f"{c:<22}{b['at_risk_minor']/100:>10,.0f}{b['recovered_minor']/100:>10,.0f}"
            + "".join(f"{a['recovery_by_category'][c]['recovered_minor']/100:>10,.0f}" for a in agents)
        )
    return "\n".join(out)


def substitution(base: dict, agents: list[dict]) -> str:
    """The headline mechanism: payment links traded for escalations."""
    out = [f"{'run':<36}{'link':>6}{'escalate':>10}{'escalations':>13}{'recovery':>10}"]
    for d in [base] + agents:
        out.append(
            f"{d['run_id'][:35]:<36}"
            f"{d['proposals_by_action'].get('GENERATE_PAYMENT_LINK', 0):>6}"
            f"{d['proposals_by_action'].get('REQUEST_HUMAN_REVIEW', 0):>10}"
            f"{d['escalations']:>13}{d['recovery_rate']:>10.1%}"
        )
    return "\n".join(out)


def calibration(agents: list[dict]) -> str:
    out = [f"{'run':<36}{'n>=0.9':>8}{'success':>9}{'gap':>8}"]
    for d in agents:
        c = d["confidence_calibration"].get("0.9-1.0")
        if c:
            out.append(f"{d['run_id'][:35]:<36}{c['n']:>8}{c['hit_rate']:>9.0%}{0.95 - c['hit_rate']:>+8.0%}")
    return "\n".join(out)


def main() -> None:
    base_runs, agents = valid_runs()
    base = base_runs[0]
    print(f"valid baseline runs: {len(base_runs)} (recovery "
          f"{ {r['recovery_rate'] for r in base_runs} })")
    print(f"valid agent runs:    {len(agents)}\n")
    print("RECOVERY BY CATEGORY (rupees)\n" + recovery_by_category(base, agents), "\n")
    print("ACTION SUBSTITUTION\n" + substitution(base, agents), "\n")
    print("CONFIDENCE CALIBRATION\n" + calibration(agents))


if __name__ == "__main__":
    main()
