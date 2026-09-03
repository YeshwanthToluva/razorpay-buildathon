"""LLM-backed reasoner, over the Microsoft Agent Framework OpenAI client.

The framework earns its place here for exactly one thing: schema-constrained
responses against an OpenAI-compatible endpoint. No workflow, orchestration or
tool-calling machinery is used, because the orchestration that matters in this
system is the policy boundary, and that must stay outside any framework's
control.

This class can propose something catastrophic. That is permitted, and recorded.
It cannot execute anything, because it holds nothing executable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from agent_framework import Message
from pydantic import ValidationError
from agent_framework.openai import (
    OpenAIChatClient,
    OpenAIChatCompletionClient,
    OpenAIChatCompletionOptions,
    OpenAIChatOptions,
)

from afin.agent.prompts import SYSTEM_PROMPT, USER_TEMPLATE
from afin.agent.schema import AgentProposal, InvalidProposal, PROMPT_VERSION
from afin.config import LLMProfile, Settings
from afin.domain.models import CustomerSnapshot, PaymentSnapshot, ProposedAction


class LLMReasoner:
    prompt_version = PROMPT_VERSION

    def __init__(self, profile: LLMProfile | str = "gpt", temperature: float = 0.0):
        if isinstance(profile, str):
            profile = Settings.profile(profile)
        self.profile = profile
        self.model = profile.model
        self.name = f"llm:{profile.describe()}"
        self.temperature = temperature
        self.reasoning_effort = profile.reasoning_effort
        if profile.api_style == "responses":
            client_cls, self._options_cls = OpenAIChatClient, OpenAIChatOptions
        else:
            client_cls = OpenAIChatCompletionClient
            self._options_cls = OpenAIChatCompletionOptions
        self._client = client_cls(
            profile.model, api_key=profile.api_key, base_url=profile.base_url or None
        )

    def _render(
        self, payment: PaymentSnapshot, customer: CustomerSnapshot, now: datetime
    ) -> str:
        return USER_TEMPLATE.format(
            payment_id=payment.id,
            amount_minor=payment.amount_minor,
            amount_rupees=f"Rs {payment.amount_minor / 100:,.2f}",
            currency=payment.currency,
            failure_category=payment.failure_category,
            failure_code=payment.failure_code,
            payment_state=payment.payment_state,
            recovery_state=payment.recovery_state,
            retry_count=payment.retry_count,
            contact_count=payment.contact_count,
            is_disputed=payment.is_disputed,
            failed_at=payment.failed_at.isoformat(),
            window_expires_at=payment.window_expires_at.isoformat(),
            last_attempt_at=(
                payment.last_attempt_at.isoformat() if payment.last_attempt_at else "none"
            ),
            now=now.isoformat(),
            customer_id=customer.id,
            segment=customer.segment,
            opted_out=customer.opted_out,
            preferred_channel=customer.preferred_channel,
            lifetime_payments=customer.lifetime_payments,
            lifetime_failures=customer.lifetime_failures,
            prior_successful_payments=customer.prior_successful_payments,
            risk_flag=customer.risk_flag,
        )

    async def propose(
        self,
        payment: PaymentSnapshot,
        customer: CustomerSnapshot,
        now: datetime,
        feedback: Sequence[str] = (),
    ) -> ProposedAction:
        prompt = self._render(payment, customer, now)
        if feedback:
            blocked = "\n".join(f"  - {f}" for f in feedback)
            prompt += (
                "\nActions already blocked on this case:\n"
                f"{blocked}\n"
                "Propose a different action, or STOP_RECOVERY if no route remains.\n"
            )
        messages = [Message("system", SYSTEM_PROMPT), Message("user", prompt)]
        try:
            response = await self._client.get_response(
                messages,
                options=self._options_cls(
                    response_format=AgentProposal,
                    temperature=self.temperature,
                    # Options are a plain dict, so this reaches the request body
                    # and is honoured by providers that read it; the rest ignore
                    # it harmlessly.
                    reasoning_effort=self.reasoning_effort,
                ),
            )
        except ValidationError as exc:
            # Some providers ignore the schema and return prose. That is a
            # malformed proposal, not an infrastructure fault, and belongs in
            # the ledger as PROPOSAL_INVALID so the model can be held to account
            # for it in the metrics.
            raise InvalidProposal(
                f"provider returned output that is not a valid proposal: {exc.error_count()} error(s)"
            ) from exc

        # Some providers return a `reasoning_content` field carrying raw
        # chain-of-thought. It is read from nowhere and stored nowhere: only the
        # parsed structured fields below ever leave this method.
        proposal = getattr(response, "value", None)
        if isinstance(proposal, AgentProposal):
            return proposal.to_domain(payment.id)

        text = (response.text or "").strip()
        if not text:
            raise InvalidProposal("model returned an empty response")
        from afin.agent.schema import parse

        return parse(text, payment.id)
