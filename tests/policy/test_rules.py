"""One test per rule, allow side and deny side."""

from __future__ import annotations

from datetime import timedelta

import pytest

from afin.domain.enums import (
    ActionType,
    Channel,
    CustomerRiskFlag,
    Decision,
    FailureCategory,
    PaymentState,
    RecoveryState,
    RiskLevel,
)
from afin.policy.decisions import PolicyRule
from afin.policy.engine import evaluate
from tests.conftest import NOW, make_customer, make_payment
from tests.policy.conftest import propose, request


# --- UNSUPPORTED_ACTION ---------------------------------------------------

@pytest.mark.parametrize(
    "invented",
    ["WIRE_FUNDS", "REFUND_PAYMENT", "DELETE_CUSTOMER", "retry_payment", "", "RETRY_PAYMENT "],
)
def test_invented_actions_are_denied_as_unsupported(invented):
    d = evaluate(request(proposal=propose(action=invented)))

    assert d.allowed is False
    assert d.policy is PolicyRule.UNSUPPORTED_ACTION
    assert d.risk_level is RiskLevel.CRITICAL


def test_action_space_members_are_not_unsupported():
    for action in ActionType:
        d = evaluate(request(proposal=propose(action=action, scheduled_delay_hours=24)))
        assert d.policy is not PolicyRule.UNSUPPORTED_ACTION


# --- PAYMENT_MISMATCH -----------------------------------------------------

def test_proposal_targeting_a_different_payment_is_denied():
    d = evaluate(request(proposal=propose(payment_id="pay_9999")))

    assert d.allowed is False
    assert d.policy is PolicyRule.PAYMENT_MISMATCH
    assert d.risk_level is RiskLevel.CRITICAL


def test_customer_context_belonging_to_another_payment_is_denied():
    d = evaluate(request(customer=make_customer(id="cust_8888")))

    assert d.policy is PolicyRule.PAYMENT_MISMATCH


# --- TERMINAL_STATE -------------------------------------------------------

@pytest.mark.parametrize(
    "kw",
    [
        {"payment_state": PaymentState.RECOVERED},
        {"payment_state": PaymentState.WRITTEN_OFF},
        {"payment_state": PaymentState.REFUNDED},
        {"payment_state": PaymentState.DISPUTED},
        {"recovery_state": RecoveryState.COMPLETED},
        {"recovery_state": RecoveryState.STOPPED},
        {"recovery_state": RecoveryState.ESCALATED},
    ],
)
@pytest.mark.parametrize("action", list(ActionType))
def test_no_action_whatsoever_is_permitted_on_a_terminal_case(kw, action):
    d = evaluate(request(proposal=propose(action=action, scheduled_delay_hours=24), **kw))

    assert d.allowed is False
    assert d.policy is PolicyRule.TERMINAL_STATE


# --- SAFETY_VALVE ---------------------------------------------------------

@pytest.mark.parametrize("action", [ActionType.STOP_RECOVERY, ActionType.REQUEST_HUMAN_REVIEW])
def test_stopping_and_escalating_survive_every_blocking_condition(action):
    """Policy must never trap an open case with no legal exit."""
    trapped = make_payment(
        is_disputed=True,
        retry_count=99,
        contact_count=99,
        failure_category=FailureCategory.FRAUD_SUSPECTED,
        amount_minor=50_000_000,
        window_expires_at=NOW - timedelta(days=5),
    )
    d = evaluate(
        request(
            proposal=propose(action=action),
            payment=trapped,
            customer=make_customer(opted_out=True, risk_flag=CustomerRiskFlag.FRAUD_WATCH),
        )
    )

    assert d.allowed is True
    assert d.policy is PolicyRule.SAFETY_VALVE


# --- DISPUTE_BLOCK --------------------------------------------------------

@pytest.mark.parametrize(
    "action",
    [
        ActionType.RETRY_PAYMENT,
        ActionType.SCHEDULE_RETRY,
        ActionType.SEND_PAYMENT_REMINDER,
        ActionType.GENERATE_PAYMENT_LINK,
    ],
)
def test_disputed_payments_block_all_recovery_actions(action):
    d = evaluate(
        request(proposal=propose(action=action, scheduled_delay_hours=24), is_disputed=True)
    )

    assert d.allowed is False
    assert d.policy is PolicyRule.DISPUTE_BLOCK
    assert d.risk_level is RiskLevel.CRITICAL


def test_undisputed_payment_passes_the_dispute_rule():
    d = evaluate(request(is_disputed=False))
    assert d.policy is not PolicyRule.DISPUTE_BLOCK


# --- FRAUD_HOLD -----------------------------------------------------------

def test_fraud_failure_category_blocks_recovery():
    d = evaluate(request(failure_category=FailureCategory.FRAUD_SUSPECTED))

    assert d.allowed is False
    assert d.policy is PolicyRule.FRAUD_HOLD


def test_fraud_watched_customer_blocks_recovery_even_on_a_benign_failure():
    d = evaluate(
        request(
            customer=make_customer(risk_flag=CustomerRiskFlag.FRAUD_WATCH),
            failure_category=FailureCategory.BANK_UNAVAILABLE,
        )
    )

    assert d.allowed is False
    assert d.policy is PolicyRule.FRAUD_HOLD


# --- OPT_OUT_COMMUNICATION ------------------------------------------------

@pytest.mark.parametrize(
    "action", [ActionType.SEND_PAYMENT_REMINDER, ActionType.GENERATE_PAYMENT_LINK]
)
def test_opted_out_customer_blocks_communication(action):
    d = evaluate(
        request(
            proposal=propose(action=action, channel=Channel.EMAIL),
            customer=make_customer(opted_out=True),
        )
    )

    assert d.allowed is False
    assert d.policy is PolicyRule.OPT_OUT_COMMUNICATION


