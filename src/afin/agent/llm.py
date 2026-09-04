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

import asyncio
import random
import time
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

from afin.agent.prompts import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_TREATMENT,
    USER_TEMPLATE,
    USER_TEMPLATE_TREATMENT,
)
from afin.agent.schema import (
    AgentProposal,
    InvalidProposal,
    PROMPT_VERSION,
    PROMPT_VERSION_TREATMENT,
)
from afin.config import LLMProfile, Settings
from afin.domain.models import CustomerSnapshot, PaymentSnapshot, ProposedAction


class LLMReasoner:
    def __init__(self, profile: LLMProfile | str = "gpt", prompt: str = "control"):
        if isinstance(profile, str):
            profile = Settings.profile(profile)
        self.profile = profile
        if prompt not in ("control", "treatment"):
            raise ValueError(f"unknown prompt arm {prompt!r}")
        self.prompt_arm = prompt
        self.prompt_version = (
            PROMPT_VERSION if prompt == "control" else PROMPT_VERSION_TREATMENT
        )
        self.model = profile.model
        self.name = f"llm:{profile.describe()}"
        self.temperature = profile.temperature
        self.reasoning_effort = profile.reasoning_effort
        #: Transient provider faults absorbed by _request during this run.
        self.retries = 0
        self._pace_lock = asyncio.Lock()
        self._next_allowed = 0.0
        if profile.api_style == "responses":
            client_cls, self._options_cls = OpenAIChatClient, OpenAIChatOptions
        else:
            client_cls = OpenAIChatCompletionClient
            self._options_cls = OpenAIChatCompletionOptions
        self._client = client_cls(
            profile.model, api_key=profile.api_key, base_url=profile.base_url or None
        )

    def _render(
        self,
        payment: PaymentSnapshot,
        customer: CustomerSnapshot,
        now: datetime,
        cycle: int,
        max_cycles: int,
    ) -> str:
        if self.prompt_arm == "treatment":
            return self._render_treatment(payment, customer, now, cycle, max_cycles)
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

    def _render_treatment(
        self,
        payment: PaymentSnapshot,
        customer: CustomerSnapshot,
        now: datetime,
        cycle: int,
        max_cycles: int,
    ) -> str:
        from afin.domain.enums import RETRYABLE_CATEGORIES
        from afin.policy.config import DEFAULT_POLICY_CONFIG

        reusable = payment.failure_category in RETRYABLE_CATEGORIES
        remaining = max(DEFAULT_POLICY_CONFIG.max_retries - payment.retry_count, 0)
        return USER_TEMPLATE_TREATMENT.format(
            payment_id=payment.id,
            amount_minor=payment.amount_minor,
            amount_rupees=f"Rs {payment.amount_minor / 100:,.2f}",
            payment_state=payment.payment_state,
            recovery_state=payment.recovery_state,
            failure_category=payment.failure_category,
            failure_code=payment.failure_code,
            instrument_reusable=(
                "yes - this instrument can be re-presented"
                if reusable
                else "no - this instrument is permanently unusable"
            ),
            is_disputed=payment.is_disputed,
            retry_count=payment.retry_count,
            retries_remaining=remaining,
            contact_count=payment.contact_count,
            cycle=cycle,
            max_cycles=max_cycles,
            failed_at=payment.failed_at.isoformat(),
            last_attempt_at=(
                payment.last_attempt_at.isoformat() if payment.last_attempt_at else "none"
            ),
            window_expires_at=payment.window_expires_at.isoformat(),
            now=now.isoformat(),
            customer_id=customer.id,
            segment=customer.segment,
            opted_out=customer.opted_out,
            preferred_channel=customer.preferred_channel,
            prior_successful_payments=customer.prior_successful_payments,
            lifetime_payments=customer.lifetime_payments,
            lifetime_failures=customer.lifetime_failures,
            risk_flag=customer.risk_flag,
        )

    #: Provider faults worth retrying: rate limits and transient server errors.
    #: A schema violation is deliberately NOT here -- that is the model's actual
    #: answer, and resampling it would quietly retry until the model looked
    #: better than it is, corrupting the invalid-proposal metric.
    RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
    MAX_ATTEMPTS = 8

    @staticmethod
    def _status_of(exc: Exception) -> int | None:
        for candidate in (exc, getattr(exc, "__cause__", None)):
            code = getattr(candidate, "status_code", None)
            if isinstance(code, int):
                return code
        text = str(exc)
        for code in (429, 503, 502, 500, 504, 408):
            if f"Error code: {code}" in text or f"'status': {code}" in text:
                return code
        return None

    def _options(self) -> dict:
        """Request options, omitting anything the profile did not ask for.

        Optional parameters are not free to send. LiteLLM rejects
        temperature=0.0 alongside reasoning_effort on gpt-5 models with a 400,
        so sending both unconditionally broke every request to that gateway.
        """
        opts: dict = {"response_format": AgentProposal}
        if self.temperature is not None:
            opts["temperature"] = self.temperature
        if self.reasoning_effort:
            opts["reasoning_effort"] = self.reasoning_effort
        return opts

    async def _pace(self) -> None:
        """Hold requests to the profile's minimum interval.

        Concurrent cases share one reasoner, so this is where a rate limit is
        actually respected. Without it, six cases in flight fire six requests at
        once, take six 429s, and spend the retry budget learning a limit that
        was knowable in advance.
        """
        if self.profile.min_interval_seconds <= 0:
            return
        async with self._pace_lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self.profile.min_interval_seconds

    async def _request(self, messages):
        """Call the provider, retrying transient faults with backoff.

        Without this a free-tier rate limit turns an experiment into noise: a
        run in which 40 of 50 cases errored still reports a recovery rate, and
        that number reads like a finding rather than an outage.
        """
        last: Exception | None = None
        for attempt in range(self.MAX_ATTEMPTS):
            await self._pace()
            try:
                return await self._client.get_response(
                    messages, options=self._options_cls(**self._options())
                )
            except ValidationError:
                raise
            except Exception as exc:  # noqa: BLE001
                status = self._status_of(exc)
                if status not in self.RETRYABLE_STATUS or attempt == self.MAX_ATTEMPTS - 1:
                    raise
                last = exc
                self.retries += 1
                await asyncio.sleep(min(2.0 ** attempt, 60.0) + random.uniform(0, 1.5))
        raise last if last else RuntimeError("unreachable")

    async def propose(
        self,
        payment: PaymentSnapshot,
        customer: CustomerSnapshot,
        now: datetime,
        feedback: Sequence[str] = (),
        cycle: int = 1,
        max_cycles: int = 4,
    ) -> ProposedAction:
        prompt = self._render(payment, customer, now, cycle, max_cycles)
        if feedback:
            blocked = "\n".join(f"  - {f}" for f in feedback)
            prompt += (
                "\nActions already blocked on this case:\n"
                f"{blocked}\n"
                "Propose a different action, or STOP_RECOVERY if no route remains.\n"
            )
        system = SYSTEM_PROMPT if self.prompt_arm == "control" else SYSTEM_PROMPT_TREATMENT
        messages = [Message("system", system), Message("user", prompt)]
        try:
            response = await self._request(messages)
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
