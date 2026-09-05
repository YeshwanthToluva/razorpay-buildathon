"""Properties that must hold across the whole (action x state) space.

Example-based tests prove a rule works on the cases someone thought of. These
sweep the product of every action against every dangerous state, which is what
actually protects against a future rule reordering quietly opening a hole.
"""

from __future__ import annotations

import itertools
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
    RiskType,
    SAFETY_VALVE_ACTIONS,
)
from afin.policy.decisions import PolicyRule
from afin.policy.engine import RULES, evaluate
from tests.conftest import NOW, make_customer, make_payment
from tests.policy.conftest import propose, request

#: Actions that could move money or reach a customer -- the ones that matter.
CONSEQUENTIAL = [a for a in ActionType if a not in SAFETY_VALVE_ACTIONS]

DELAYS = [None, 1, 24, 72]


def _sweep(payment_kw, customer_kw=None):
    """Every consequential action against one dangerous state."""
    for action, delay in itertools.product(CONSEQUENTIAL, DELAYS):
        yield action, evaluate(
            request(
                proposal=propose(
                    action=action, scheduled_delay_hours=delay, channel=Channel.EMAIL
                ),
                payment=make_payment(**payment_kw),
                customer=make_customer(**(customer_kw or {})),
            )
        )


def test_no_consequential_action_is_ever_allowed_on_a_disputed_payment():
    for action, d in _sweep({"is_disputed": True}):
        assert d.allowed is False, f"{action} was allowed on a disputed payment"
        assert d.decision is Decision.DENY


def test_no_consequential_action_is_ever_allowed_under_a_fraud_signal():
    for action, d in _sweep({"failure_category": FailureCategory.FRAUD_SUSPECTED}):
        assert d.allowed is False, f"{action} was allowed under a fraud signal"
    for action, d in _sweep({}, {"risk_flag": CustomerRiskFlag.FRAUD_WATCH}):
        assert d.allowed is False, f"{action} was allowed for a fraud-watched customer"


@pytest.mark.parametrize(
    "kw",
    [
        {"payment_state": s} for s in (PaymentState.RECOVERED, PaymentState.WRITTEN_OFF,
                                       PaymentState.REFUNDED, PaymentState.DISPUTED)
    ]
    + [
        {"recovery_state": s} for s in (RecoveryState.COMPLETED, RecoveryState.STOPPED,
                                        RecoveryState.ESCALATED)
    ],
)
def test_nothing_at_all_is_allowed_on_a_terminal_case(kw):
    """Including the safety valves: a closed case has nothing left to stop."""
    for action, delay in itertools.product(ActionType, DELAYS):
        d = evaluate(
            request(
                proposal=propose(action=action, scheduled_delay_hours=delay),
                payment=make_payment(**kw),
            )
        )
        assert d.allowed is False, f"{action} was allowed on terminal case {kw}"


def test_no_communication_is_ever_allowed_to_an_opted_out_customer():
    for action, d in _sweep({}, {"opted_out": True}):
        if action in (ActionType.SEND_PAYMENT_REMINDER, ActionType.GENERATE_PAYMENT_LINK):
            assert d.allowed is False, f"{action} reached an opted-out customer"


def test_retry_budget_can_never_be_exceeded():
    for count in range(3, 12):
        for action in (ActionType.RETRY_PAYMENT, ActionType.SCHEDULE_RETRY):
            d = evaluate(
                request(
                    proposal=propose(action=action, scheduled_delay_hours=24),
                    retry_count=count,
                )
            )
            assert d.allowed is False, f"{action} allowed at retry_count={count}"


def test_nothing_consequential_is_allowed_after_the_window_closes():
    for action, d in _sweep({"window_expires_at": NOW - timedelta(seconds=1)}):
        assert d.allowed is False, f"{action} allowed past the recovery window"


def test_an_allowed_action_is_always_a_member_of_the_action_space():
    """The gateway relies on this: allowed implies a real, executable action."""
    for action, delay in itertools.product(
        list(ActionType) + ["WIRE_FUNDS", "DROP_TABLE", ""], DELAYS
    ):
        d = evaluate(
            request(proposal=propose(action=action, scheduled_delay_hours=delay))
        )
        if d.allowed:
            assert isinstance(action, ActionType)


def test_require_approval_is_never_reported_as_allowed():
    """At Autonomy Level 2 nothing approves these, so allowed must stay False."""
    for action, delay in itertools.product(CONSEQUENTIAL, DELAYS):
        d = evaluate(
            request(
                proposal=propose(action=action, scheduled_delay_hours=delay),
                amount_minor=90_000_000,
            )
        )
        if d.decision is Decision.REQUIRE_APPROVAL:
            assert d.allowed is False


def test_every_rule_in_the_table_is_reachable():
    """A rule no input can trigger is dead safety code; catch it at the source."""
    declared = {rule_id for rule_id, _ in RULES}
    unreachable = declared - _rules_triggered_by_a_broad_sweep()
    assert not unreachable, f"unreachable policy rules: {sorted(unreachable)}"


def _rules_triggered_by_a_broad_sweep() -> set[PolicyRule]:
    seen: set[PolicyRule] = set()
    states = [
        {},
        {"is_disputed": True},
        {"failure_category": FailureCategory.FRAUD_SUSPECTED},
        {"failure_category": FailureCategory.CARD_EXPIRED},
        {"recovery_state": RecoveryState.STOPPED},
        {"retry_count": 5},
        {"contact_count": 5},
        {"amount_minor": 90_000_000},
        {"window_expires_at": NOW - timedelta(days=1)},
        {"last_attempt_at": NOW - timedelta(minutes=30)},
        {"risk_type": RiskType.CHECKOUT_ABANDONMENT,
         "failure_category": FailureCategory.CHECKOUT_DROPPED},
    ]
    emails = ["cust_0001@synthetic.invalid", "stranger@elsewhere.invalid"]
    for kw, action, delay, opted, pid, email in itertools.product(
        states, list(ActionType) + ["WIRE_FUNDS"], DELAYS, [False, True],
        ["pay_0001", "pay_X"], emails,
    ):
        d = evaluate(
            request(
                proposal=propose(action=action, payment_id=pid, scheduled_delay_hours=delay),
                payment=make_payment(**kw),
                customer=make_customer(opted_out=opted, email=email),
            )
        )
        seen.add(d.policy)
    return seen
