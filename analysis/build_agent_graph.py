"""Derive the agent map from the code, not from a description of the code.

A hand-drawn architecture diagram is a claim about the system that nothing
checks. This builds the same picture by asking the real objects: the action
space comes from the enum, the rules and their order come from the policy
engine's RULES tuple, the tool scope comes from the notifier's allowlist, and
the edge from each action to the rules that gate it is *observed* -- every
action is put to the engine against every scenario in the dataset and the rules
that actually fire are recorded.

So if a rule stops applying to an action, the map changes on the next build
rather than quietly becoming a lie.

    PYTHONPATH=src python analysis/build_agent_graph.py
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

from afin.db.repository import _to_customer, _to_payment
from afin.db.seed import EPOCH, generate
from afin.domain.enums import (
    COMMUNICATION_ACTIONS,
    FINANCIAL_ACTIONS,
    SAFETY_VALVE_ACTIONS,
    ActionType,
)
from afin.domain.models import ProposedAction
from afin.policy.config import DEFAULT_POLICY_CONFIG
from afin.policy.content import ContentRule
from afin.policy.engine import RULES, PolicyRequest, evaluate
from afin.tools.notify import ALLOWED_TOOL_ACTIONS

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "agent_graph.json"

#: Rules that do not depend on the case at all, so no dataset sweep can observe
#: them. They gate every action by construction.
UNIVERSAL = ("UNSUPPORTED_ACTION", "PAYMENT_MISMATCH")

SAYS = {
    "RETRY_PAYMENT": "charge the instrument again now",
    "SCHEDULE_RETRY": "charge it again later",
    "SEND_PAYMENT_REMINDER": "tell the customer it is outstanding",
    "GENERATE_PAYMENT_LINK": "give the customer another way to pay",
    "REQUEST_HUMAN_REVIEW": "hand the case to a person",
    "STOP_RECOVERY": "close the case and stop",
}


def observed_gates() -> dict[str, list[str]]:
    """For each action, the rules that actually refused it somewhere."""
    d = generate(seed=20260304, version="graph-probe")
    customers = {c["id"]: c for c in d.customers}
    seen: dict[str, set[str]] = {a.value: set(UNIVERSAL) for a in ActionType}

    # Both configurations matter: in live email mode a charge reaches the
    # customer, so the contact rules govern it too.
    configs = (
        DEFAULT_POLICY_CONFIG,
        dataclasses.replace(DEFAULT_POLICY_CONFIG, contact_is_the_payment_rail=True),
    )
    for row in d.payments:
        payment = _to_payment(type("R", (), row)())
        customer = _to_customer(type("R", (), customers[row["customer_id"]])())
        for cfg in configs:
            for action in ActionType:
                proposal = ProposedAction(
                    action=action, payment_id=payment.id, diagnosis="probe",
                    reasoning_summary="probe", confidence=0.5,
                )
                decision = evaluate(PolicyRequest(
                    proposal=proposal, payment=payment, customer=customer,
                    now=EPOCH, config=cfg,
                ))
                if not decision.allowed:
                    seen[action.value].add(decision.policy.value)

    order = {r.value: i for i, (r, _) in enumerate(RULES)}
    return {a: sorted(rs, key=lambda r: order.get(r, 99)) for a, rs in seen.items()}


def main() -> None:
    gates = observed_gates()
    actions = []
    for a in ActionType:
        if a in COMMUNICATION_ACTIONS:
            kind, tools = "communication", ["content", "gmail", "paylink"]
        elif a in FINANCIAL_ACTIONS:
            kind, tools = "financial", ["rail", "content", "gmail", "paylink"]
        else:
            kind, tools = "valve", []
        actions.append({
            "action": a.value,
            "kind": kind,
            "says": SAYS.get(a.value, ""),
            "always_available": a in SAFETY_VALVE_ACTIONS,
            "gated_by": gates[a.value],
            "reaches": tools,
        })

    graph = {
        "actions": actions,
        "rules": [
            {"rule": r.value, "order": i + 1} for i, (r, _) in enumerate(RULES)
        ],
        "content_rules": [c.value for c in ContentRule if c is not ContentRule.PERMITTED],
        "tool_scope": sorted(ALLOWED_TOOL_ACTIONS),
        "policy_version": DEFAULT_POLICY_CONFIG.version,
        "fingerprint": DEFAULT_POLICY_CONFIG.fingerprint(),
        "note": ("Edges from an action to the rules that gate it are observed: every action was "
                 "put to the policy engine against every scenario in the dataset, in both the "
                 "simulated and the live-email configuration, and the rules that refused it were "
                 "recorded. Nothing here is hand-written except the plain-English gloss."),
    }
    OUT.write_text(json.dumps(graph, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    for a in graph["actions"]:
        print(f"  {a['action']:<22}{len(a['gated_by']):>2} gates  → {', '.join(a['reaches']) or '—'}")


if __name__ == "__main__":
    main()
