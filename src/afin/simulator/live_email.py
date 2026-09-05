"""A provider that reaches a real person instead of rolling dice.

Financial actions still go to the simulator -- no real money moves anywhere in
this project. What changes is communication: instead of the simulator deciding
whether a customer "would have paid", an actual email goes out with an actual
link, and whether the money arrives is decided by whether a person clicks it.

It is a PaymentProvider like any other, so it is reachable only with an
AuthorizedAction, which means policy has already approved both the action and
the recipient before this class is constructed a message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from afin.domain.enums import (
    ActionType,
    COMMUNICATION_ACTIONS,
    ExecutionResult,
    FINANCIAL_ACTIONS,
)
from afin.domain.models import PaymentSnapshot, ProviderOutcome
from afin.policy.authorization import AuthorizedAction
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
    name: str = "live-email"

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
        reason = REASON_TEXT.get(payment.failure_category.value, "the payment could not be collected")

        # What the agent decided to do, said the way the customer would hear it.
        INTENT = {
            ActionType.RETRY_PAYMENT: (
                f"We are attempting to collect {_rupees(payment.amount_minor)} again now",
                "We are re-presenting the payment on your existing method. If it does not "
                "go through, you can settle it directly using the button below.",
            ),
            ActionType.SCHEDULE_RETRY: (
                f"We will retry {_rupees(payment.amount_minor)} shortly",
                "We have scheduled another attempt on your existing payment method. You do "
                "not need to wait for it — you can settle it right now instead.",
            ),
            ActionType.GENERATE_PAYMENT_LINK: (
                f"Action needed: {_rupees(payment.amount_minor)} outstanding",
                "Your existing payment method cannot be used, so please settle this with "
                "another method.",
            ),
            ActionType.SEND_PAYMENT_REMINDER: (
                f"Reminder: {_rupees(payment.amount_minor)} outstanding",
                "This payment is still outstanding. You can settle it below.",
            ),
        }
        headline, explain = INTENT[action]
        subject = f"{headline} — invoice {payment.invoice_id}"
        body = (
            "<div style='font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
            "max-width:540px;color:#14201e;line-height:1.55'>"
            f"<p>We were unable to collect <strong>{_rupees(payment.amount_minor)}</strong> "
            f"for invoice <code>{payment.invoice_id}</code>, because {reason}.</p>"
            f"<p>{explain}</p>"
            f"<p style='margin:24px 0'><a href='{url}' style='background:#0d7d78;color:#fff;"
            "padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:600;"
            f"display:inline-block'>Pay {_rupees(payment.amount_minor)}</a></p>"
            f"<p style='font-size:12.5px;color:#65756f'>Or open: {url}</p>"
            "<hr style='border:none;border-top:1px solid #d5dbd9;margin:22px 0'>"
            "<p style='color:#65756f;font-size:12.5px'>Sent by an autonomous recovery agent. "
            "The decision to contact you was authorised by a deterministic policy engine "
            "before this message existed. Synthetic test data — no real payment is taken.</p>"
            "</div>"
        )

        result = self.notifier.send(
            Message(to=recipient, subject=subject, body_html=body,
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
            f"payment link delivered to {recipient} via {result.channel}",
        )
