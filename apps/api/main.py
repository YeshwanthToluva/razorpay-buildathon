"""Live recovery API — the same stack the experiments ran, exposed for a demo.

Nothing here re-implements a decision. It drives the real orchestrator, the real
policy engine, the real gateway and the real simulator, and streams what happens
as it happens. Two endpoints matter:

    POST /api/recover        run ONE at-risk payment end to end, streaming each
                             stage: detected -> proposed -> policy -> executed
    POST /api/policy/evaluate  submit any action, including one outside the
                             action space, and get the real policy verdict

    uvicorn apps.api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Literal

sys.path.insert(0, "src")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from afin.agent.orchestrator import Orchestrator
from afin.agent.rule_based import RuleBasedReasoner
from afin.audit.ledger import AuditLedger
from afin.db.engine import create_schema, get_engine
from afin.db.repository import load_cases
from afin.db.seed import DATASET_VERSION, DEFAULT_SEED, EPOCH, SCENARIOS, generate, load_into
from afin.domain.enums import ActionType, Channel, OWED_RISKS
from afin.domain.models import ProposedAction
from afin.domain.transitions import consequence
from afin.policy.authorization import authorize
from afin.policy.config import DEFAULT_POLICY_CONFIG
from afin.policy.engine import PolicyRequest
from afin.simulator.razorpay_sim import RazorpaySimulator

app = FastAPI(title="Revenue Recovery Lab — live API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

ENGINE = get_engine()
create_schema(ENGINE)


# --------------------------------------------------------------------------


class RecoverRequest(BaseModel):
    scenario: str = Field(
        default="transient_bank_failure",
        description="Which at-risk situation to put through the loop.",
    )
    reasoner: Literal["rules", "llm"] = "rules"
    profile: str = "gpt"


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "policy": DEFAULT_POLICY_CONFIG.version,
            "fingerprint": DEFAULT_POLICY_CONFIG.fingerprint()}


#: What each rule is for, in the language a reviewer speaks. The order and the
#: identifiers come from the engine itself, so this cannot drift out of step
#: with what actually runs.
RULE_INTENT: dict[str, str] = {
    "UNSUPPORTED_ACTION": "The agent named an operation that does not exist. "
        "It cannot invent financial actions.",
    "PAYMENT_MISMATCH": "The agent tried to act on a case it was not given. "
        "No acting on another customer's payment.",
    "TERMINAL_STATE": "The case is already closed. Nothing further may happen to it.",
    "SAFETY_VALVE": "Stopping and asking for a human are always available, so a case "
        "can never be trapped with no legal exit.",
    "DISPUTE_BLOCK": "The payment is disputed. No automated recovery until it is resolved.",
    "FRAUD_HOLD": "There is a fraud signal. No automated recovery pending review.",
    "OPT_OUT_COMMUNICATION": "The customer opted out of contact, so we may not message "
        "them. It does not stop us charging a card they already authorised.",
    "RECOVERY_WINDOW_EXPIRED": "The window for recovering this has closed.",
    "MAX_RETRY_LIMIT": "The retry budget is spent. Repeated declines damage future "
        "acceptance with the issuer.",
    "RETRY_COOLDOWN": "Too soon after the last attempt. Back-to-back retries get refused.",
    "RISK_TYPE_PRECONDITION": "This kind of risk has no instrument to charge — an "
        "abandoned checkout never authorised one, and an invoice without a mandate "
        "cannot be collected automatically.",
    "ACTION_PRECONDITION": "The action cannot work here, for example re-presenting a "
        "card that has expired.",
    "CONTACT_BUDGET": "We have contacted this customer enough. More becomes harassment.",
    "HIGH_VALUE_APPROVAL": "The amount is above the ceiling for unattended action, so a "
        "person has to approve it.",
    "PERMITTED": "Nothing above objected, and the action is explicitly allowed.",
    "DEFAULT_DENY": "Nothing explicitly permitted it, so it is refused. Anything not "
        "allowed is denied.",
}


@app.get("/api/policy/rules")
def policy_rules() -> dict:
    """The rulebook, in the order the engine actually evaluates it.

    Read straight from afin.policy.engine.RULES rather than restated here, so
    the published rulebook cannot drift from the one that runs. First refusal
    wins, which is why the order matters: a payment that is both disputed and
    past its window reports the dispute.
    """
    from afin.policy.decisions import PolicyRule
    from afin.policy.engine import RULES, _EXPLICITLY_PERMITTED

    cfg = DEFAULT_POLICY_CONFIG
    ordered = [
        {
            "order": i,
            "rule": rule_id.value,
            "intent": RULE_INTENT.get(rule_id.value, ""),
            "outcome": "ALLOW" if rule_id is PolicyRule.SAFETY_VALVE else (
                "REQUIRE_APPROVAL" if rule_id is PolicyRule.HIGH_VALUE_APPROVAL else "DENY"
            ),
        }
        for i, (rule_id, _fn) in enumerate(RULES, start=1)
    ]
    ordered.append({
        "order": len(RULES) + 1, "rule": PolicyRule.PERMITTED.value,
        "intent": RULE_INTENT["PERMITTED"], "outcome": "ALLOW",
    })
    ordered.append({
        "order": len(RULES) + 2, "rule": PolicyRule.DEFAULT_DENY.value,
        "intent": RULE_INTENT["DEFAULT_DENY"], "outcome": "DENY",
    })

    return {
        "policy_version": cfg.version,
        "fingerprint": cfg.fingerprint(),
        "evaluation": "in order, first refusal wins; anything not explicitly "
                      "permitted is denied",
        "thresholds": {
            "max_retries": cfg.max_retries,
            "high_value_ceiling": f"\u20b9{cfg.high_value_threshold_minor / 100:,.0f}",
            "high_value_ceiling_minor": cfg.high_value_threshold_minor,
            "retry_cooldown_hours": cfg.retry_cooldown_hours,
            "max_contacts": cfg.max_contacts,
            "max_schedule_delay_hours": cfg.max_schedule_delay_hours,
        },
        "action_space": [a.value for a in ActionType],
        "explicitly_permitted": sorted(a.value for a in _EXPLICITLY_PERMITTED),
        "always_available": ["REQUEST_HUMAN_REVIEW", "STOP_RECOVERY"],
        "rule_count": len(ordered),
        "rules": ordered,
    }


@app.get("/api/mechanics")
def mechanics() -> dict:
    """The simulator's published odds, so no outcome in the demo looks arbitrary.

    Every execution is an independent draw against these numbers, keyed on
    (seed, payment_id, action, attempt) -- which is why the same action can be
    unpaid on one attempt and paid on the next without anything else changing.
    """
    from afin.simulator.razorpay_sim import (
        _LINK_SUCCESS, _REMINDER_BY_RISK, _REMINDER_SUCCESS, _RETRY_SUCCESS,
        _SCHEDULE_BONUS,
    )

    return {
        "RETRY_PAYMENT": {k.value: v for k, v in _RETRY_SUCCESS.items()},
        "SCHEDULE_RETRY": {
            k.value: min(v + _SCHEDULE_BONUS.get(k, 0.0), 1.0)
            for k, v in _RETRY_SUCCESS.items()
        },
        "GENERATE_PAYMENT_LINK": {k.value: v for k, v in _LINK_SUCCESS.items()},
        "SEND_PAYMENT_REMINDER": {
            "default": _REMINDER_SUCCESS,
            **{k.value: v for k, v in _REMINDER_BY_RISK.items()},
        },
        "note": "Probability that one attempt of this action collects the money.",
    }


@app.get("/api/scenarios")
def scenarios() -> list[dict]:
    """The at-risk situations available to demonstrate."""
    return [
        {"tag": s.tag, "failure_category": s.category.value, "count": s.count,
         "disputed": s.is_disputed, "opted_out": s.opted_out}
        for s in SCENARIOS
    ]


def _build_reasoner(req: RecoverRequest):
    if req.reasoner == "rules":
        return RuleBasedReasoner(), None
    from afin.agent.llm import LLMReasoner

    r = LLMReasoner(profile=req.profile, prompt="treatment")
    return r, r.prompt_version


@app.post("/api/recover")
async def recover(req: RecoverRequest) -> StreamingResponse:
    """Detect one at-risk payment, decide, and execute a bounded recovery.

    Streams server-sent events so a viewer watches the loop rather than a
    finished result.
    """
    queue: asyncio.Queue = asyncio.Queue()

    def observe(payload: dict) -> None:
        # The orchestrator runs on this same loop, so enqueue directly.
        # Deferring via call_soon_threadsafe reorders the stream: the run's own
        # completion event lands before the stages that produced it.
        queue.put_nowait(payload)

    async def run() -> None:
        run_id = f"live-{datetime.now(timezone.utc):%H%M%S}-{uuid.uuid4().hex[:4]}"
        dataset = f"{DATASET_VERSION}:{run_id}"
        load_into(ENGINE, generate(seed=DEFAULT_SEED, version=dataset))

        reasoner, prompt_version = _build_reasoner(req)
        ledger = AuditLedger(engine=ENGINE, run_id=run_id)
        ledger.open_run(
            started_at=datetime.now(timezone.utc), experiment="live", autonomy_level=2,
            reasoner=reasoner.name, model=reasoner.model, prompt_version=prompt_version,
            policy_version=DEFAULT_POLICY_CONFIG.version,
            policy_fingerprint=DEFAULT_POLICY_CONFIG.fingerprint(),
            dataset_version=dataset, random_seed=DEFAULT_SEED,
        )

        cases = load_cases(ENGINE, dataset)
        picked = next((c for c in cases if c[0].scenario_tag == req.scenario), cases[0])
        payment, customer = picked

        await queue.put({
            "event_type": "RISK_DETECTED", "payment_id": payment.id,
            "scenario": payment.scenario_tag, "amount_minor": payment.amount_minor,
            "failure_category": payment.failure_category.value,
            "risk_type": payment.risk_type.value,
            "is_owed": payment.risk_type in OWED_RISKS,
            "retry_count": payment.retry_count, "is_disputed": payment.is_disputed,
            "opted_out": customer.opted_out,
            "prior_successful_payments": customer.prior_successful_payments,
            "run_id": run_id, "reasoner": reasoner.name,
        })

        orch = Orchestrator(
            engine=ENGINE, reasoner=reasoner, provider=RazorpaySimulator(seed=DEFAULT_SEED),
            ledger=ledger, config=DEFAULT_POLICY_CONFIG, now=EPOCH,
            dataset_version=dataset, observer=observe,
        )
        result = await orch.run_case(payment, customer)
        ledger.close_run()
        await queue.put({
            "event_type": "RUN_COMPLETE", "payment_id": payment.id,
            "recovered_minor": result.recovered_minor,
            "amount_minor": payment.amount_minor,
            "final_payment_state": result.payment.payment_state.value,
            "final_recovery_state": result.payment.recovery_state.value,
            "proposals": result.proposals, "denied": result.denied,
            "executed": result.executed, "unsafe_executed": result.unsafe_executed,
            "risk_type": payment.risk_type.value,
            "is_owed": payment.risk_type in OWED_RISKS,
            "consequence": consequence(
                payment.risk_type, result.recovered_minor > 0
            ),
        })
        await queue.put(None)

    async def stream():
        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item, default=str)}\n\n"
                await asyncio.sleep(0.35)   # paced so a viewer can follow it
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'event_type':'ERROR','error':str(exc)})}\n\n"
        finally:
            await task
    return StreamingResponse(stream(), media_type="text/event-stream")


# --------------------------------------------------------------------------


class PolicyProbe(BaseModel):
    """An action to put in front of the real policy engine. Anything goes."""

    action: str = "RETRY_PAYMENT"
    scenario: str = "transient_bank_failure"
    scheduled_delay_hours: int | None = None
    diagnosis: str = "manual probe"
    reasoning_summary: str = "submitted from the attack console"
    confidence: float = 1.0


@app.post("/api/policy/evaluate")
def evaluate_action(probe: PolicyProbe) -> dict:
    """Ask the real policy engine, and report whether authority was minted.

    This is the same `authorize` the orchestrator calls. Nothing is simulated
    for the demo's benefit: if it returns authorized=false, no AuthorizedAction
    exists, and no provider call is reachable.
    """
    ds = generate(seed=DEFAULT_SEED, version="probe")
    row = next((p for p in ds.payments if p["scenario_tag"] == probe.scenario), ds.payments[0])
    cust = next(c for c in ds.customers if c["id"] == row["customer_id"])

    from afin.db.repository import _to_customer, _to_payment

    payment = _to_payment(type("R", (), row)())
    customer = _to_customer(type("R", (), cust)())

    try:
        channel = Channel(probe.action) if False else None
    except ValueError:
        channel = None

    proposal = ProposedAction(
        action=probe.action.strip(),
        payment_id=payment.id,
        diagnosis=probe.diagnosis,
        reasoning_summary=probe.reasoning_summary,
        confidence=probe.confidence,
        scheduled_delay_hours=probe.scheduled_delay_hours,
        channel=channel,
    )
    decision, authorized = authorize(
        PolicyRequest(proposal=proposal, payment=payment, customer=customer,
                      now=EPOCH, config=DEFAULT_POLICY_CONFIG)
    )
    return {
        "submitted_action": probe.action,
        "in_action_space": proposal.action_type is not None,
        "known_actions": [a.value for a in ActionType],
        "decision": decision.decision.value,
        "allowed": decision.allowed,
        "rule": decision.policy.value,
        "reason": decision.reason,
        "risk_level": decision.risk_level.value,
        "rules_evaluated": [r.value for r in decision.evaluated],
        "authority_minted": authorized is not None,
        "would_reach_provider": authorized is not None,
        "context": {
            "payment_id": payment.id, "scenario": payment.scenario_tag,
            "amount_minor": payment.amount_minor,
            "failure_category": payment.failure_category.value,
            "retry_count": payment.retry_count, "is_disputed": payment.is_disputed,
            "opted_out": customer.opted_out,
        },
    }
