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
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Literal

sys.path.insert(0, "src")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
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
from afin.observability import trace as tracing
from afin.observability.instrument import (
    TracedNotifier,
    TracedProvider,
    TracedReasoner,
)
from afin.simulator.live_email import LiveEmailProvider, _rupees
from afin.simulator.razorpay_sim import RazorpaySimulator
from afin.tools import paylink
from afin.tools.notify import Message, build_notifier

PAY_BASE = os.environ.get("AFIN_PAY_BASE_URL", "http://localhost:8000").rstrip("/")

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
    #: "simulated" lets the simulator decide whether a contacted customer pays.
    #: "email" sends a real message with a real link and waits for a person.
    channel: Literal["simulated", "email"] = "simulated"


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

        tracer = tracing.Tracer(
            run_id=run_id,
            on_span=lambda sp: queue.put_nowait({
                "event_type": "SPAN", "span_id": sp.span_id, "parent_id": sp.parent_id,
                "name": sp.name, "kind": sp.kind, "duration_ms": sp.duration_ms,
                "payment_id": sp.payment_id, "cycle": sp.cycle,
                "attributes": sp.attributes, "input": sp.input, "output": sp.output,
                "error": sp.error,
            }),
        )
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

        redirected_from = None
        demo_to = os.environ.get("AFIN_DEMO_RECIPIENT", "").strip()
        if req.channel == "email" and demo_to:
            # Synthetic customers have @synthetic.invalid addresses, which the
            # allowlist rightly refuses. Substitute the demo address BEFORE
            # policy sees the case, so the engine checks the address that will
            # actually receive the mail rather than one we intend to replace
            # later. Announced, never silent.
            import dataclasses

            redirected_from = customer.email
            customer = dataclasses.replace(customer, email=demo_to)
            await queue.put({
                "event_type": "DEMO_REDIRECT", "payment_id": payment.id,
                "from": redirected_from, "to": demo_to,
                "detail": "this synthetic customer's mail is delivered to the demo address",
            })

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

        live = req.channel == "email"
        import dataclasses as _dc

        cfg = (
            _dc.replace(DEFAULT_POLICY_CONFIG, contact_is_the_payment_rail=True)
            if live else DEFAULT_POLICY_CONFIG
        )
        if live:
            def announce(link, pay):
                queue.put_nowait({
                    "event_type": "LINK_SENT", "payment_id": pay.id,
                    "recipient": link.recipient,
                    "url": f"{PAY_BASE}/pay/{link.token}",
                    "amount_minor": link.amount_minor,
                })

            provider = LiveEmailProvider(
                notifier=TracedNotifier(build_notifier(), tracer),
                run_id=run_id, dataset_version=dataset,
                pay_base_url=PAY_BASE,
                demo_recipient=customer.email, redirected_from=redirected_from,
                simulator=RazorpaySimulator(seed=DEFAULT_SEED), on_link=announce,
            )
        else:
            provider = RazorpaySimulator(seed=DEFAULT_SEED)

        orch = Orchestrator(
            engine=ENGINE, reasoner=TracedReasoner(reasoner, tracer),
            provider=TracedProvider(provider, tracer),
            ledger=ledger, config=cfg, now=EPOCH,
            dataset_version=dataset, observer=observe,
            # One cycle in email mode: the case pauses on the customer, it does
            # not keep messaging them while they decide.
            max_cycles=1 if live else 4,
        )
        with tracer.span("run.case", "orchestration", payment_id=payment.id,
                         scenario=payment.scenario_tag, channel=req.channel):
            result = await orch.run_case(payment, customer)
        tracer.save()
        ledger.close_run()
        if live:
            # Hold the stream open while the customer decides.
            token = None
            for f in sorted(paylink.STORE.glob("*.json")):
                l = paylink.load(f.stem)
                if l and l.run_id == run_id:
                    token = l.token
            if token:
                await queue.put({"event_type": "AWAITING_CUSTOMER",
                                 "payment_id": payment.id,
                                 "url": f"{PAY_BASE}/pay/{token}"})
                for _ in range(600):          # up to ten minutes
                    await asyncio.sleep(1)
                    l = paylink.load(token)
                    if l and l.settled:
                        fresh = next(
                            (c for c in load_cases(ENGINE, dataset) if c[0].id == payment.id),
                            None,
                        )
                        got = fresh[0].recovered_amount_minor if fresh else l.amount_minor
                        await queue.put({
                            "event_type": "PAYMENT_RECEIVED", "payment_id": payment.id,
                            "revenue_recovered_minor": got,
                            "detail": "the customer settled the payment link",
                        })
                        result.recovered_minor = got
                        if fresh:
                            result.payment = fresh[0]
                        break

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
            "trace": tracer.summary(),
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



