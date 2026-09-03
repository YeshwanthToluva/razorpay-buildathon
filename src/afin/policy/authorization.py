"""Minting of execution authority.

The action gateway will not touch a payment provider without an
`AuthorizedAction`, and an `AuthorizedAction` cannot be constructed outside this
module: its initialiser demands a private token. So "the agent bypassed policy"
is not a code-review finding one might miss, it is a `PolicyBypassError` at the
moment of the attempt.

This is the load-bearing line of the whole architecture:

    a bad LLM decision is an experimental observation;
    a bad LLM decision reaching the execution layer is an architectural failure.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass

from afin.domain.enums import ActionType
from afin.domain.models import ProposedAction
from afin.policy.decisions import PolicyDecision
from afin.policy.engine import PolicyRequest, evaluate

#: Private capability token. Holding it is what confers the right to authorize.
_MINT_TOKEN = object()


class PolicyBypassError(RuntimeError):
    """Raised on an attempt to forge execution authority."""


@dataclass(frozen=True, slots=True)
class AuthorizedAction:
    """Proof that the policy engine allowed this specific action on this payment."""

    _token: InitVar[object]
    action: ActionType
    payment_id: str
    proposal: ProposedAction
    decision: PolicyDecision

    def __post_init__(self, _token: object) -> None:
        if _token is not _MINT_TOKEN:
            raise PolicyBypassError(
                "AuthorizedAction may only be minted by afin.policy.authorization.authorize"
            )


def authorize(request: PolicyRequest) -> tuple[PolicyDecision, AuthorizedAction | None]:
    """Evaluate a proposal and, only on ALLOW, mint the authority to execute it.

    Returns the decision unconditionally -- a denial is data the ledger needs,
    not an error -- and an AuthorizedAction only when execution is permitted.
    """
    decision = evaluate(request)
    if not decision.allowed:
        return decision, None

    action = request.proposal.action_type
    if action is None:  # unreachable: an invalid action can never be allowed
        raise PolicyBypassError("policy allowed an action outside the action space")

    return decision, AuthorizedAction(
        _MINT_TOKEN,
        action=action,
        payment_id=request.payment.id,
        proposal=request.proposal,
        decision=decision,
    )
