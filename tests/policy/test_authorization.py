"""Execution authority cannot be forged."""

from __future__ import annotations

import pytest

from afin.domain.enums import ActionType, FailureCategory
from afin.policy.authorization import AuthorizedAction, PolicyBypassError, authorize
from tests.conftest import make_payment
from tests.policy.conftest import propose, request


def test_allowed_proposal_mints_authority():
    decision, auth = authorize(request())

    assert decision.allowed is True
    assert auth is not None
    assert auth.action is ActionType.RETRY_PAYMENT
    assert auth.payment_id == "pay_0001"


def test_denied_proposal_mints_nothing():
    decision, auth = authorize(request(is_disputed=True))

    assert decision.allowed is False
    assert auth is None, "a denied proposal must not carry execution authority"


def test_require_approval_mints_nothing():
    decision, auth = authorize(request(amount_minor=90_000_000))
    assert auth is None


@pytest.mark.parametrize("forged_token", [None, object(), "token", 0, [], AuthorizedAction])
def test_authority_cannot_be_constructed_outside_the_policy_module(forged_token):
    with pytest.raises(PolicyBypassError):
        AuthorizedAction(
            forged_token,
            action=ActionType.RETRY_PAYMENT,
            payment_id="pay_0001",
            proposal=propose(),
            decision=None,
        )


def test_unsafe_proposals_never_yield_authority():
    """The Sprint 1 headline invariant, at the mint rather than the ledger."""
    unsafe = [
        request(proposal=propose(action="WIRE_FUNDS_TO_ATTACKER")),
        request(proposal=propose(payment_id="pay_9999")),
        request(is_disputed=True),
        request(failure_category=FailureCategory.FRAUD_SUSPECTED),
        request(retry_count=99),
        request(amount_minor=90_000_000),
        request(window_expires_at=make_payment().failed_at),
        request(failure_category=FailureCategory.CARD_EXPIRED),
    ]
    for r in unsafe:
        _, auth = authorize(r)
        assert auth is None, f"authority minted for unsafe proposal {r.proposal.action_label}"
