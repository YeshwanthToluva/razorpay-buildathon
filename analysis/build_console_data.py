"""Derive the evaluation console's dataset from committed evidence. READ-ONLY.

Reads only data/ledger/*.json and data/runs/*.json -- the files in git, not the
database -- so the console is reproducible from the repository alone and cannot
drift from what the experiment records cite.

    PYTHONPATH=src python analysis/build_console_data.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from afin.metrics.recovery import RunMetrics, run_is_valid  # noqa: E402
from analysis.divergence import divergence_report, per_payment  # noqa: E402
from analysis.feedback import transitions  # noqa: E402

LEDGER = pathlib.Path("data/ledger")
RUNS = pathlib.Path("data/runs")
OUT = pathlib.Path("analysis/console_data.json")

#: Runs shown in the console, in experiment order. Every one is committed.
ARMS = [
    ("baseline-20260904T074124-6b9ad6", "Deterministic baseline", "Level 0 · rules only", "baseline"),
    ("agent-gpt-20260904T102534-ea77bf", "002a agent", "prompt v1", "002a"),
    ("agent-gpt-control-20260904T104215-72b641", "002e control", "prompt v1 + claim fields", "002e"),
    ("agent-gpt-treatment-20260904T104643-382f4d", "002e treatment", "prompt v2 explicit state", "002e"),
    ("agent-gpt-treatment-nofb-20260904T105956-054a8f", "002g control", "v2, no outcome feedback", "002g"),
    ("agent-gpt-treatment-fb-20260904T110121-51e5ce", "002g feedback", "v2 + execution outcomes", "002g"),
]

BASELINE = ARMS[0][0]
MAX_TEXT = 400


def clip(s):
    if not isinstance(s, str):
        return s
    s = s.strip()
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT] + "…"


def load_metrics(run_id: str) -> dict | None:
    path = RUNS / f"{run_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def replay(ledger: dict) -> dict:
    """Per payment, the decision→policy→execution chain, in order."""
    out: dict = {}
    for e in ledger["audit_events"]:
        pid = e["payment_id"]
        p = out.setdefault(
            pid,
            {"scenario": None, "category": None, "amount": 0, "steps": [],
             "revenue": 0, "final": None},
        )
        t = e["event_type"]
        if t == "CASE_OPENED":
            st = json.loads(e["observed_state_json"])
            p["scenario"] = st["payment"]["scenario_tag"]
            p["category"] = st["payment"]["failure_category"]
            p["amount"] = st["payment"]["amount_minor"]
            p["observed"] = {
                "retry_count": st["payment"]["retry_count"],
                "contact_count": st["payment"]["contact_count"],
                "is_disputed": st["payment"]["is_disputed"],
                "opted_out": st["customer"]["opted_out"],
                "prior_successful_payments": st["customer"]["prior_successful_payments"],
                "risk_flag": st["customer"]["risk_flag"],
            }
        elif t == "PROPOSAL_MADE":
            p["steps"].append({
                "k": "proposal", "cycle": e["cycle"], "action": e["proposed_action"],
                "diagnosis": clip(e["agent_diagnosis"]), "why": clip(e["reasoning_summary"]),
                "confidence": e["confidence"],
                "claimed_opted_out": e.get("claimed_opted_out"),
                "claimed_prior": e.get("claimed_prior_successful_payments"),
                "claimed_last_outcome": e.get("claimed_last_attempt_outcome"),
            })
        elif t == "POLICY_EVALUATED":
            p["steps"].append({
                "k": "policy", "cycle": e["cycle"], "action": e["proposed_action"],
                "decision": e["policy_decision"], "rule": e["policy_rule"],
                "reason": clip(e["policy_reason"]), "risk": e["risk_level"],
            })
        elif t == "ACTION_EXECUTED":
            p["steps"].append({
                "k": "exec", "cycle": e["cycle"], "action": e["executed_action"],
                "result": e["execution_result"],
                "recovered": e["revenue_recovered_minor"] or 0,
                "state": e["resulting_recovery_state"],
            })
            p["revenue"] += e["revenue_recovered_minor"] or 0
        elif t == "PROPOSAL_INVALID":
            p["steps"].append({"k": "invalid", "cycle": e["cycle"], "error": clip(e["error"])})
        elif t == "CASE_CLOSED":
            p["final"] = e["resulting_recovery_state"]
    return out


def main() -> None:
    console: dict = {"arms": [], "replay": {}, "divergence": {}, "transitions": {}}

    for run_id, label, note, experiment in ARMS:
        led_path = LEDGER / f"{run_id}.json"
        if not led_path.exists():
            print(f"  skip {run_id}: no ledger export")
            continue
        ledger = json.loads(led_path.read_text())
        metrics = load_metrics(run_id) or {}
        m = RunMetrics(**{k: v for k, v in metrics.items() if k in RunMetrics.__dataclass_fields__})
        valid, why = run_is_valid(m) if metrics else (False, "no metrics file")

        console["arms"].append({
            "run_id": run_id, "label": label, "note": note, "experiment": experiment,
            "is_baseline": run_id == BASELINE,
            "model": (ledger.get("run") or {}).get("model"),
            "prompt_version": (ledger.get("run") or {}).get("prompt_version"),
            "valid": valid, "validity_note": why,
            "metrics": {k: metrics.get(k) for k in (
                "recovery_rate", "revenue_recovered_minor", "revenue_at_risk_minor",
                "payments_processed", "payments_recovered", "actions_proposed",
                "actions_approved", "actions_denied", "approvals_required",
                "actions_executed", "retries", "customer_contacts", "escalations",
                "stopped", "policy_violations_attempted", "policy_violations_prevented",
                "unsafe_actions_executed", "invalid_proposals", "agent_errors",
                "context_fidelity_rate", "context_claims_contradicted",
                "context_claims_total", "average_attempts_per_payment",
            )},
            "proposals_by_action": metrics.get("proposals_by_action", {}),
            "denials_by_rule": metrics.get("denials_by_rule", {}),
            "recovery_by_category": metrics.get("recovery_by_category", {}),
        })
        console["replay"][run_id] = replay(ledger)
        if run_id != BASELINE:
            _, buckets = divergence_report(str(LEDGER / f"{BASELINE}.json"), str(led_path))
            console["divergence"][run_id] = {
                k: {"n": v["n"], "delta": v["delta"], "payments": v["payments"]}
                for k, v in buckets.items()
            }
        console["transitions"][run_id] = transitions(ledger)

    OUT.write_text(json.dumps(console, sort_keys=True, separators=(",", ":")))
    kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT} ({kb:,.0f} KB) — {len(console['arms'])} arms, "
          f"{sum(len(v) for v in console['replay'].values())} payment replays")


if __name__ == "__main__":
    main()
