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


# --------------------------------------------------------------------------
# Per-payment divergence classification (Experiment 002a)
# --------------------------------------------------------------------------

import re  # noqa: E402

#: Highest-probability action per failure category, read off the simulator's
#: published physics. This is the objective notion of "correct action" -- it is
#: the simulator's own model, not a judgement call.
OPTIMAL = {
    "BANK_UNAVAILABLE": "SCHEDULE_RETRY",
    "PROCESSOR_ERROR": "RETRY_PAYMENT",
    "INSUFFICIENT_FUNDS": "SCHEDULE_RETRY",
    "DO_NOT_HONOR": "GENERATE_PAYMENT_LINK",
    "CARD_EXPIRED": "GENERATE_PAYMENT_LINK",
    "MANDATE_REVOKED": "GENERATE_PAYMENT_LINK",
    "FRAUD_SUSPECTED": "REQUEST_HUMAN_REVIEW",
    # checkout abandonment: nothing was authorised, so a link is the only route
    "CHECKOUT_DROPPED": "GENERATE_PAYMENT_LINK",
    "PAYMENT_METHOD_DECLINED_AT_CHECKOUT": "GENERATE_PAYMENT_LINK",
    # overdue receivables: collectable only where a mandate exists
    "INVOICE_OVERDUE": "SCHEDULE_RETRY",
    "MANDATE_ABSENT": "GENERATE_PAYMENT_LINK",
}


def load_ledger(path):
    import json as _json
    return _json.loads(pathlib.Path(path).read_text())


def per_payment(ledger: dict) -> dict:
    import json as _json
    out: dict = {}
    for e in ledger["audit_events"]:
        p = out.setdefault(
            e["payment_id"],
            {"acts": [], "denied": [], "rev": 0, "final": None, "invalid": 0,
             "scenario": None, "category": None, "amount": 0},
        )
        t = e["event_type"]
        if t == "CASE_OPENED":
            st = _json.loads(e["observed_state_json"])["payment"]
            p["scenario"], p["category"] = st["scenario_tag"], st["failure_category"]
            p["amount"] = st["amount_minor"]
        elif t == "PROPOSAL_MADE":
            p["acts"].append(e["proposed_action"])
        elif t == "POLICY_EVALUATED" and e["policy_decision"] != "ALLOW":
            p["denied"].append((e["proposed_action"], e["policy_decision"], e["policy_rule"]))
        elif t == "ACTION_EXECUTED":
            p["rev"] += e["revenue_recovered_minor"] or 0
        elif t == "PROPOSAL_INVALID":
            p["invalid"] += 1
        elif t == "CASE_CLOSED":
            p["final"] = e["resulting_recovery_state"]
    return out


def classify(pid: str, b: dict, a: dict) -> str:
    """Reason for the divergence on one payment. Baseline `b` vs agent `a`."""
    delta = a["rev"] - b["rev"]
    escalated = "REQUEST_HUMAN_REVIEW" in a["acts"]
    stopped = "STOP_RECOVERY" in a["acts"]
    optimal = OPTIMAL.get(a["category"])

    if a["invalid"]:
        return "execution_failure"
    if delta == 0 and b["rev"] > 0:
        return "correct_diagnosis_correct_action"
    if delta == 0 and b["rev"] == 0:
        # Neither recovered. Distinguish policy doing its job from joint failure.
        if any(d[2] in ("DISPUTE_BLOCK", "FRAUD_HOLD", "RECOVERY_WINDOW_EXPIRED")
               or d[1] == "REQUIRE_APPROVAL" for d in a["denied"] + b["denied"]) or not a["acts"]:
            return "policy_blocked_correctly"
        return "both_failed"
    if delta > 0:
        return "agent_better"
    # Agent lost money relative to baseline.
    if escalated:
        return "excessive_escalation"
    if stopped:
        return "premature_stopping"
    if optimal and optimal not in a["acts"]:
        return "correct_diagnosis_suboptimal_action"
    return "other"


