"""Compare two completed runs.

    python -m afin.experiment.compare <run_a.json> <run_b.json>

Deliberately tiny. The full evaluation suite is a later sprint; this exists so
the baseline and the agent arm can be put side by side without anyone reading
two reports and doing arithmetic in their head.
"""

from __future__ import annotations

import argparse
import json
import pathlib

_ROWS = [
    ("revenue at risk", "revenue_at_risk_minor", "money"),
    ("revenue recovered", "revenue_recovered_minor", "money"),
    ("recovery rate (value)", "recovery_rate", "pct"),
    ("recovery rate (payments)", "payment_recovery_rate", "pct"),
    ("actions proposed", "actions_proposed", "int"),
    ("invalid proposals", "invalid_proposals", "int"),
    ("actions denied", "actions_denied", "int"),
    ("actions executed", "actions_executed", "int"),
    ("retries", "retries", "int"),
    ("customer contacts", "customer_contacts", "int"),
    ("escalations", "escalations", "int"),
    ("stopped", "stopped", "int"),
    ("attempts per payment", "average_attempts_per_payment", "float"),
    ("recovered per intervention", "revenue_recovered_per_intervention_minor", "money"),
    ("violations attempted", "policy_violations_attempted", "int"),
    ("violations prevented", "policy_violations_prevented", "int"),
    ("UNSAFE EXECUTED", "unsafe_actions_executed", "int"),
]


def _fmt(value, kind: str) -> str:
    if kind == "money":
        return f"Rs {value / 100:,.2f}"
    if kind == "pct":
        return f"{value:.1%}"
    if kind == "float":
        return f"{value:.2f}"
    return str(value)


def _delta(a, b, kind: str) -> str:
    diff = b - a
    if abs(diff) < 1e-9:
        return "="
    sign = "+" if diff > 0 else ""
    if kind == "money":
        return f"{sign}Rs {diff / 100:,.2f}"
    if kind == "pct":
        return f"{sign}{diff:.1%}"
    if kind == "float":
        return f"{sign}{diff:.2f}"
    return f"{sign}{diff}"


def render(a: dict, b: dict) -> str:
    width = 30
    lines = [
        "",
        f"{'':<{width}} {a['run_id'][:22]:>22} {b['run_id'][:22]:>22} {'delta':>16}",
        "-" * (width + 64),
    ]
    for label, key, kind in _ROWS:
        av, bv = a.get(key, 0), b.get(key, 0)
        lines.append(
            f"{label:<{width}} {_fmt(av, kind):>22} {_fmt(bv, kind):>22} "
            f"{_delta(av, bv, kind):>16}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two run metric files.")
    parser.add_argument("baseline", type=pathlib.Path)
    parser.add_argument("candidate", type=pathlib.Path)
    args = parser.parse_args()

    print(
        render(
            json.loads(args.baseline.read_text()),
            json.loads(args.candidate.read_text()),
        )
    )


if __name__ == "__main__":
    main()
