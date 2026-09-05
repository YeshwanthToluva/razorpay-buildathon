"""A provider that reaches a real person instead of rolling dice.

Financial actions still go to the simulator -- no real money moves anywhere in
this project. What changes is communication: instead of the simulator deciding
whether a customer "would have paid", an actual email goes out with an actual
link, and whether the money arrives is decided by whether a person clicks it.

It is a PaymentProvider like any other, so it is reachable only with an
AuthorizedAction, which means policy has already approved both the action and
the recipient before this class is constructed a message.

Two things write the message. A deterministic template always can, and an agent
may. When a composer is supplied, what it wrote is put to the content policy
before it can be sent; if that policy refuses, the template goes out instead and
the refusal is reported. The agent's prose is therefore never a way around a
boundary -- it is a candidate that has to pass one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable

from afin.domain.enums import (
    ActionType,
    COMMUNICATION_ACTIONS,
    ExecutionResult,
    FINANCIAL_ACTIONS,
    NO_INSTRUMENT_ON_FILE,
)
from afin.domain.models import PaymentSnapshot, ProviderOutcome
from afin.policy.authorization import AuthorizedAction
from afin.policy.content import ContentRule, evaluate_message
from afin.simulator.razorpay_sim import RazorpaySimulator
from afin.tools import paylink
from afin.tools.notify import Message, Notifier


def _rupees(minor: int) -> str:
    return f"₹{minor / 100:,.2f}"


REASON_TEXT = {
    "CARD_EXPIRED": "the card we had on file has expired, so we cannot simply try it again",
    "MANDATE_REVOKED": "the mandate for this payment was cancelled, so we cannot collect it automatically",
    "INSUFFICIENT_FUNDS": "the payment was declined for insufficient funds",
    "DO_NOT_HONOR": "your bank declined the payment",
    "BANK_UNAVAILABLE": "your bank could not be reached when we tried to collect",
    "PROCESSOR_ERROR": "our payment processor errored while collecting",
    "CHECKOUT_DROPPED": "your order was never completed",
    "PAYMENT_METHOD_DECLINED_AT_CHECKOUT": "your card was declined at checkout",
    "INVOICE_OVERDUE": "this invoice is now past its due date",
    "MANDATE_ABSENT": "there is no active mandate on this account, so it cannot be collected automatically",
}


@dataclass(frozen=True, slots=True)
class ComposedMessage:
    """The message that will actually be sent, and who wrote it."""

    subject: str
    paragraphs: tuple[str, ...]
    cta_label: str
    #: "agent" if the model's draft passed the content policy, else "template".
    authored_by: str
    #: The content rule that decided. TEMPLATE when no composer was offered.
    content_rule: str
    content_reason: str | None = None
    #: What the agent wrote, kept only when the content policy refused it. This
    #: is the evidence that a refusal happened; it is never delivered.
    refused_text: str | None = None


@dataclass
class LiveEmailProvider:
    """Sends a real recovery email; leaves the collecting to the customer."""

    notifier: Notifier
    run_id: str
    dataset_version: str
    pay_base_url: str
    demo_recipient: str | None = None
    redirected_from: str | None = None
    simulator: RazorpaySimulator = field(default_factory=RazorpaySimulator)
    #: Called with the created PayLink so a caller can stream it.
    on_link: Callable[[paylink.PayLink, PaymentSnapshot], None] | None = None
    #: Optional. Given (payment, action), returns a MessageDraft the agent wrote,
    #: or None to keep the template. Called only after policy authorised the
    #: action, so a bad sentence cannot corrupt a decision already made.
    composer: Callable[[PaymentSnapshot, ActionType], Any | None] | None = None
    #: Called with (ComposedMessage, payment) once the content policy has ruled,
    #: so a caller can stream the outcome. Observation only.
    on_content: Callable[[ComposedMessage, PaymentSnapshot], None] | None = None
    name: str = "live-email"

    # -- writing the message ----------------------------------------------

    def _template(self, payment: PaymentSnapshot, action: ActionType) -> ComposedMessage:
        """The deterministic message. Always available, never refused."""
        amount = _rupees(payment.amount_minor)
        reason = REASON_TEXT.get(
            payment.failure_category.value, "the payment could not be collected"
        )
        no_instrument = payment.risk_type in NO_INSTRUMENT_ON_FILE
        intent = {
            ActionType.RETRY_PAYMENT: (
                f"We are attempting to collect {amount} again now",
                "We are re-presenting the payment on your existing method. If it does not "
                "go through, you can settle it directly using the button below.",
            ),
            ActionType.SCHEDULE_RETRY: (
                f"We will retry {amount} shortly",
                "We have scheduled another attempt on your existing payment method. You do "
                "not need to wait for it — you can settle it right now instead.",
            ),
            ActionType.GENERATE_PAYMENT_LINK: (
                f"Action needed: {amount} outstanding",
                # Nothing was ever authorised on an abandoned checkout, so there
                # is no existing method to describe as unusable.
                "You can complete this payment using the link below."
                if no_instrument else
                "Your existing payment method cannot be used, so please settle this with "
                "another method.",
            ),
            ActionType.SEND_PAYMENT_REMINDER: (
                f"Reminder: {amount} outstanding",
                "This payment is still outstanding. You can settle it below.",
            ),
        }
        headline, explain = intent[action]
        return ComposedMessage(
            subject=f"{headline} — invoice {payment.invoice_id}",
            paragraphs=(
                f"We were unable to collect {amount} for invoice "
                f"{payment.invoice_id}, because {reason}.",
                explain,
            ),
            cta_label=f"Pay {amount}",
            authored_by="template",
            content_rule="TEMPLATE",
        )

    def _author(
        self, payment: PaymentSnapshot, action: ActionType, fallback: ComposedMessage
    ) -> ComposedMessage:
        """Ask the agent to write it, then put what it wrote to the content policy."""
        assert self.composer is not None
        try:
            draft = self.composer(payment, action)
        except Exception as exc:
            return replace(
                fallback,
                content_rule="COMPOSER_ERROR",
                content_reason=f"{type(exc).__name__}: {exc}"[:300],
            )
        if draft is None:
            return fallback

        paragraphs = tuple(
            p.strip()
            for p in (draft.opening, draft.explanation, draft.tip, draft.closing)
            if p and p.strip()
        )
        # The content policy sees exactly what the customer would see.
        visible = "\n".join((draft.subject, *paragraphs))
        decision = evaluate_message(
            visible,
            amount_minor=payment.amount_minor,
            invoice_id=payment.invoice_id,
            instrument_on_file=payment.risk_type not in NO_INSTRUMENT_ON_FILE,
        )
        if not decision.allowed:
            return replace(
                fallback,
                content_rule=decision.rule.value,
                content_reason=decision.reason,
                refused_text=visible,
            )
        return ComposedMessage(
            subject=draft.subject.strip(),
            paragraphs=paragraphs,
            cta_label=(draft.cta_label or "").strip() or f"Pay {_rupees(payment.amount_minor)}",
            authored_by="agent",
            content_rule=ContentRule.PERMITTED.value,
        )

    def _render(self, msg: ComposedMessage, url: str) -> str:
        paragraphs = "".join(f"<p>{p}</p>" for p in msg.paragraphs)
        return (
            "<div style='font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
            "max-width:540px;color:#14201e;line-height:1.55'>"
            f"{paragraphs}"
            f"<p style='margin:24px 0'><a href='{url}' style='background:#0d7d78;color:#fff;"
            "padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:600;"
            f"display:inline-block'>{msg.cta_label}</a></p>"
            f"<p style='font-size:12.5px;color:#65756f'>Or open: {url}</p>"
            "<hr style='border:none;border-top:1px solid #d5dbd9;margin:22px 0'>"
            "<p style='color:#65756f;font-size:12.5px'>Sent by an autonomous recovery agent. "
            "The decision to contact you was authorised by a deterministic policy engine "
            "before this message existed. Synthetic test data — no real payment is taken.</p>"
            "</div>"
        )

    # -- provider ----------------------------------------------------------

    def execute(
        self, authorized: AuthorizedAction, payment: PaymentSnapshot, now: datetime
    ) -> ProviderOutcome:
        action = authorized.action
        if action not in COMMUNICATION_ACTIONS | FINANCIAL_ACTIONS:
            # Stopping and escalating touch nobody.
            return self.simulator.execute(authorized, payment, now)

        recipient = self.demo_recipient or ""
        redirected_from = self.redirected_from
        if not recipient:
            return ProviderOutcome(
                ExecutionResult.REJECTED, 0, "NO_RECIPIENT", None,
                "no delivery address configured",
            )

        link = paylink.create(
            run_id=self.run_id,
            payment_id=payment.id,
            dataset_version=self.dataset_version,
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            invoice_id=payment.invoice_id,
            recipient=recipient,
            redirected_from=redirected_from,
        )
        url = f"{self.pay_base_url.rstrip('/')}/pay/{link.token}"

        message = self._template(payment, action)
        if self.composer is not None:
            message = self._author(payment, action, message)
        if self.on_content is not None:
            try:
                self.on_content(message, payment)
            except Exception:
                # A watcher must never be able to break a recovery run.
                pass

        result = self.notifier.send(
            Message(to=recipient, subject=message.subject,
                    body_html=self._render(message, url),
                    redirected_from=redirected_from)
        )
        if self.on_link:
            self.on_link(link, payment)

        if not result.delivered:
            return ProviderOutcome(
                ExecutionResult.FAILURE, 0, "DELIVERY_FAILED",
                result.provider_ref, f"could not deliver: {result.detail}",
            )
        # The message went out. Nothing is recovered until a person pays, which
        # is settled separately through the same reducer.
        return ProviderOutcome(
            ExecutionResult.SUCCESS, 0, None, result.provider_ref,
            f"payment link delivered to {recipient} via {result.channel} "
            f"({message.authored_by}-written)",
        )