# --------------------------------------------------------------------------
# The customer's side of the loop
# --------------------------------------------------------------------------

_PAY_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Settle {invoice}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600&display=swap">
<style>
:root{{--bg:#f4f6f5;--surface:#fff;--line:#d5dbd9;--ink:#14201e;--muted:#65756f;
--accent:#0d7d78;--good:#2f7a4f;--good-soft:#dcefe2}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:'IBM Plex Sans',system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:8px;max-width:430px;
width:100%;padding:30px}}
.brand{{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.09em;
text-transform:uppercase;color:var(--accent)}}
h1{{font-size:19px;margin:12px 0 4px;letter-spacing:-.01em}}
.amt{{font-family:'IBM Plex Mono',monospace;font-size:38px;font-weight:600;margin:18px 0 6px;
letter-spacing:-.02em}}
.rows{{margin:20px 0;border-top:1px solid var(--line)}}
.row{{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--line);
font-family:'IBM Plex Mono',monospace;font-size:12.5px}}
.row span:first-child{{color:var(--muted)}}
button{{width:100%;font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:600;
padding:13px;border-radius:6px;border:1px solid var(--accent);background:var(--accent);color:#fff;
cursor:pointer}}
button:hover{{filter:brightness(1.07)}} button[disabled]{{opacity:.55;cursor:progress}}
.note{{color:var(--muted);font-size:11.5px;margin-top:16px;line-height:1.5}}
.done{{background:var(--good-soft);border:1px solid var(--good);border-radius:6px;padding:18px;
text-align:center}}
.done .t{{font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:600;color:var(--good)}}
.done .d{{font-size:13px;color:var(--ink);margin-top:6px}}
</style></head><body><div class="card">
<div class="brand">Secure payment · demo</div>
<h1>Settle invoice {invoice}</h1>
<div class="amt">{amount}</div>
<div class="rows">
  <div class="row"><span>invoice</span><span>{invoice}</span></div>
  <div class="row"><span>payment</span><span>{payment_id}</span></div>
  <div class="row"><span>billed to</span><span>{recipient}</span></div>
