"""Immutable value objects passed across module boundaries.

Everything here is a frozen dataclass. The policy engine, the agent and the
simulator all receive *copies of data*, never live database handles — which is
what makes the policy engine pure and the LLM incapable of mutating anything.

Money is always an integer count of the currency's minor unit (paise for INR).
Floating-point money in a system whose headline metric is "revenue recovered"
would quietly corrupt the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from afin.domain.enums import (
    ActionType,
    Channel,
    CustomerRiskFlag,
    ExecutionResult,
    FailureCategory,
    PaymentState,
    RecoveryState,
    TERMINAL_PAYMENT_STATES,
    TERMINAL_RECOVERY_STATES,
)


@dataclass(frozen=True, slots=True)
class CustomerSnapshot:
    id: str
    segment: str
    opted_out: bool
    preferred_channel: Channel
    lifetime_payments: int
    lifetime_failures: int
    prior_successful_payments: int
    risk_flag: CustomerRiskFlag

    @property
    def failure_rate(self) -> float:
        """Historical failure rate; 0.0 when the customer has no payment history."""
        if self.lifetime_payments <= 0:
            return 0.0
        return self.lifetime_failures / self.lifetime_payments


@dataclass(frozen=True, slots=True)
class PaymentSnapshot:
    id: str
    customer_id: str
    invoice_id: str
    amount_minor: int
    currency: str
    payment_state: PaymentState
    recovery_state: RecoveryState
    failure_category: FailureCategory
    failure_code: str
    retry_count: int
    contact_count: int
    is_disputed: bool
    failed_at: datetime
    window_expires_at: datetime
    last_attempt_at: datetime | None
    recovered_amount_minor: int
    scenario_tag: str

    @property
    def is_terminal(self) -> bool:
        return (
            self.payment_state in TERMINAL_PAYMENT_STATES
            or self.recovery_state in TERMINAL_RECOVERY_STATES
        )


@dataclass(frozen=True, slots=True)
class ProviderOutcome:
    """The *only* thing that may change financial state.

    Obtainable only from a :class:`afin.simulator.base.PaymentProvider`, which is
    reachable only through the action gateway, which requires an
    ``AuthorizedAction``. That chain is the enforcement of "the LLM cannot
    execute" — it is a construction constraint, not a code-review convention.
    """

    result: ExecutionResult
    amount_recovered_minor: int
    failure_code: str | None
    provider_ref: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """A structured proposal from the agent. Data only — proposing is not doing.

    `action` is deliberately typed to admit a plain string. If the agent invents
    an action outside the closed space, that string must survive schema
    validation and reach the policy engine so the engine can deny it as
    UNSUPPORTED_ACTION and the ledger can record the proposal. Coercing it to a
    valid action, or rejecting it at the schema layer, would erase exactly the
    experimental observation we are trying to measure.
    """

    action: ActionType | str
    payment_id: str
    diagnosis: str
    reasoning_summary: str
    confidence: float
    scheduled_delay_hours: int | None = None
    channel: Channel | None = None
    #: The reasoner's structured answer as JSON, kept for the audit record.
    raw_json: str | None = None

    @property
    def action_type(self) -> ActionType | None:
        """The action as a domain enum, or None if the agent invented one."""
        if isinstance(self.action, ActionType):
            return self.action
        try:
            return ActionType(self.action)
        except ValueError:
            return None

    @property
    def action_label(self) -> str:
        """Human/audit-facing name, valid or not."""
        return self.action.value if isinstance(self.action, ActionType) else str(self.action)
