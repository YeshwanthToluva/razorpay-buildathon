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
from afin.metrics.recovery import compute, run_is_valid
from afin.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from afin.simulator.razorpay_sim import RazorpaySimulator

ARMS = ("baseline", "agent")


def build_reasoner(
    arm: str, profile: str, prompt: str = "control", outcome_feedback: bool = False
):
    if arm == "baseline":
        return RuleBasedReasoner(), None
    from afin.agent.llm import LLMReasoner

    r = LLMReasoner(profile=profile, prompt=prompt, outcome_feedback=outcome_feedback)
    return r, r.prompt_version


async def run_experiment(
    arm: str = "agent",
    profile: str = "gpt",
    prompt: str = "control",
    outcome_feedback: bool = False,
    seed: int = DEFAULT_SEED,
    config: PolicyConfig = DEFAULT_POLICY_CONFIG,
    now: datetime = EPOCH,
    limit: int | None = None,
    concurrency: int = 1,
) -> tuple[str, object]:
    engine = get_engine()
    create_schema(engine)

    fb = "fb" if outcome_feedback else "nofb"
    tag = arm if arm == "baseline" else f"{arm}-{profile}-{prompt}-{fb}"
    run_id = f"{tag}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"

    # Each run gets its own private copy of the dataset, generated from the same
    # seed so the content is identical. Arms therefore never share mutable
    # financial state and can run concurrently against different providers --
    # which matters when a free-tier rate limit makes a sequential sweep take
    # an hour per model.
    dataset_version = f"{DATASET_VERSION}:{run_id}"
    load_into(engine, generate(seed=seed, version=dataset_version))
    reasoner, prompt_version = build_reasoner(arm, profile, prompt, outcome_feedback)
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
                "outcome_feedback": getattr(reasoner, "outcome_feedback", None),
            }
        ),
        prompt_version=prompt_version,
        policy_version=config.version,
        policy_fingerprint=config.fingerprint(),
        dataset_version=dataset_version,
        random_seed=seed,
    )

    orchestrator = Orchestrator(
        engine=engine,
        reasoner=reasoner,
        provider=RazorpaySimulator(seed=seed),
        ledger=ledger,
        config=config,
        now=now,
        dataset_version=dataset_version,
    )

    cases = load_cases(engine, dataset_version)
    if limit:
        cases = cases[:limit]

    print(
        f"run {run_id}: {len(cases)} payments, reasoner={reasoner.name}, "
        f"concurrency={concurrency}"
    )

    # Cases are independent -- each owns its own payment row -- so they may run
    # concurrently. This matters under a rate-limited provider: serially, one
    # case sitting in backoff stalls the whole sweep and a 50-payment arm takes
    # hours. Overlapping the waits recovers most of that without issuing a
    # single extra request.
    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async def run_one(payment, customer):
        nonlocal done
        async with semaphore:
            result = await orchestrator.run_case(payment, customer)
        done += 1
        flag = "*" if result.recovered_minor else " "
        print(
            f"  [{done:>2}/{len(cases)}] {payment.id} {payment.scenario_tag:<26} "
            f"{result.payment.payment_state.value:<11} "
            f"{flag}Rs{result.recovered_minor / 100:>10,.2f}  "
            f"proposed={result.proposals} denied={result.denied} exec={result.executed}"
        )
        for err in result.errors:
            print(f"        ! {err}")
        return result

    await asyncio.gather(*(run_one(p, c) for p, c in cases))

    retries = getattr(reasoner, "retries", 0)
    if retries:
        print(f"  ({retries} transient provider faults absorbed by retry)")
    ledger.close_run()
    metrics = compute(engine, run_id, dataset_version)

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
        f"  context fidelity            {m.context_fidelity_rate:.1%}"
        f"  ({m.context_claims_supported}/{m.context_claims_total} supported, "
        f"{m.context_claims_contradicted} contradicted, {m.context_claims_missing} missing)",
        f"  provider errors             {m.agent_errors}",
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
    valid, why = run_is_valid(m)
    if not valid:
        lines += [
            "",
            "  " + "!" * 62,
            f"  RUN NOT VALID AS AN EXPERIMENT: {why}",
            "  Numbers above describe an outage, not agent behaviour.",
            "  " + "!" * 62,
        ]
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a recovery experiment.")
    parser.add_argument("--arm", choices=ARMS, default="agent")
    parser.add_argument(
        "--outcome-feedback",
        action="store_true",
        help="Experiment 002g: report each executed action's factual result to "
        "the next cycle.",
    )
    parser.add_argument(
        "--prompt",
        choices=("control", "treatment"),
        default="control",
        help="Prompt arm for experiment 002e. Ignored for the baseline arm.",
    )
    parser.add_argument(
        "--profile",
        default="gpt",
        help="LLM profile from .env (AFIN_LLM_PROFILES). Ignored for the baseline arm.",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=6,
        help="Cases in flight at once. Overlaps provider backoff; it does "
        "not increase the number of requests issued.",
    )
    args = parser.parse_args()

    _, metrics = asyncio.run(
        run_experiment(
            arm=args.arm,
            profile=args.profile,
            prompt=args.prompt,
            outcome_feedback=args.outcome_feedback,
            seed=args.seed,
            limit=args.limit,
            concurrency=max(1, args.concurrency),
        )
    )
    print(report(metrics))


if __name__ == "__main__":
    main()
