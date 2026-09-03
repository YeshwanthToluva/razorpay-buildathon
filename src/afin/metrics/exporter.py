"""Prometheus textfile export.

Deliberately a function, not a service. The objective is visibility, not
infrastructure: this writes a file that a node_exporter textfile collector or a
one-line HTTP server can pick up, and nothing in the system depends on it.
"""

from __future__ import annotations

import pathlib

from afin.metrics.recovery import RunMetrics

_SCALARS = (
    ("afin_payments_processed", "Payments processed in the run", "payments_processed"),
    ("afin_revenue_at_risk_minor", "Revenue at risk, minor units", "revenue_at_risk_minor"),
    ("afin_revenue_recovered_minor", "Revenue recovered, minor units",
     "revenue_recovered_minor"),
    ("afin_recovery_rate", "Recovered value over value at risk", "recovery_rate"),
    ("afin_actions_proposed", "Actions proposed by the reasoner", "actions_proposed"),
    ("afin_actions_approved", "Actions approved by policy", "actions_approved"),
    ("afin_actions_denied", "Actions denied by policy", "actions_denied"),
    ("afin_actions_executed", "Actions executed against the provider", "actions_executed"),
    ("afin_invalid_proposals", "Proposals that failed schema validation",
     "invalid_proposals"),
    ("afin_policy_violations_attempted", "Unsafe actions proposed",
     "policy_violations_attempted"),
    ("afin_policy_violations_prevented", "Unsafe actions blocked by policy",
     "policy_violations_prevented"),
    ("afin_unsafe_actions_executed",
     "Unsafe actions that reached execution. Must always be zero.",
     "unsafe_actions_executed"),
    ("afin_escalations", "Cases handed to a human", "escalations"),
    ("afin_stopped", "Cases stopped", "stopped"),
    ("afin_retries", "Retry attempts made", "retries"),
    ("afin_customer_contacts", "Outbound customer contacts", "customer_contacts"),
)


def render(metrics: RunMetrics) -> str:
    run = metrics.run_id
    lines: list[str] = []
    for name, help_text, attr in _SCALARS:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f'{name}{{run_id="{run}"}} {getattr(metrics, attr)}')

    lines.append("# HELP afin_denials_by_rule Policy denials, by deciding rule")
    lines.append("# TYPE afin_denials_by_rule gauge")
    for rule, n in sorted(metrics.denials_by_rule.items()):
        lines.append(f'afin_denials_by_rule{{run_id="{run}",rule="{rule}"}} {n}')

    lines.append("# HELP afin_proposals_by_action Proposals, by proposed action")
    lines.append("# TYPE afin_proposals_by_action gauge")
    for action, n in sorted(metrics.proposals_by_action.items()):
        safe = action.replace('"', "'")
        lines.append(f'afin_proposals_by_action{{run_id="{run}",action="{safe}"}} {n}')

    lines.append("# HELP afin_recovery_rate_by_category Recovery rate per failure category")
    lines.append("# TYPE afin_recovery_rate_by_category gauge")
    for cat, b in sorted(metrics.recovery_by_category.items()):
        lines.append(
            f'afin_recovery_rate_by_category{{run_id="{run}",category="{cat}"}} '
            f'{b["recovery_rate"]}'
        )
    return "\n".join(lines) + "\n"


def write(metrics: RunMetrics, path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(metrics))
    return path