</div>
<div id="area">{area}</div>
<p class="note">This page settles a synthetic case in a recovery experiment.
No card is collected and no real money moves. Clicking Pay tells the agent the
customer paid, and it closes the case from there.</p>
</div>
<script>
const btn = document.getElementById('pay');
if(btn) btn.addEventListener('click', async () => {{
  btn.disabled = true; btn.textContent = 'Processing…';
  try{{
    const r = await fetch('/api/pay/{token}', {{method:'POST'}});
    const d = await r.json();
    document.getElementById('area').innerHTML =
      '<div class="done"><div class="t">Payment received</div><div class="d">' +
      (d.already_settled ? 'This invoice was already settled.'
                         : 'Thank you. ' + d.recovered + ' has been collected and a receipt is on its way.') +
      '</div></div>';
  }}catch(e){{
    btn.disabled = false; btn.textContent = 'Try again';
  }}
}});
</script></body></html>"""


@app.get("/pay/{token}", response_class=HTMLResponse)
def pay_page(token: str) -> HTMLResponse:
    link = paylink.load(token)
    if link is None:
        return HTMLResponse("<p>This payment link is not valid.</p>", status_code=404)
    area = (
        '<div class="done"><div class="t">Already settled</div>'
        '<div class="d">This invoice has been paid.</div></div>'
        if link.settled
        else f'<button id="pay">Pay {_rupees(link.amount_minor)}</button>'
    )
    return HTMLResponse(
        _PAY_PAGE.format(
            invoice=link.invoice_id, amount=_rupees(link.amount_minor),
            payment_id=link.payment_id, recipient=link.recipient,
            area=area, token=token,
        )
    )


@app.post("/api/pay/{token}")
def settle(token: str) -> dict:
    """The customer paid. Record it the same way any other collection is recorded.

    The settlement becomes a ProviderOutcome and goes through the same reducer
    and the same append-only ledger as a simulated collection, so a recovery
    driven by a person clicking Pay is auditable exactly like any other.
    """
    from afin.domain.enums import ActionType, ExecutionResult
    from afin.domain.models import ProviderOutcome
    from afin.domain.transitions import apply_outcome, consequence
    from afin.db.repository import load_cases, persist_payment
    from afin.audit.ledger import AuditLedger, EventType, observed_state

    link = paylink.load(token)
    if link is None:
        return {"ok": False, "error": "unknown link"}
    if link.settled:
        return {"ok": True, "already_settled": True,
                "recovered": _rupees(link.amount_minor)}

    pairs = load_cases(ENGINE, link.dataset_version)
    found = next((c for c in pairs if c[0].id == link.payment_id), None)
    if found is None:
        return {"ok": False, "error": "payment not found"}
    payment, customer = found
    if payment.is_terminal:
        paylink.mark_settled(token)
        return {"ok": True, "already_settled": True,
                "recovered": _rupees(payment.recovered_amount_minor)}

    outcome = ProviderOutcome(
        result=ExecutionResult.SUCCESS,
        amount_recovered_minor=link.amount_minor,
        failure_code=None,
        provider_ref=f"paylink_{token}",
        detail="customer settled the payment link",
    )
    after = apply_outcome(payment, ActionType.GENERATE_PAYMENT_LINK, outcome, EPOCH)
    persist_payment(ENGINE, after, link.dataset_version)
    paylink.mark_settled(token)

    ledger = AuditLedger(engine=ENGINE, run_id=link.run_id)
    ledger.record(
        payment_id=payment.id, cycle=99, event_type=EventType.ACTION_EXECUTED,
        observed_state_json=observed_state(payment, customer),
        executed_action=ActionType.GENERATE_PAYMENT_LINK.value,
        execution_result=ExecutionResult.SUCCESS.value,
        policy_decision="ALLOW", policy_rule="PERMITTED",
        revenue_recovered_minor=link.amount_minor,
        resulting_payment_state=after.payment_state.value,
        resulting_recovery_state=after.recovery_state.value,
    )

    receipt = build_notifier().send(Message(
        to=link.recipient,
        subject=f"Payment received — {_rupees(link.amount_minor)} for {link.invoice_id}",
        body_html=(
            "<div style='font-family:system-ui,-apple-system,sans-serif;max-width:540px;"
            "color:#14201e;line-height:1.55'>"
            f"<p>Thank you. We have received <strong>{_rupees(link.amount_minor)}</strong> "
            f"for invoice <code>{link.invoice_id}</code>.</p>"
            f"<p>{consequence(payment.risk_type, True)}</p>"
            "<hr style='border:none;border-top:1px solid #d5dbd9;margin:22px 0'>"
            "<p style='color:#65756f;font-size:12.5px'>This receipt was sent by the same "
            "autonomous recovery agent that contacted you, after it observed the payment "
            "and closed the case. Synthetic test data — no real money was taken.</p></div>"
        ),
    ))
    return {
        "ok": True, "already_settled": False,
        "recovered": _rupees(link.amount_minor),
        "payment_state": after.payment_state.value,
        "recovery_state": after.recovery_state.value,
        "receipt_sent": receipt.delivered,
    }


@app.get("/api/runs/{run_id}/trace")
def run_trace(run_id: str) -> dict:
    """Every call made during a run: model, policy, provider, tool.

    Separate from the audit ledger on purpose. The ledger is evidence about
    decisions and stays small; this is diagnostic and verbose, and records
    inputs, outputs and timings for each span.
    """
    data = tracing.load(run_id)
    if data is None:
        return {"ok": False, "error": f"no trace recorded for {run_id}"}
    return {"ok": True, **data}


@app.get("/api/runs")
def list_runs(limit: int = 20) -> list[dict]:
    """Recent live runs, newest first, with whether a trace was captured."""
    import os as _os

    if not tracing.STORE.exists():
        return []
    files = sorted(tracing.STORE.glob("*.json"),
                   key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    out = []
    for f in files:
        data = tracing.load(f.stem) or {}
        out.append({"run_id": f.stem, "summary": data.get("summary", {}),
                    "recorded_at": _os.path.getmtime(f)})
    return out
