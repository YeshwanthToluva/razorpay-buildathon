"""The Action Gateway: the only route from a proposal to the outside world.

Every capability request, whatever its origin -- the LLM, the rule-based
baseline, or the orchestrator itself -- passes through this one function. There
is no second path to a provider. When external tools arrive later, they attach
here, behind the same policy call, rather than beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from afin.domain.models import (
    CustomerSnapshot,
    PaymentSnapshot,
    ProposedAction,
    ProviderOutcome,
)
from afin.policy.authorization import authorize
from afin.policy.config import PolicyConfig
from afin.policy.decisions import PolicyDecision
from afin.policy.engine import PolicyRequest
from afin.simulator.base import PaymentProvider


@dataclass(frozen=True, slots=True)
class GatewayResult:
    decision: PolicyDecision
    #: None whenever policy withheld authority. Absence of an outcome is the
    #: proof that nothing executed.
    outcome: ProviderOutcome | None

    @property
    def executed(self) -> bool:
        return self.outcome is not None


def submit(
    proposal: ProposedAction,
    payment: PaymentSnapshot,
    customer: CustomerSnapshot,
    provider: PaymentProvider,
    now: datetime,
    config: PolicyConfig,
) -> GatewayResult:
    """Evaluate a proposal and execute it only if policy minted authority."""
    decision, authorized = authorize(
        PolicyRequest(
            proposal=proposal, payment=payment, customer=customer, now=now, config=config
        )
    )
    if authorized is None:
        return GatewayResult(decision=decision, outcome=None)

    return GatewayResult(
        decision=decision, outcome=provider.execute(authorized, payment, now)
    )
