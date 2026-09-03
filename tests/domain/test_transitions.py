from __future__ import annotations

import pytest

from afin.domain.enums import (
    ActionType,
    ExecutionResult,
    PaymentState,
    RecoveryState,
)
from afin.domain.models import ProviderOutcome
from afin.domain.transitions import TerminalStateError, apply_outcome, exhaust
from tests.conftest import NOW, make_payment


def outcome(result=ExecutionResult.FAILURE, recovered=0, code=None) -> ProviderOutcome:
    return ProviderOutcome(
        result=result,
        amount_recovered_minor=recovered,
        failure_code=code,
        provider_ref="sim_ref",
        detail="test",
    )


def test_successful_retry_recovers_payment():
    p = make_payment(amount_minor=250_000)
    out = apply_outcome(p, ActionType.RETRY_PAYMENT, outcome(ExecutionResult.SUCCESS, 250_000), NOW)

    assert out.payment_state is PaymentState.RECOVERED
    assert out.recovery_state is RecoveryState.COMPLETED
    assert out.recovered_amount_minor == 250_000
    assert out.retry_count == 1
    assert out.is_terminal


def test_failed_retry_consumes_budget_but_keeps_case_open():
    p = make_payment(retry_count=1)
    out = apply_outcome(p, ActionType.RETRY_PAYMENT, outcome(code="BANK_DOWN"), NOW)

    assert out.retry_count == 2
    assert out.payment_state is PaymentState.FAILED
    assert out.recovery_state is RecoveryState.IN_PROGRESS
    assert not out.is_terminal


def test_rejected_attempt_does_not_consume_retry_budget():
    """A provider-side rejection is not an attempt against the instrument."""
    p = make_payment(retry_count=1)
    out = apply_outcome(p, ActionType.RETRY_PAYMENT, outcome(ExecutionResult.REJECTED), NOW)

    assert out.retry_count == 1
    assert out.last_attempt_at is None


def test_stop_recovery_writes_off_rather_than_recovering():
    out = apply_outcome(make_payment(), ActionType.STOP_RECOVERY, outcome(), NOW)

    assert out.payment_state is PaymentState.WRITTEN_OFF
    assert out.recovery_state is RecoveryState.STOPPED
    assert out.recovered_amount_minor == 0


def test_escalation_leaves_financial_state_untouched():
    p = make_payment()
    out = apply_outcome(p, ActionType.REQUEST_HUMAN_REVIEW, outcome(), NOW)

    assert out.payment_state is PaymentState.FAILED
    assert out.recovery_state is RecoveryState.ESCALATED
    assert out.recovered_amount_minor == p.recovered_amount_minor


def test_reminder_increments_contact_count_and_awaits_customer():
    out = apply_outcome(
        make_payment(), ActionType.SEND_PAYMENT_REMINDER, outcome(ExecutionResult.SUCCESS), NOW
    )

    assert out.contact_count == 1
    assert out.recovery_state is RecoveryState.AWAITING_CUSTOMER
    assert out.payment_state is PaymentState.FAILED


def test_payment_link_that_gets_paid_recovers_money():
    out = apply_outcome(
        make_payment(),
        ActionType.GENERATE_PAYMENT_LINK,
        outcome(ExecutionResult.SUCCESS, 250_000),
        NOW,
    )

    assert out.payment_state is PaymentState.RECOVERED
    assert out.contact_count == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"payment_state": PaymentState.RECOVERED},
        {"payment_state": PaymentState.DISPUTED},
        {"payment_state": PaymentState.WRITTEN_OFF},
        {"recovery_state": RecoveryState.STOPPED},
        {"recovery_state": RecoveryState.COMPLETED},
        {"recovery_state": RecoveryState.ESCALATED},
    ],
)
def test_terminal_payments_reject_any_outcome(kwargs):
    """Reaching the reducer with a closed case is an upstream defect, not a no-op."""
    with pytest.raises(TerminalStateError):
        apply_outcome(make_payment(**kwargs), ActionType.RETRY_PAYMENT, outcome(), NOW)


def test_apply_outcome_does_not_mutate_its_input():
    p = make_payment()
    apply_outcome(p, ActionType.RETRY_PAYMENT, outcome(ExecutionResult.SUCCESS, 1), NOW)

    assert p.retry_count == 0
    assert p.payment_state is PaymentState.FAILED


def test_exhaust_is_idempotent_on_terminal_cases():
    already = make_payment(payment_state=PaymentState.RECOVERED)
    assert exhaust(already, "no actions permitted") is already
