"""The payment provider port.

Deliberately shaped like a real gateway client so a Razorpay test-mode adapter
can replace the simulator without touching the orchestrator. It accepts an
AuthorizedAction, not a proposal: there is no method on this interface that an
unauthorized caller can reach.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from afin.domain.models import PaymentSnapshot, ProviderOutcome
from afin.policy.authorization import AuthorizedAction


class PaymentProvider(Protocol):
    name: str

    def execute(
        self, authorized: AuthorizedAction, payment: PaymentSnapshot, now: datetime
    ) -> ProviderOutcome:
        """Carry out an authorized action and report what happened."""
        ...
