"""Mock Razorpay adapter.

NO REAL FINANCIAL TRANSACTIONS. This never opens a socket.

Outcomes are deterministic under a fixed seed: the per-attempt draw is derived
from SHA-256 of (seed, payment id, action, attempt number), not from a mutable
PRNG stream. That makes a single payment's outcome independent of how many
other payments were processed before it -- so re-running one case in isolation
reproduces the experiment, and adding a payment to the dataset does not silently
change every later result.

The physics encode one idea: a failure category has an underlying nature, and
re-presenting the same instrument only helps when the obstacle is temporary.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from datetime import datetime

from afin.domain.enums import ActionType, ExecutionResult, FailureCategory, RiskType
from afin.domain.models import PaymentSnapshot, ProviderOutcome
from afin.policy.authorization import AuthorizedAction

#: P(success) when re-presenting the same instrument, by failure category.
#: Transient infrastructure faults usually clear; a declined instrument does not.
_RETRY_SUCCESS: dict[FailureCategory, float] = {
    FailureCategory.BANK_UNAVAILABLE: 0.75,
    FailureCategory.PROCESSOR_ERROR: 0.80,
    FailureCategory.INSUFFICIENT_FUNDS: 0.35,
    FailureCategory.DO_NOT_HONOR: 0.15,
    FailureCategory.CARD_EXPIRED: 0.0,
    FailureCategory.MANDATE_REVOKED: 0.0,
    FailureCategory.FRAUD_SUSPECTED: 0.0,
    # An abandoned checkout has no instrument to re-present at all.
    FailureCategory.CHECKOUT_DROPPED: 0.0,
    FailureCategory.PAYMENT_METHOD_DECLINED_AT_CHECKOUT: 0.0,
    # An overdue invoice with a live mandate collects well; without one, never.
    FailureCategory.INVOICE_OVERDUE: 0.55,
    FailureCategory.MANDATE_ABSENT: 0.0,
}

#: P(customer pays) after being sent a link. A dead instrument is no obstacle
#: here -- the whole point of a link is that they can use a different one.
_LINK_SUCCESS: dict[FailureCategory, float] = {
    FailureCategory.BANK_UNAVAILABLE: 0.30,
    FailureCategory.PROCESSOR_ERROR: 0.30,
    FailureCategory.INSUFFICIENT_FUNDS: 0.40,
    FailureCategory.DO_NOT_HONOR: 0.35,
    FailureCategory.CARD_EXPIRED: 0.55,
    FailureCategory.MANDATE_REVOKED: 0.45,
    FailureCategory.FRAUD_SUSPECTED: 0.0,
    # A checkout link recovers well -- the customer had already chosen to buy.
    FailureCategory.CHECKOUT_DROPPED: 0.45,
    FailureCategory.PAYMENT_METHOD_DECLINED_AT_CHECKOUT: 0.50,
    FailureCategory.INVOICE_OVERDUE: 0.40,
    FailureCategory.MANDATE_ABSENT: 0.42,
}

#: A reminder nudges a customer who could always have paid; it cannot fix a
#: broken instrument, so its ceiling is low and category-insensitive.
_REMINDER_SUCCESS = 0.18

#: Reminders work harder on some risks than others: an abandoned cart is a live
#: intent, and an overdue invoice is a payment the customer already owes.
_REMINDER_BY_RISK: dict[RiskType, float] = {
    RiskType.CHECKOUT_ABANDONMENT: 0.30,
    RiskType.OVERDUE_RECEIVABLE: 0.34,
}

#: Deferring an attempt helps most where the obstacle is time itself.
_SCHEDULE_BONUS: dict[FailureCategory, float] = {
    FailureCategory.INSUFFICIENT_FUNDS: 0.20,
    FailureCategory.BANK_UNAVAILABLE: 0.10,
}


def _draw(seed: int, payment_id: str, action: str, attempt: int) -> float:
    """A stable uniform draw in [0, 1) for this exact (payment, action, attempt)."""
    key = f"{seed}|{payment_id}|{action}|{attempt}".encode()
    digest = hashlib.sha256(key).digest()
    (value,) = struct.unpack(">Q", digest[:8])
    return value / float(1 << 64)


@dataclass
class RazorpaySimulator:
    """Deterministic stand-in for a payment gateway."""

    seed: int = 20260304
    name: str = "razorpay-sim"

    def execute(
        self, authorized: AuthorizedAction, payment: PaymentSnapshot, now: datetime
    ) -> ProviderOutcome:
        action = authorized.action

        if authorized.payment_id != payment.id:
            # Authority is minted per payment; using it elsewhere is a defect.
            return ProviderOutcome(
                result=ExecutionResult.REJECTED,
                amount_recovered_minor=0,
                failure_code="AUTHORIZATION_SCOPE_MISMATCH",
                provider_ref=None,
                detail=f"authority for {authorized.payment_id} used on {payment.id}",
            )

        if action is ActionType.STOP_RECOVERY:
            return self._noop(payment, action, "recovery stopped by decision")
        if action is ActionType.REQUEST_HUMAN_REVIEW:
            return self._noop(payment, action, "case queued for human review")

        attempt = payment.retry_count + payment.contact_count
        roll = _draw(self.seed, payment.id, action.value, attempt)
        ref = self._ref(payment, action, attempt)

        if action is ActionType.RETRY_PAYMENT:
            p = _RETRY_SUCCESS[payment.failure_category]
            return self._charge(payment, roll, p, ref, "retry")

        if action is ActionType.SCHEDULE_RETRY:
            p = _RETRY_SUCCESS[payment.failure_category]
            p = min(p + _SCHEDULE_BONUS.get(payment.failure_category, 0.0), 1.0)
            return self._charge(payment, roll, p, ref, "scheduled retry")

        if action is ActionType.GENERATE_PAYMENT_LINK:
            p = _LINK_SUCCESS[payment.failure_category]
            return self._charge(payment, roll, p, ref, "payment link")

        if action is ActionType.SEND_PAYMENT_REMINDER:
            p = _REMINDER_BY_RISK.get(payment.risk_type, _REMINDER_SUCCESS)
            return self._charge(payment, roll, p, ref, "reminder")

        return ProviderOutcome(
            result=ExecutionResult.REJECTED,
            amount_recovered_minor=0,
            failure_code="UNSUPPORTED_BY_PROVIDER",
            provider_ref=None,
            detail=f"{action} is not implemented by {self.name}",
        )

    # -- helpers ----------------------------------------------------------

    def _ref(self, payment: PaymentSnapshot, action: ActionType, attempt: int) -> str:
        tail = hashlib.sha256(
            f"{self.seed}|{payment.id}|{action.value}|{attempt}".encode()
        ).hexdigest()[:12]
        return f"sim_{tail}"

    def _charge(
        self,
        payment: PaymentSnapshot,
        roll: float,
        probability: float,
        ref: str,
        label: str,
    ) -> ProviderOutcome:
        if roll < probability:
            return ProviderOutcome(
                result=ExecutionResult.SUCCESS,
                amount_recovered_minor=payment.amount_minor,
                failure_code=None,
                provider_ref=ref,
                detail=f"{label} captured {payment.amount_minor} minor units",
            )
        return ProviderOutcome(
            result=ExecutionResult.FAILURE,
            amount_recovered_minor=0,
            failure_code=payment.failure_code,
            provider_ref=ref,
            detail=f"{label} declined ({payment.failure_code})",
        )

    def _noop(self, payment: PaymentSnapshot, action: ActionType, detail: str) -> ProviderOutcome:
        return ProviderOutcome(
            result=ExecutionResult.SUCCESS,
            amount_recovered_minor=0,
            failure_code=None,
            provider_ref=None,
            detail=detail,
        )
