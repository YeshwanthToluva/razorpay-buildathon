"""Who an outbound message may actually reach.

The check lives in policy, ahead of authority being minted, so a mail tool
never gets the chance to decide for itself.
"""

from __future__ import annotations

import pytest

from afin.domain.enums import ActionType, COMMUNICATION_ACTIONS, FINANCIAL_ACTIONS
from afin.policy.authorization import authorize
from afin.policy.config import PolicyConfig
from afin.policy.decisions import PolicyRule
from afin.policy.engine import evaluate
from tests.conftest import make_customer
from tests.policy.conftest import propose, request

ALLOWED = "cust_0001@synthetic.invalid"
CONFIG = PolicyConfig(email_allowlist=(ALLOWED,))
EMPTY = PolicyConfig(email_allowlist=())


@pytest.mark.parametrize("action", sorted(COMMUNICATION_ACTIONS))
def test_an_unlisted_recipient_is_refused(action):
    d = evaluate(
        request(
            proposal=propose(action=action),
            customer=make_customer(email="stranger@elsewhere.invalid"),
            config=CONFIG,
        )
    )
    assert d.allowed is False
    assert d.policy is PolicyRule.RECIPIENT_NOT_ALLOWLISTED
    assert d.risk_level.value == "CRITICAL"


@pytest.mark.parametrize("action", sorted(COMMUNICATION_ACTIONS))
def test_an_allowlisted_recipient_may_be_contacted(action):
    d = evaluate(
        request(proposal=propose(action=action),
                customer=make_customer(email=ALLOWED), config=CONFIG)
    )
    assert d.allowed is True


def test_the_check_is_case_and_whitespace_insensitive():
    d = evaluate(
        request(
            proposal=propose(action=ActionType.SEND_PAYMENT_REMINDER),
            customer=make_customer(email="  CUST_0001@Synthetic.Invalid "),
            config=CONFIG,
        )
    )
    assert d.allowed is True


def test_a_customer_with_no_address_cannot_be_contacted():
    d = evaluate(
        request(proposal=propose(action=ActionType.SEND_PAYMENT_REMINDER),
                customer=make_customer(email=""), config=CONFIG)
    )
    assert d.allowed is False
    assert d.policy is PolicyRule.RECIPIENT_NOT_ALLOWLISTED


@pytest.mark.parametrize("action", sorted(COMMUNICATION_ACTIONS))
def test_an_empty_allowlist_sends_to_nobody(action):
    """A misconfigured deployment must send nothing, not everything."""
    d = evaluate(
        request(proposal=propose(action=action),
                customer=make_customer(email=ALLOWED), config=EMPTY)
    )
    assert d.allowed is False
    assert d.policy is PolicyRule.RECIPIENT_NOT_ALLOWLISTED


@pytest.mark.parametrize("action", sorted(FINANCIAL_ACTIONS))
def test_the_allowlist_does_not_block_charging(action):
    """It governs delivery, not collection: a card may still be re-presented."""
    d = evaluate(
        request(proposal=propose(action=action, scheduled_delay_hours=24),
                customer=make_customer(email="stranger@elsewhere.invalid"),
                config=CONFIG)
    )
    assert d.allowed is True


def test_no_authority_is_minted_for_an_unlisted_recipient():
    """The property that matters: the mail tool is never reachable."""
    _, auth = authorize(
        request(proposal=propose(action=ActionType.SEND_PAYMENT_REMINDER),
                customer=make_customer(email="stranger@elsewhere.invalid"),
                config=CONFIG)
    )
    assert auth is None


def test_opt_out_still_outranks_the_allowlist():
    """An allowlisted customer who opted out is still not contacted."""
    d = evaluate(
        request(proposal=propose(action=ActionType.SEND_PAYMENT_REMINDER),
                customer=make_customer(email=ALLOWED, opted_out=True), config=CONFIG)
    )
    assert d.policy is PolicyRule.OPT_OUT_COMMUNICATION


# --- when the human is the payment rail ------------------------------------

LIVE = PolicyConfig(email_allowlist=(ALLOWED,), contact_is_the_payment_rail=True)


@pytest.mark.parametrize("action", sorted(FINANCIAL_ACTIONS))
def test_a_charge_that_reaches_the_customer_obeys_the_allowlist(action):
    """With no automated rail, a retry is delivered to a person and must clear
    the same rules a message does. Otherwise the live channel would contact
    someone policy believed it was silently charging."""
    d = evaluate(
        request(proposal=propose(action=action, scheduled_delay_hours=24),
                customer=make_customer(email="stranger@elsewhere.invalid"),
                config=LIVE)
    )
    assert d.allowed is False
    assert d.policy is PolicyRule.RECIPIENT_NOT_ALLOWLISTED


@pytest.mark.parametrize("action", sorted(FINANCIAL_ACTIONS))
def test_a_charge_that_reaches_an_opted_out_customer_is_refused(action):
    d = evaluate(
        request(proposal=propose(action=action, scheduled_delay_hours=24),
                customer=make_customer(email=ALLOWED, opted_out=True), config=LIVE)
    )
    assert d.allowed is False
    assert d.policy is PolicyRule.OPT_OUT_COMMUNICATION


def test_the_normal_rail_still_allows_silent_charging():
    """Without the live channel, opting out of contact does not stop a retry."""
    d = evaluate(
        request(proposal=propose(action=ActionType.RETRY_PAYMENT),
                customer=make_customer(email=ALLOWED, opted_out=True), config=CONFIG)
    )
    assert d.allowed is True
