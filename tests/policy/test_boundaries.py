"""Boundary triples: one below, exactly at, one above every threshold.

Off-by-one errors in a policy engine are the difference between "3 retries" and
"4 retries against a customer's card", so each threshold is pinned on both sides.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from afin.domain.enums import ActionType, Decision
from afin.policy.config import PolicyConfig
from afin.policy.decisions import PolicyRule
from afin.policy.engine import evaluate
from tests.conftest import NOW
from tests.policy.conftest import propose, request


@pytest.mark.parametrize(
    "retry_count,allowed",
    [(0, True), (1, True), (2, True), (3, False), (4, False)],
)
def test_retry_budget_boundary(retry_count, allowed):
    """max_retries=3 means three attempts total: counts 0,1,2 may still act."""
    d = evaluate(request(retry_count=retry_count))

    assert d.allowed is allowed
    if not allowed:
        assert d.policy is PolicyRule.MAX_RETRY_LIMIT


@pytest.mark.parametrize(
    "amount,expected",
    [(999_999, Decision.ALLOW), (1_000_000, Decision.ALLOW), (1_000_001, Decision.REQUIRE_APPROVAL)],
)
def test_high_value_threshold_boundary(amount, expected):
    """The threshold is exclusive: exactly at the ceiling is still automatable."""
    assert evaluate(request(amount_minor=amount)).decision is expected


@pytest.mark.parametrize(
    "offset,allowed",
    [
        (timedelta(seconds=-1), True),   # window closes one second from now
        (timedelta(seconds=0), False),   # window closes exactly now
        (timedelta(seconds=1), False),   # window closed one second ago
    ],
)
def test_recovery_window_boundary(offset, allowed):
    d = evaluate(request(window_expires_at=NOW - offset))

    assert d.allowed is allowed
    if not allowed:
        assert d.policy is PolicyRule.RECOVERY_WINDOW_EXPIRED


@pytest.mark.parametrize(
    "hours_ago,allowed",
    [(5.99, False), (6.0, True), (6.01, True)],
)
def test_retry_cooldown_boundary(hours_ago, allowed):
    d = evaluate(request(last_attempt_at=NOW - timedelta(hours=hours_ago)))
    assert d.allowed is allowed


@pytest.mark.parametrize(
    "contact_count,allowed", [(2, True), (3, False), (4, False)]
)
def test_contact_budget_boundary(contact_count, allowed):
    d = evaluate(
        request(
            proposal=propose(action=ActionType.SEND_PAYMENT_REMINDER),
            contact_count=contact_count,
        )
    )
    assert d.allowed is allowed


@pytest.mark.parametrize("delay,allowed", [(71, True), (72, True), (73, False)])
def test_schedule_horizon_boundary(delay, allowed):
    d = evaluate(
        request(
            proposal=propose(action=ActionType.SCHEDULE_RETRY, scheduled_delay_hours=delay),
            window_expires_at=NOW + timedelta(days=30),
        )
    )
    assert d.allowed is allowed


def test_thresholds_come_from_config_not_constants():
    """A tightened config must actually tighten behaviour."""
    strict = PolicyConfig(max_retries=1, high_value_threshold_minor=100)

    assert evaluate(request(retry_count=1)).allowed is True
    assert evaluate(request(retry_count=1, config=strict)).allowed is False
    assert evaluate(request(config=strict)).decision is Decision.REQUIRE_APPROVAL