def test_opt_out_does_not_block_a_silent_retry():
    """Opting out of communication is not opting out of being charged."""
    d = evaluate(request(customer=make_customer(opted_out=True)))

    assert d.allowed is True
    assert d.policy is PolicyRule.PERMITTED


# --- RECOVERY_WINDOW_EXPIRED ---------------------------------------------

def test_expired_recovery_window_blocks_recovery():
    d = evaluate(request(window_expires_at=NOW - timedelta(seconds=1)))

    assert d.allowed is False
    assert d.policy is PolicyRule.RECOVERY_WINDOW_EXPIRED


# --- MAX_RETRY_LIMIT ------------------------------------------------------

@pytest.mark.parametrize("action", [ActionType.RETRY_PAYMENT, ActionType.SCHEDULE_RETRY])
def test_exhausted_retry_budget_blocks_financial_actions(action):
    d = evaluate(
        request(proposal=propose(action=action, scheduled_delay_hours=24), retry_count=3)
    )

    assert d.allowed is False
    assert d.policy is PolicyRule.MAX_RETRY_LIMIT


def test_retry_limit_does_not_block_communication():
    """A customer can still be asked to pay after automated retries are spent."""
    d = evaluate(
        request(proposal=propose(action=ActionType.GENERATE_PAYMENT_LINK), retry_count=3)
    )

    assert d.allowed is True


# --- RETRY_COOLDOWN -------------------------------------------------------

def test_retry_inside_cooldown_is_denied():
    d = evaluate(request(last_attempt_at=NOW - timedelta(hours=1)))

    assert d.allowed is False
    assert d.policy is PolicyRule.RETRY_COOLDOWN


def test_retry_after_cooldown_is_allowed():
    d = evaluate(request(last_attempt_at=NOW - timedelta(hours=7)))

    assert d.allowed is True


def test_scheduled_retry_is_exempt_from_cooldown():
    """SCHEDULE_RETRY defers the attempt, which is the remedy for a cooldown."""
    d = evaluate(
        request(
            proposal=propose(action=ActionType.SCHEDULE_RETRY, scheduled_delay_hours=24),
            last_attempt_at=NOW - timedelta(hours=1),
        )
    )

    assert d.allowed is True


# --- ACTION_PRECONDITION --------------------------------------------------

@pytest.mark.parametrize(
    "category", [FailureCategory.CARD_EXPIRED, FailureCategory.MANDATE_REVOKED]
)
def test_retrying_a_dead_instrument_is_denied(category):
    d = evaluate(request(failure_category=category))

    assert d.allowed is False
    assert d.policy is PolicyRule.ACTION_PRECONDITION


@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.BANK_UNAVAILABLE,
        FailureCategory.PROCESSOR_ERROR,
        FailureCategory.INSUFFICIENT_FUNDS,
        FailureCategory.DO_NOT_HONOR,
    ],
)
def test_retrying_a_live_instrument_is_allowed(category):
    d = evaluate(request(failure_category=category))
    assert d.allowed is True


def test_expired_card_can_still_be_sent_a_payment_link():
    """The remedy for a dead instrument is a new one, so this must stay open."""
    d = evaluate(
        request(
            proposal=propose(action=ActionType.GENERATE_PAYMENT_LINK),
            failure_category=FailureCategory.CARD_EXPIRED,
        )
    )

    assert d.allowed is True


@pytest.mark.parametrize("delay", [None, 0, -5])
def test_schedule_retry_requires_a_positive_delay(delay):
    d = evaluate(
        request(proposal=propose(action=ActionType.SCHEDULE_RETRY, scheduled_delay_hours=delay))
    )

    assert d.allowed is False
    assert d.policy is PolicyRule.ACTION_PRECONDITION


def test_schedule_retry_beyond_the_horizon_is_denied():
    d = evaluate(
        request(proposal=propose(action=ActionType.SCHEDULE_RETRY, scheduled_delay_hours=73))
    )

    assert d.policy is PolicyRule.ACTION_PRECONDITION


def test_schedule_retry_landing_after_the_window_is_denied():
    d = evaluate(
        request(
            proposal=propose(action=ActionType.SCHEDULE_RETRY, scheduled_delay_hours=48),
            window_expires_at=NOW + timedelta(hours=12),
        )
    )

    assert d.allowed is False
    assert d.policy is PolicyRule.ACTION_PRECONDITION


# --- CONTACT_BUDGET -------------------------------------------------------

def test_exhausted_contact_budget_blocks_communication():
    d = evaluate(
        request(proposal=propose(action=ActionType.SEND_PAYMENT_REMINDER), contact_count=3)
    )

    assert d.allowed is False
    assert d.policy is PolicyRule.CONTACT_BUDGET


def test_contact_budget_does_not_block_retries():
    d = evaluate(request(contact_count=3))
    assert d.allowed is True


# --- HIGH_VALUE_APPROVAL --------------------------------------------------

def test_high_value_payment_requires_human_approval_and_does_not_execute():
    d = evaluate(request(amount_minor=1_000_001))

    assert d.decision is Decision.REQUIRE_APPROVAL
    assert d.allowed is False, "REQUIRE_APPROVAL must never be treated as an allow"
    assert d.policy is PolicyRule.HIGH_VALUE_APPROVAL


def test_payment_at_the_ceiling_does_not_require_approval():
    d = evaluate(request(amount_minor=1_000_000))
    assert d.allowed is True
