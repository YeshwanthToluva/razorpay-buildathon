"""The three risk types in the brief differ in what is mechanically possible."""

from __future__ import annotations

import pytest

from afin.domain.enums import ActionType, FailureCategory, RiskType
from afin.policy.decisions import PolicyRule
from afin.policy.engine import evaluate
from tests.conftest import make_payment
from tests.policy.conftest import propose, request


def abandoned(**kw):
    return make_payment(
        risk_type=RiskType.CHECKOUT_ABANDONMENT,
        failure_category=FailureCategory.CHECKOUT_DROPPED,
        **kw,
    )


def receivable(category=FailureCategory.INVOICE_OVERDUE, **kw):
    return make_payment(
        risk_type=RiskType.OVERDUE_RECEIVABLE, failure_category=category, **kw
    )


# --- checkout abandonment -------------------------------------------------

@pytest.mark.parametrize("action", [ActionType.RETRY_PAYMENT, ActionType.SCHEDULE_RETRY])
def test_an_abandoned_checkout_cannot_be_charged(action):
    """Nothing was ever authorised, so this is impossible, not merely unwise."""
    d = evaluate(
        request(
            proposal=propose(action=action, scheduled_delay_hours=24),
            payment=abandoned(),
        )
    )
    assert d.allowed is False
    assert d.policy is PolicyRule.RISK_TYPE_PRECONDITION


def test_an_abandoned_checkout_can_still_be_sent_a_link():
    """The remedy is the customer completing payment, so this must stay open."""
    d = evaluate(
        request(
            proposal=propose(action=ActionType.GENERATE_PAYMENT_LINK),
            payment=abandoned(),
        )
    )
    assert d.allowed is True


def test_an_opted_out_abandoned_checkout_cannot_be_contacted():
    from tests.conftest import make_customer

    d = evaluate(
        request(
            proposal=propose(action=ActionType.GENERATE_PAYMENT_LINK),
            payment=abandoned(),
            customer=make_customer(opted_out=True),
        )
    )
    assert d.allowed is False
    assert d.policy is PolicyRule.OPT_OUT_COMMUNICATION


# --- overdue receivables --------------------------------------------------

def test_an_overdue_invoice_with_a_mandate_can_be_collected():
    d = evaluate(request(payment=receivable()))
    assert d.allowed is True


@pytest.mark.parametrize("action", [ActionType.RETRY_PAYMENT, ActionType.SCHEDULE_RETRY])
def test_an_overdue_invoice_without_a_mandate_cannot_be_collected(action):
    d = evaluate(
        request(
            proposal=propose(action=action, scheduled_delay_hours=24),
            payment=receivable(category=FailureCategory.MANDATE_ABSENT),
        )
    )
    assert d.allowed is False
    assert d.policy is PolicyRule.RISK_TYPE_PRECONDITION


def test_an_invoice_without_a_mandate_can_still_be_sent_a_link():
    d = evaluate(
        request(
            proposal=propose(action=ActionType.GENERATE_PAYMENT_LINK),
            payment=receivable(category=FailureCategory.MANDATE_ABSENT),
        )
    )
    assert d.allowed is True


# --- the boundary is not weakened by the new risks ------------------------

def test_safety_rules_still_outrank_risk_type():
    """A disputed abandoned checkout reports the dispute, not the risk type."""
    d = evaluate(request(payment=abandoned(is_disputed=True)))
    assert d.policy is PolicyRule.DISPUTE_BLOCK


def test_stopping_and_escalating_remain_available_on_every_risk_type():
    for payment in (abandoned(), receivable(), make_payment()):
        for action in (ActionType.STOP_RECOVERY, ActionType.REQUEST_HUMAN_REVIEW):
            d = evaluate(request(proposal=propose(action=action), payment=payment))
            assert d.allowed is True


def test_no_charging_action_is_ever_allowed_without_an_instrument():
    """The invariant this rule exists for, swept over both instrument-less risks."""
    cases = [abandoned(), receivable(category=FailureCategory.MANDATE_ABSENT)]
    for payment in cases:
        for action in (ActionType.RETRY_PAYMENT, ActionType.SCHEDULE_RETRY):
            for delay in (None, 1, 24, 72):
                d = evaluate(
                    request(
                        proposal=propose(action=action, scheduled_delay_hours=delay),
                        payment=payment,
                    )
                )
                assert d.allowed is False, f"{action} allowed on {payment.risk_type}"
