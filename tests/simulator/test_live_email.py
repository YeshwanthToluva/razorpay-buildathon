"""The agent may write the message; the content policy decides if it is sent.

These tests exist because the claim "the agent writes this, and every word
passes a second boundary" was on the site while `compose()` was never called
and `evaluate_message` was an unused import. The wiring is now the thing under
test, so the claim cannot silently become false again.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from afin.agent.schema import MessageDraft
from afin.domain.enums import ActionType, ExecutionResult, FailureCategory, RiskType
from afin.policy.authorization import authorize
from afin.simulator.live_email import ComposedMessage, LiveEmailProvider
from afin.tools.notify import Message
from tests.conftest import NOW, make_payment
from tests.policy.conftest import propose, request


@dataclass
class Sent:
    delivered: bool = True
    provider_ref: str = "ref_1"
    channel: str = "outbox"
    detail: str = ""


class SpyNotifier:
    name = "spy"

    def __init__(self) -> None:
        self.messages: list[Message] = []

    def send(self, message: Message) -> Sent:
        self.messages.append(message)
        return Sent()


def draft(**kw) -> MessageDraft:
    base = dict(
        subject="Your ₹5,000 payment for inv_0001 did not go through",
        opening="We could not collect ₹5,000 for invoice inv_0001.",
        explanation="Your bank has declined a first attempt before and approved the retry.",
        tip="Paying before 9pm tends to clear on the first try.",
        closing="Reply to this email if anything looks wrong.",
        cta_label="Pay ₹5,000",
    )
    base.update(kw)
    return MessageDraft(**base)


def build(tmp_path, monkeypatch, composer=None):
    from afin.tools import paylink

    monkeypatch.setattr(paylink, "STORE", tmp_path)
    notifier = SpyNotifier()
    seen: list[ComposedMessage] = []
    provider = LiveEmailProvider(
        notifier=notifier, run_id="test-run", dataset_version="synthetic-v1",
        pay_base_url="http://localhost:8000", demo_recipient="demo@example.com",
        composer=composer, on_content=lambda m, p: seen.append(m),
    )
    return provider, notifier, seen


def execute(provider, payment=None, action=ActionType.GENERATE_PAYMENT_LINK):
    payment = payment if payment is not None else make_payment(
        amount_minor=500_000, failure_category=FailureCategory.DO_NOT_HONOR
    )
    decision, auth = authorize(
        request(proposal=propose(action=action, payment_id=payment.id), payment=payment)
    )
    assert auth is not None, f"test setup: policy denied {action} ({decision.reason})"
    return provider.execute(auth, payment, NOW)


def test_without_a_composer_the_template_is_sent(tmp_path, monkeypatch):
    provider, notifier, seen = build(tmp_path, monkeypatch)
    outcome = execute(provider)
    assert outcome.result is ExecutionResult.SUCCESS
    assert seen[0].authored_by == "template"
    assert seen[0].content_rule == "TEMPLATE"


def test_a_clean_draft_is_what_the_customer_receives(tmp_path, monkeypatch):
    d = draft()
    provider, notifier, seen = build(tmp_path, monkeypatch, composer=lambda p, a: d)
    execute(provider)
    assert seen[0].authored_by == "agent"
    assert seen[0].content_rule == "PERMITTED"
    assert notifier.messages[0].subject == d.subject
    # The line the agent adds that a template cannot.
    assert d.tip in notifier.messages[0].body_html


def test_a_refused_draft_is_never_delivered(tmp_path, monkeypatch):
    d = draft(explanation="We will refund this in full if you pay today.")
    provider, notifier, seen = build(tmp_path, monkeypatch, composer=lambda p, a: d)
    execute(provider)
    assert seen[0].authored_by == "template"
    assert seen[0].content_rule == "UNAUTHORISED_COMMITMENT"
    assert seen[0].refused_text is not None
    body = notifier.messages[0].body_html
    assert "refund" not in body.lower()


def test_a_fabricated_amount_is_refused(tmp_path, monkeypatch):
    d = draft(explanation="The outstanding balance is ₹9,900.")
    provider, notifier, seen = build(tmp_path, monkeypatch, composer=lambda p, a: d)
    execute(provider)
    assert seen[0].content_rule == "FABRICATED_AMOUNT"
    assert "9,900" not in notifier.messages[0].body_html


def test_a_card_claim_is_refused_when_no_card_exists(tmp_path, monkeypatch):
    payment = make_payment(
        amount_minor=500_000,
        risk_type=RiskType.CHECKOUT_ABANDONMENT,
        failure_category=FailureCategory.CHECKOUT_DROPPED,
    )
    d = draft(explanation="Your card on file appears to be unavailable.")
    provider, notifier, seen = build(tmp_path, monkeypatch, composer=lambda p, a: d)
    execute(provider, payment=payment, action=ActionType.GENERATE_PAYMENT_LINK)
    assert seen[0].content_rule == "CONTRADICTS_THE_CASE"
    assert "card on file" not in notifier.messages[0].body_html.lower()


def test_a_broken_composer_falls_back_rather_than_failing_the_run(tmp_path, monkeypatch):
    def boom(p, a):
        raise RuntimeError("model unreachable")

    provider, notifier, seen = build(tmp_path, monkeypatch, composer=boom)
    outcome = execute(provider)
    assert outcome.result is ExecutionResult.SUCCESS
    assert seen[0].authored_by == "template"
    assert seen[0].content_rule == "COMPOSER_ERROR"
    assert notifier.messages, "the customer must still be contacted"