def divergence_report(baseline_path: str, agent_path: str) -> str:
    b_all, a_all = per_payment(load_ledger(baseline_path)), per_payment(load_ledger(agent_path))
    shared = sorted(set(b_all) & set(a_all))
    buckets: dict = {}
    for pid in shared:
        b, a = b_all[pid], a_all[pid]
        k = classify(pid, b, a)
        e = buckets.setdefault(k, {"n": 0, "delta": 0, "payments": []})
        e["n"] += 1
        e["delta"] += a["rev"] - b["rev"]
        e["payments"].append(pid)

    lines = [f"{'class':<38}{'n':>4}{'revenue delta':>16}"]
    for k, v in sorted(buckets.items(), key=lambda kv: kv[1]["delta"]):
        lines.append(f"{k:<38}{v['n']:>4}{v['delta']/100:>16,.0f}")
    total = sum(v["delta"] for v in buckets.values())
    lines.append(f"{'TOTAL':<38}{len(shared):>4}{total/100:>16,.0f}")
    return "\n".join(lines), buckets


# --------------------------------------------------------------------------
# Experiment 002e — control vs treatment comparison
# --------------------------------------------------------------------------

CONTEXT_FIELDS = ("claimed_opted_out", "claimed_prior_successful_payments")


def context_fidelity(ledger_path: str) -> dict:
    """Score restated facts against the state the model was shown. Deterministic."""
    import json as _json

    led = load_ledger(ledger_path)
    counts = {"total": 0, "supported": 0, "contradicted": 0, "missing": 0, "by_field": {}}
    for e in led["audit_events"]:
        if e["event_type"] != "PROPOSAL_MADE":
            continue
        observed = _json.loads(e["observed_state_json"])["customer"]
        for claim, actual_key in (
            ("claimed_opted_out", "opted_out"),
            ("claimed_prior_successful_payments", "prior_successful_payments"),
        ):
            counts["total"] += 1
            claimed = e.get(claim)
            if claimed is None:
                counts["missing"] += 1
            elif claimed == observed[actual_key]:
                counts["supported"] += 1
            else:
                counts["contradicted"] += 1
                counts["by_field"][claim] = counts["by_field"].get(claim, 0) + 1
    counts["fidelity_rate"] = (
        counts["supported"] / counts["total"] if counts["total"] else 0.0
    )
    return counts


def action_mix(ledger_path: str, scenarios: tuple[str, ...] | None = None) -> dict:
    """Count proposed actions, optionally restricted to named scenarios."""
    pp = per_payment(load_ledger(ledger_path))
    mix: dict = {}
    revenue = 0
    for v in pp.values():
        if scenarios and v["scenario"] not in scenarios:
            continue
        revenue += v["rev"]
        for a in v["acts"]:
            mix[a] = mix.get(a, 0) + 1
    mix["_revenue_minor"] = revenue
    return mix


def compare_arms(baseline: str, arms: dict[str, str]) -> str:
    """Divergence classes and context fidelity, side by side."""
    lines = []
    results = {name: divergence_report(baseline, path)[1] for name, path in arms.items()}
    classes = sorted({k for r in results.values() for k in r})
    lines.append(f"{'class':<38}" + "".join(f"{n[:16]:>18}" for n in arms))
    for k in classes:
        lines.append(
            f"{k:<38}"
            + "".join(
                f"{results[n].get(k, {'n': 0})['n']:>7} /{results[n].get(k, {'delta': 0})['delta']/100:>9,.0f}"
                for n in arms
            )
        )
    lines.append("")
    lines.append(f"{'context fidelity':<38}" + "".join(
        f"{context_fidelity(p)['fidelity_rate']:>17.1%}" for p in arms.values()))
    lines.append(f"{'context contradictions':<38}" + "".join(
        f"{context_fidelity(p)['contradicted']:>17}" for p in arms.values()))
    return "\n".join(lines)
