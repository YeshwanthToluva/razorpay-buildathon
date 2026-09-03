"""Rule ordering. The ledger must attribute a block to its most severe cause."""

from __future__ import annotations

from datetime import timedelta

from afin.domain.enums import ActionType, CustomerRiskFlag, FailureCategory, RecoveryState
from afin.policy.decisions import PolicyRule
from afin.policy.engine import evaluate
from tests.conftest import NOW, make_customer, make_payment
from tests.policy.conftest import propose, request


def test_invented_action_outranks_every_other_condition():
    d = evaluate(request(proposal=propose(action="WIRE_FUNDS"), is_disputed=True, retry_count=99))
    assert d.policy is PolicyRule.UNSUPPORTED_ACTION


def test_terminal_state_outranks_dispute():
    d = evaluate(
        request(payment=make_payment(is_disputed=True, recovery_state=RecoveryState.STOPPED))
    )
    assert d.policy is PolicyRule.TERMINAL_STATE


def test_dispute_outranks_window_expiry():
    """Both apply; the dispute is the reason a human needs to see."""
    d = evaluate(
        request(is_disputed=True, window_expires_at=NOW - timedelta(days=1))
    )
    assert d.policy is PolicyRule.DISPUTE_BLOCK


def test_dispute_outranks_fraud():
    d = evaluate(
        request(is_disputed=True, failure_category=FailureCategory.FRAUD_SUSPECTED)
    )
    assert d.policy is PolicyRule.DISPUTE_BLOCK


def test_fraud_outranks_opt_out():
    d = evaluate(
        request(
            proposal=propose(action=ActionType.SEND_PAYMENT_REMINDER),
            customer=make_customer(opted_out=True, risk_flag=CustomerRiskFlag.FRAUD_WATCH),
        )
    )
    assert d.policy is PolicyRule.FRAUD_HOLD


def test_window_expiry_outranks_retry_limit():
    d = evaluate(request(retry_count=99, window_expires_at=NOW - timedelta(days=1)))
    assert d.policy is PolicyRule.RECOVERY_WINDOW_EXPIRED


def test_every_deny_outranks_high_value_approval():
    """A high-value disputed payment must read as blocked, not as awaiting sign-off."""
    d = evaluate(request(is_disputed=True, amount_minor=90_000_000))

    assert d.policy is PolicyRule.DISPUTE_BLOCK
    assert d.allowed is False


def test_decision_trace_records_every_rule_consulted():
    d = evaluate(request(retry_count=3))

    assert d.evaluated[0] is PolicyRule.UNSUPPORTED_ACTION
    assert d.evaluated[-1] is PolicyRule.MAX_RETRY_LIMIT
    assert PolicyRule.DISPUTE_BLOCK in d.evaluated
    assert PolicyRule.CONTACT_BUDGET not in d.evaluated, "trace must stop at the decision"


def test_allowed_decision_traces_the_full_rule_set():
    d = evaluate(request())

    assert d.policy is PolicyRule.PERMITTED
    assert PolicyRule.HIGH_VALUE_APPROVAL in d.evaluated
