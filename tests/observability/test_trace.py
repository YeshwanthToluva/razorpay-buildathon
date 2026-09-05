"""Traces record what ran; they must never change what runs."""

from __future__ import annotations

import asyncio

import pytest

from afin.domain.enums import ActionType, ExecutionResult
from afin.domain.models import ProposedAction, ProviderOutcome
from afin.observability.instrument import TracedProvider, TracedReasoner
from afin.observability.trace import MAX_VALUE_CHARS, Tracer
from afin.policy.authorization import authorize
from tests.conftest import NOW, make_customer, make_payment
from tests.policy.conftest import request as policy_request


class StubReasoner:
    name, model = "stub", "test-model"

    async def propose(self, payment, customer, now, feedback=(), cycle=1,
                      max_cycles=4, attempts=()):
        return ProposedAction(
            action=ActionType.RETRY_PAYMENT, payment_id=payment.id,
            diagnosis="d", reasoning_summary="r", confidence=0.75,
            raw_json='{"action":"RETRY_PAYMENT"}',
        )


class StubProvider:
    name = "stub-provider"

    def execute(self, authorized, payment, now):
        return ProviderOutcome(ExecutionResult.SUCCESS, 5000, None, "ref", "ok")


def test_a_span_records_timing_inputs_and_outputs():
    t = Tracer(run_id="r")
    with t.span("x", "model", payment_id="pay_0001", cycle=2, model="m") as s:
        s.input = {"a": 1}
        s.output = {"b": 2}
    span = t.spans[0]
    assert span.duration_ms is not None and span.duration_ms >= 0
    assert span.input == {"a": 1} and span.output == {"b": 2}
    assert span.payment_id == "pay_0001" and span.cycle == 2
    assert span.ok


def test_spans_nest():
    t = Tracer(run_id="r")
    with t.span("outer", "orchestration"):
        with t.span("inner", "model"):
            pass
    outer, inner = t.spans
    assert outer.parent_id is None
    assert inner.parent_id == outer.span_id


def test_a_failing_call_is_recorded_and_still_raises():
    """A trace must not swallow the error it is recording."""
    t = Tracer(run_id="r")
    with pytest.raises(ValueError):
        with t.span("boom", "tool"):
            raise ValueError("nope")
    assert t.spans[0].ok is False
    assert "nope" in t.spans[0].error


def test_long_values_are_clipped_not_dropped():
    t = Tracer(run_id="r")
    with t.span("x", "model") as s:
        s.input = {"prompt": "y" * (MAX_VALUE_CHARS + 500)}
    v = t.spans[0].input["prompt"]
    assert len(v) < MAX_VALUE_CHARS + 200
    assert "more chars" in v


def test_a_watcher_cannot_break_the_run():
    def explode(_span):
        raise RuntimeError("watcher failed")

    t = Tracer(run_id="r", on_span=explode)
    with t.span("x", "model"):
        pass
    assert len(t.spans) == 1


def test_tracing_a_reasoner_does_not_change_its_proposal():
    payment, customer = make_payment(), make_customer()
    plain = asyncio.run(StubReasoner().propose(payment, customer, NOW))
    t = Tracer(run_id="r")
    traced = asyncio.run(TracedReasoner(StubReasoner(), t).propose(payment, customer, NOW))

    assert traced == plain
    span = t.spans[0]
    assert span.kind == "model"
    assert span.output["action"] == "RETRY_PAYMENT"
    assert span.output["confidence"] == 0.75
    assert span.output["raw_response"] == '{"action":"RETRY_PAYMENT"}'


def test_tracing_a_provider_does_not_change_its_outcome():
    payment = make_payment()
    _, auth = authorize(policy_request(payment=payment))
    assert auth is not None
    t = Tracer(run_id="r")
    outcome = TracedProvider(StubProvider(), t).execute(auth, payment, NOW)

    assert outcome.amount_recovered_minor == 5000
    span = t.spans[0]
    assert span.kind == "provider"
    assert span.input["action"] == "RETRY_PAYMENT"
    assert span.output["recovered_minor"] == 5000


def test_the_summary_totals_by_kind():
    t = Tracer(run_id="r")
    with t.span("a", "model"):
        pass
    with t.span("b", "tool"):
        pass
    with t.span("c", "model"):
        pass
    s = t.summary()
    assert s["spans"] == 3
    assert s["by_kind"]["model"]["calls"] == 2
    assert s["by_kind"]["tool"]["calls"] == 1


def test_chain_of_thought_is_not_among_the_recorded_fields():
    """A model span records the parsed answer, never private reasoning."""
    t = Tracer(run_id="r")
    asyncio.run(TracedReasoner(StubReasoner(), t).propose(make_payment(), make_customer(), NOW))
    recorded = set(t.spans[0].output)
    assert "reasoning_content" not in recorded
    assert recorded <= {
        "action", "in_action_space", "confidence", "diagnosis",
        "reasoning_summary", "raw_response",
    }
