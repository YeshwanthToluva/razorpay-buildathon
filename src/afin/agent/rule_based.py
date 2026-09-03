"""Deterministic reasoner: the Autonomy Level 0 control arm.

This exists so "did the LLM help?" is answerable. Without a deterministic
baseline running the identical dataset, simulator seed and policy, an agent's
recovery rate is a number with nothing to compare against.

It encodes the obvious heuristics a competent rules engine would use, and
nothing more. Beating it is the bar the agent has to clear.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from afin.domain.enums import ActionType, FailureCategory, RETRYABLE_CATEGORIES
from afin.domain.models import CustomerSnapshot, PaymentSnapshot, ProposedAction


class RuleBasedReasoner:
    name = "rule-based-v1"
    model = None

    async def propose(
        self,
        payment: PaymentSnapshot,
        customer: CustomerSnapshot,
        now: datetime,
        feedback: Sequence[str] = (),
    ) -> ProposedAction:
        if feedback:
            # The control arm has one heuristic and no second idea. Being
            # blocked means it is out of moves, and pretending otherwise
            # would flatter the baseline the agent is measured against.
            return ProposedAction(
                action=ActionType.STOP_RECOVERY,
                payment_id=payment.id,
                diagnosis="policy blocked the indicated action",
                reasoning_summary=(
                    "The rule set has no alternative once its chosen action is "
                    "denied, so the case is closed rather than retried."
                ),
                confidence=0.5,
            )
        action, delay, diagnosis, why = self._decide(payment, customer, now)
        return ProposedAction(
            action=action,
            payment_id=payment.id,
            diagnosis=diagnosis,
            reasoning_summary=why,
            confidence=0.5,
            scheduled_delay_hours=delay,
            channel=customer.preferred_channel,
        )

    def _decide(self, payment: PaymentSnapshot, customer: CustomerSnapshot, now: datetime):
        cat = payment.failure_category

        if payment.is_disputed or cat is FailureCategory.FRAUD_SUSPECTED:
            return (
                ActionType.REQUEST_HUMAN_REVIEW,
                None,
                "dispute or fraud signal present",
                "Cases carrying a dispute or fraud signal are routed to a human.",
            )

        if cat not in RETRYABLE_CATEGORIES:
            # The instrument is dead; the only route to money is a new one.
            if customer.opted_out:
                return (
                    ActionType.STOP_RECOVERY,
                    None,
                    f"{cat} with no permitted contact channel",
                    "The instrument cannot be re-presented and the customer "
                    "cannot be contacted, so no recovery route remains.",
                )
            return (
                ActionType.GENERATE_PAYMENT_LINK,
                None,
                f"{cat} requires a new payment method",
                "Re-presenting a dead instrument cannot succeed; a link lets "
                "the customer supply a different method.",
            )

        if payment.retry_count == 0:
            return (
                ActionType.RETRY_PAYMENT,
                None,
                f"{cat} on first failure",
                "No automated attempt has been made yet and the instrument is live.",
            )

        if payment.retry_count < 3:
            return (
                ActionType.SCHEDULE_RETRY,
                24,
                f"{cat} persisting after {payment.retry_count} attempt(s)",
                "An immediate re-presentment has already failed, so the next "
                "attempt is deferred.",
            )

        if not customer.opted_out:
            return (
                ActionType.GENERATE_PAYMENT_LINK,
                None,
                "automated retries exhausted",
                "The retry budget is spent; the customer is asked to pay directly.",
            )

        return (
            ActionType.STOP_RECOVERY,
            None,
            "retries exhausted and contact not permitted",
            "No further automated route to recovery remains.",
        )
