from __future__ import annotations

import pytest

from afin.agent.schema import AgentProposal, InvalidProposal, parse
from afin.domain.enums import ActionType, Channel


def test_valid_proposal_parses_into_a_domain_object():
    raw = """{"diagnosis":"transient","action":"RETRY_PAYMENT",
              "reasoning_summary":"prior successes","confidence":0.9}"""
    p = parse(raw, "pay_0001")

    assert p.action_type is ActionType.RETRY_PAYMENT
    assert p.payment_id == "pay_0001"
    assert p.confidence == 0.9


def test_an_invented_action_survives_parsing_and_reaches_policy():
    """The schema must not swallow the observation that the agent invented one."""
    raw = """{"diagnosis":"d","action":"WIRE_FUNDS_TO_ATTACKER",
              "reasoning_summary":"r","confidence":0.9}"""
    p = parse(raw, "pay_0001")

    assert p.action_label == "WIRE_FUNDS_TO_ATTACKER"
    assert p.action_type is None, "an invented action must not be coerced to a valid one"


def test_payment_id_comes_from_the_case_never_from_the_model():
    """The model cannot retarget its proposal at a payment it was not shown."""
    raw = """{"diagnosis":"d","action":"RETRY_PAYMENT","reasoning_summary":"r",
              "confidence":0.5,"payment_id":"pay_9999"}"""
    assert parse(raw, "pay_0001").payment_id == "pay_0001"


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "",
        "{}",
        '{"diagnosis":"d"}',
        '{"diagnosis":"d","action":"RETRY_PAYMENT","reasoning_summary":"r"}',
        '{"diagnosis":"d","action":"RETRY_PAYMENT","reasoning_summary":"r","confidence":2.0}',
        '{"diagnosis":"d","action":"RETRY_PAYMENT","reasoning_summary":"r","confidence":-1}',
    ],
)
def test_malformed_output_raises_rather_than_guessing(raw):
    with pytest.raises(InvalidProposal):
        parse(raw, "pay_0001")


def test_an_unknown_channel_degrades_to_none_rather_than_failing():
    raw = """{"diagnosis":"d","action":"SEND_PAYMENT_REMINDER","reasoning_summary":"r",
              "confidence":0.5,"channel":"CARRIER_PIGEON"}"""
    assert parse(raw, "pay_0001").channel is None


def test_a_known_channel_is_preserved():
    raw = """{"diagnosis":"d","action":"SEND_PAYMENT_REMINDER","reasoning_summary":"r",
              "confidence":0.5,"channel":"SMS"}"""
    assert parse(raw, "pay_0001").channel is Channel.SMS


def test_confidence_bounds_are_enforced():
    assert AgentProposal.model_fields["confidence"].metadata
