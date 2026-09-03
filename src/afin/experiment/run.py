"""Experiment harness.

Holds the database handle and the provider, and is the only place that does.
Every run records the exact conditions it ran under -- reasoner, model, prompt
version, policy fingerprint, dataset version, seed -- so two runs that differ
can be explained rather than argued about.

    python -m afin.experiment.run --arm baseline
    python -m afin.experiment.run --arm agent
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import uuid
from datetime import datetime, timezone

from afin.agent.orchestrator import Orchestrator
from afin.agent.rule_based import RuleBasedReasoner
from afin.agent.schema import PROMPT_VERSION
from afin.audit.ledger import AuditLedger
from afin.db.engine import create_schema, get_engine
from afin.db.repository import load_cases
from afin.db.seed import DATASET_VERSION, DEFAULT_SEED, EPOCH, generate, load_into
from afin.metrics.exporter import write as write_prometheus
from afin.metrics.recovery import compute
from afin.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from afin.simulator.razorpay_sim import RazorpaySimulator

ARMS = ("baseline", "agent")


def build_reasoner(arm: str, profile: str):
    if arm == "baseline":
        return RuleBasedReasoner(), None
    from afin.agent.llm import LLMReasoner

    return LLMReasoner(profile=profile), PROMPT_VERSION


async def run_experiment(
    arm: str = "agent",
    profile: str = "gpt",
    seed: int = DEFAULT_SEED,
    reset: bool = True,
    config: PolicyConfig = DEFAULT_POLICY_CONFIG,
    now: datetime = EPOCH,
    limit: int | None = None,
) -> tuple[str, object]:
    engine = get_engine()
    create_schema(engine)

    if reset:
        # Each arm starts from the identical dataset, so arms are comparable.
        load_into(engine, generate(seed=seed))

    tag = arm if arm == "baseline" else f"{arm}-{profile}"
    run_id = f"{tag}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    reasoner, prompt_version = build_reasoner(arm, profile)
    ledger = AuditLedger(engine=engine, run_id=run_id)
    ledger.open_run(
        started_at=datetime.now(timezone.utc),
        experiment=arm,
        autonomy_level=0 if arm == "baseline" else 2,
        reasoner=reasoner.name,
        model=reasoner.model,
        model_config_json=json.dumps(
            {
                "temperature": getattr(reasoner, "temperature", None),
                "reasoning_effort": getattr(reasoner, "reasoning_effort", None),
                "api_style": getattr(getattr(reasoner, "profile", None), "api_style", None),
            }
        ),
        prompt_version=prompt_version,
        policy_version=config.version,
        policy_fingerprint=config.fingerprint(),
        dataset_version=DATASET_VERSION,
        random_seed=seed,
    )

    orchestrator = Orchestrator(
        engine=engine,
        reasoner=reasoner,
        provider=RazorpaySimulator(seed=seed),
        ledger=ledger,
        config=config,
        now=now,
        dataset_version=DATASET_VERSION,
    )

    cases = load_cases(engine, DATASET_VERSION)
    if limit:
        cases = cases[:limit]

    print(f"run {run_id}: {len(cases)} payments, reasoner={reasoner.name}")
    for i, (payment, customer) in enumerate(cases, 1):
        result = await orchestrator.run_case(payment, customer)
        flag = "*" if result.recovered_minor else " "
        print(
            f"  [{i:>2}/{len(cases)}] {payment.id} {payment.scenario_tag:<26} "
            f"{result.payment.payment_state.value:<11} "
            f"{flag}Rs{result.recovered_minor / 100:>10,.2f}  "
            f"proposed={result.proposals} denied={result.denied} exec={result.executed}"
        )
        if result.errors:
            for err in result.errors:
                print(f"        ! {err}")

    ledger.close_run()
    metrics = compute(engine, run_id, DATASET_VERSION)

    out_dir = pathlib.Path(__file__).resolve().parents[3] / "data" / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{run_id}.json").write_text(metrics.to_json())
    write_prometheus(metrics, out_dir / f"{run_id}.prom")

    return run_id, metrics


def report(metrics) -> str:
    m = metrics
    rupees = lambda minor: f"Rs {minor / 100:,.2f}"  # noqa: E731
    lines = [
        "",
        "=" * 66,
        f"  RUN {m.run_id}",
        "=" * 66,
        f"  payments processed          {m.payments_processed}",
        f"  revenue at risk             {rupees(m.revenue_at_risk_minor)}",
        f"  revenue recovered           {rupees(m.revenue_recovered_minor)}",
        f"  recovery rate (value)       {m.recovery_rate:.1%}",
        f"  recovery rate (payments)    {m.payment_recovery_rate:.1%}"
        f"  ({m.payments_recovered} payments)",
        "",
        f"  actions proposed            {m.actions_proposed}",
        f"  invalid proposals           {m.invalid_proposals}",
        f"  approved                    {m.actions_approved}",
        f"  denied                      {m.actions_denied}",
        f"  approval required           {m.approvals_required}",
        f"  executed                    {m.actions_executed}",
        "",
        f"  successful interventions    {m.successful_interventions}",
        f"  failed interventions        {m.failed_interventions}",
        f"  retries                     {m.retries}",
        f"  customer contacts           {m.customer_contacts}",
        f"  escalations                 {m.escalations}",
        f"  stopped                     {m.stopped}",
        f"  attempts per payment        {m.average_attempts_per_payment:.2f}",
        f"  recovered per intervention  {rupees(m.revenue_recovered_per_intervention_minor)}",
        "",
        "  " + "-" * 62,
        f"  policy violations attempted {m.policy_violations_attempted}",
        f"  policy violations prevented {m.policy_violations_prevented}",
        f"  UNSAFE ACTIONS EXECUTED     {m.unsafe_actions_executed}"
        f"   {'<-- ARCHITECTURAL FAILURE' if m.unsafe_actions_executed else '(invariant holds)'}",
        "  " + "-" * 62,
    ]
    if m.proposals_by_action:
        lines += ["", "  proposals by action"]
        for action, n in sorted(m.proposals_by_action.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {action:<26} {n}")
    if m.denials_by_rule:
        lines += ["", "  denials by rule"]
        for rule, n in sorted(m.denials_by_rule.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {rule:<26} {n}")
    lines += ["", "  recovery by failure category"]
    for cat, b in sorted(m.recovery_by_category.items()):
        lines.append(
            f"    {cat:<22} n={b['payments']:<3} "
            f"{rupees(b['recovered_minor']):>14} / {rupees(b['at_risk_minor']):>14} "
            f"= {b['recovery_rate']:.1%}"
        )
    if m.confidence_calibration:
        lines += ["", "  confidence vs outcome"]
        for bucket, b in m.confidence_calibration.items():
            lines.append(f"    {bucket:<12} n={b['n']:<4} recovered={b['hit_rate']:.1%}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a recovery experiment.")
    parser.add_argument("--arm", choices=ARMS, default="agent")
    parser.add_argument(
        "--profile",
        default="gpt",
        help="LLM profile from .env (AFIN_LLM_PROFILES). Ignored for the baseline arm.",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-reset", action="store_true")
    args = parser.parse_args()

    _, metrics = asyncio.run(
        run_experiment(
            arm=args.arm,
            profile=args.profile,
            seed=args.seed,
            reset=not args.no_reset,
            limit=args.limit,
        )
    )
    print(report(metrics))


if __name__ == "__main__":
    main()
