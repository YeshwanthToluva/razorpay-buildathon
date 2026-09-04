"""Publish the synthetic dataset and the adversarial evidence. READ-ONLY.

Writes browsable copies of what the experiments ran on, so a reader can inspect
the inputs without a database or a Python session.

SYNTHETIC DATA ONLY. Every row is generated from a seeded PRNG; no real
customer, payment or communication data exists anywhere in this project.

    PYTHONPATH=src python analysis/export_dataset.py
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys

sys.path.insert(0, "src")

from afin.db.seed import DEFAULT_SEED, SCENARIOS, generate  # noqa: E402

OUT = pathlib.Path("data/dataset")
ADV = pathlib.Path("data/adversarial")
LEDGER = pathlib.Path("data/ledger")


def write_csv(path: pathlib.Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})


def export_dataset() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ds = generate(seed=DEFAULT_SEED)
    write_csv(OUT / "payments.csv", ds.payments)
    write_csv(OUT / "customers.csv", ds.customers)
    (OUT / "payments.json").write_text(json.dumps(ds.payments, indent=2, default=str))
    (OUT / "customers.json").write_text(json.dumps(ds.customers, indent=2, default=str))
    (OUT / "manifest.json").write_text(json.dumps({
        "dataset_version": ds.version,
        "seed": ds.seed,
        "payment_count": len(ds.payments),
        "customer_count": len(ds.customers),
        "manifest_sha256": ds.manifest_sha256(),
        "synthetic": True,
        "note": "Generated from a seeded PRNG. Regenerate with "
                "afin.db.seed.generate(seed=%d); the sha256 above must match." % ds.seed,
        "scenarios": [
            {"tag": s.tag, "count": s.count, "failure_category": s.category.value}
            for s in SCENARIOS
        ],
    }, indent=2))
    print(f"dataset  -> {OUT} ({len(ds.payments)} payments, sha {ds.manifest_sha256()[:12]})")


def export_adversarial() -> None:
    """Every action the policy engine refused, across every exported run."""
    ADV.mkdir(parents=True, exist_ok=True)
    blocked, by_rule = [], {}
    denied = held = 0
    for f in sorted(LEDGER.glob("*.json")):
        led = json.loads(f.read_text())
        run = f.stem
        for e in led["audit_events"]:
            if e["event_type"] != "POLICY_EVALUATED" or e["policy_decision"] == "ALLOW":
                continue
            blocked.append({
                "run_id": run, "payment_id": e["payment_id"], "cycle": e["cycle"],
                "proposed_action": e["proposed_action"], "decision": e["policy_decision"],
                "rule": e["policy_rule"], "risk_level": e["risk_level"],
                "reason": e["policy_reason"], "executed": False,
            })
            by_rule[e["policy_rule"]] = by_rule.get(e["policy_rule"], 0) + 1
            if e["policy_decision"] == "REQUIRE_APPROVAL":
                held += 1
            else:
                denied += 1

    executed_without_allow = sum(
        1 for f in LEDGER.glob("*.json")
        for e in json.loads(f.read_text())["audit_events"]
        if e["event_type"] == "ACTION_EXECUTED" and e["policy_decision"] != "ALLOW"
    )
    write_csv(ADV / "blocked_actions.csv", blocked)
    (ADV / "summary.json").write_text(json.dumps({
        "not_authorized_total": len(blocked),
        "denied_outright": denied,
        "held_for_human_approval": held,
        "executed_without_policy_allow": executed_without_allow,
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "note": "Every row is an action the agent proposed that the deterministic policy "
                "engine did not authorize. denied_outright were refused; "
                "held_for_human_approval were high-value actions routed to a human, "
                "and at autonomy level 2 there is no human, so they did not execute "
                "either. executed_without_policy_allow must be 0.",
    }, indent=2))
    print(f"adversarial -> {ADV} ({len(blocked)} blocked, "
          f"{executed_without_allow} executed without ALLOW)")


if __name__ == "__main__":
    export_dataset()
    export_adversarial()
