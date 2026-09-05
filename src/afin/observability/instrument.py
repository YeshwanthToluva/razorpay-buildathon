"""Tracing wrappers for the reasoner, the provider and the notifier.

Each wrapper satisfies the same protocol as the thing it wraps and forwards to
it unchanged, so instrumentation cannot alter a decision. Nothing in
src/afin/agent, policy or simulator knows tracing exists -- attach a wrapper and
the calls are recorded, omit it and the system behaves exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from afin.domain.models import (
    CustomerSnapshot,
    PaymentSnapshot,
    ProposedAction,
    ProviderOutcome,
)
from afin.observability.trace import Tracer
from afin.policy.authorization import AuthorizedAction


@dataclass
class TracedReasoner:
    """Records the model call: what was asked, what came back, how long."""

    inner: object
    tracer: Tracer

    @property
    def name(self) -> str:
        return getattr(self.inner, "name", "reasoner")

    @property
    def model(self):
        return getattr(self.inner, "model", None)

    def __getattr__(self, item):
        return getattr(self.inner, item)

    async def propose(
        self,
        payment: PaymentSnapshot,
        customer: CustomerSnapshot,
        now: datetime,
        feedback: Sequence[str] = (),
        cycle: int = 1,
        max_cycles: int = 4,
        attempts: Sequence[dict] = (),
    ) -> ProposedAction:
        kind = "model" if getattr(self.inner, "model", None) else "reasoning"
        with self.tracer.span(
            f"agent.propose", kind, payment_id=payment.id, cycle=cycle,
            reasoner=self.name, model=self.model,
        ) as span:
            span.input = {
                "risk_type": payment.risk_type.value,
                "failure_category": payment.failure_category.value,
                "amount_minor": payment.amount_minor,
                "retry_count": payment.retry_count,
                "contact_count": payment.contact_count,
                "opted_out": customer.opted_out,
                "prior_successful_payments": customer.prior_successful_payments,
                "prior_denials": list(feedback),
                "attempts_so_far": list(attempts),
                "prompt_version": getattr(self.inner, "prompt_version", None),
            }
            proposal = await self.inner.propose(
                payment, customer, now, feedback, cycle, max_cycles, attempts
            )
            span.output = {
                "action": proposal.action_label,
                "in_action_space": proposal.action_type is not None,
                "confidence": proposal.confidence,
                "diagnosis": proposal.diagnosis,
                "reasoning_summary": proposal.reasoning_summary,
                # The structured answer as parsed. Never chain-of-thought.
                "raw_response": proposal.raw_json,
            }
            span.attributes["retries_absorbed"] = getattr(self.inner, "retries", 0)
            return proposal


@dataclass
class TracedProvider:
    """Records the execution: which action reached the provider, and its result."""

    inner: object
    tracer: Tracer

    @property
    def name(self) -> str:
        return getattr(self.inner, "name", "provider")

    def execute(
        self, authorized: AuthorizedAction, payment: PaymentSnapshot, now: datetime
    ) -> ProviderOutcome:
        with self.tracer.span(
            "provider.execute", "provider", payment_id=payment.id,
            provider=self.name, action=authorized.action.value,
        ) as span:
            span.input = {
                "action": authorized.action.value,
                "authorised_by": authorized.decision.policy.value,
                "amount_minor": payment.amount_minor,
            }
            outcome = self.inner.execute(authorized, payment, now)
            span.output = {
                "result": outcome.result.value,
                "recovered_minor": outcome.amount_recovered_minor,
                "failure_code": outcome.failure_code,
                "provider_ref": outcome.provider_ref,
                "detail": outcome.detail,
            }
            return outcome


@dataclass
class TracedNotifier:
    """Records the outbound tool call: recipient, subject, delivery result."""

    inner: object
    tracer: Tracer

    @property
    def name(self) -> str:
        return getattr(self.inner, "name", "notifier")

    def send(self, message):
        with self.tracer.span(
            "tool.send_email", "tool", channel=self.name,
            tool_action="GMAIL_SEND_EMAIL",
        ) as span:
            span.input = {
                "to": message.to,
                "subject": message.subject,
                "redirected_from": message.redirected_from,
                "body_html": message.body_html,
            }
            result = self.inner.send(message)
            span.output = {
                "delivered": result.delivered,
                "channel": result.channel,
                "detail": result.detail,
                "provider_ref": result.provider_ref,
            }
            if not result.delivered:
                span.error = result.detail
            return result
