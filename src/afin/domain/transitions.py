"""The single place where financial state changes.

`apply_outcome` is a pure reducer: (payment, action, provider outcome) -> new
payment. It is the only function in the system that produces a PaymentSnapshot
with a different `payment_state`, and it cannot be called without a
ProviderOutcome, which cannot be obtained without policy authorization.

Splitting state in two is deliberate:

  payment_state   financial truth   -- did we get the money?
  recovery_state  orchestration     -- what is the workflow doing?

Conflating them is the usual mistake in recovery systems: it makes "we stopped
trying" indistinguishable from "the money is gone", and the second is a
write-off that belongs in a financial report.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from afin.domain.enums import (
    ActionType,
    COMMUNICATION_ACTIONS,
    ExecutionResult,
    PaymentState,
    RecoveryState,
    RiskType,
)
from afin.domain.models import PaymentSnapshot, ProviderOutcome

#: Actions that consume one unit of the retry budget when executed.
_RETRY_CONSUMING = frozenset({ActionType.RETRY_PAYMENT, ActionType.SCHEDULE_RETRY})


class TerminalStateError(RuntimeError):
    """Raised when an outcome is applied to an already-terminal payment."""


def apply_outcome(
    payment: PaymentSnapshot,
    action: ActionType,
    outcome: ProviderOutcome,
    now: datetime,
) -> PaymentSnapshot:
    """Return the payment as it stands after `action` produced `outcome`.

    Raises TerminalStateError if the payment was already terminal: reaching this
    function with a closed case means the orchestrator or the policy engine
    failed, and silently absorbing it would hide the defect.
    """
    if payment.is_terminal:
        raise TerminalStateError(
            f"{payment.id} is terminal "
            f"({payment.payment_state}/{payment.recovery_state}); "
            f"cannot apply outcome of {action}"
        )

    changes: dict[str, object] = {}

    if action in _RETRY_CONSUMING and outcome.result is not ExecutionResult.REJECTED:
        changes["retry_count"] = payment.retry_count + 1
        changes["last_attempt_at"] = now

    if action in COMMUNICATION_ACTIONS and outcome.result is not ExecutionResult.REJECTED:
        changes["contact_count"] = payment.contact_count + 1

    if outcome.amount_recovered_minor > 0:
        # Money actually arrived. This is the only path to RECOVERED.
        changes["payment_state"] = PaymentState.RECOVERED
        changes["recovery_state"] = RecoveryState.COMPLETED
        changes["recovered_amount_minor"] = (
            payment.recovered_amount_minor + outcome.amount_recovered_minor
        )
    elif action is ActionType.STOP_RECOVERY:
        # We gave up. The money is not recovered and never will be by this system.
        changes["payment_state"] = PaymentState.WRITTEN_OFF
        changes["recovery_state"] = RecoveryState.STOPPED
    elif action is ActionType.REQUEST_HUMAN_REVIEW:
        # Financial state is untouched: escalation hands the case to a human,
        # it does not resolve or abandon it.
        changes["recovery_state"] = RecoveryState.ESCALATED
    elif action in COMMUNICATION_ACTIONS and outcome.result is ExecutionResult.SUCCESS:
        changes["recovery_state"] = RecoveryState.AWAITING_CUSTOMER
    else:
        changes["recovery_state"] = RecoveryState.IN_PROGRESS

    return dataclasses.replace(payment, **changes)  # type: ignore[arg-type]


def exhaust(payment: PaymentSnapshot, reason: str) -> PaymentSnapshot:
    """Close a case that policy will permit no further action on.

    Used by the orchestrator when every remaining action is denied, so that a
    payment can never sit open forever. `reason` is recorded by the caller in
    the audit ledger, not on the payment row.
    """
    if payment.is_terminal:
        return payment
    return dataclasses.replace(
        payment,
        payment_state=PaymentState.WRITTEN_OFF,
        recovery_state=RecoveryState.STOPPED,
    )


#: What it actually means for the business when a case closes without the money.
#: Kept in the domain rather than the UI because it is a statement about the
#: obligation, not about presentation: an abandoned checkout leaves no debt, an
#: unpaid subscription leaves service running that is not being paid for, and a
#: delivered invoice leaves a genuine receivable to write off or pursue.
CONSEQUENCE_UNRECOVERED: dict[RiskType, str] = {
    RiskType.PAYMENT_FAILURE: (
        "The subscription is live but unpaid. Nothing further is owed to us "
        "automatically, so the service now has to be suspended or downgraded — "
        "otherwise we keep delivering it for free."
    ),
    RiskType.CHECKOUT_ABANDONMENT: (
        "No sale was made. Nothing was delivered and nothing is owed by either "
        "side, so the customer simply does not get the product. This is lost "
        "pipeline, not an unpaid debt."
    ),
    RiskType.OVERDUE_RECEIVABLE: (
        "The goods or services were already delivered and remain unpaid, so this "
        "is a genuine receivable. It goes to write-off or to collections."
    ),
}

CONSEQUENCE_RECOVERED: dict[RiskType, str] = {
    RiskType.PAYMENT_FAILURE: "The subscription is paid and stays active.",
    RiskType.CHECKOUT_ABANDONMENT: "The sale completed and the product is delivered.",
    RiskType.OVERDUE_RECEIVABLE: "The invoice is settled and clears from receivables.",
}


def consequence(risk_type: RiskType, recovered: bool) -> str:
    """What happens next, in business terms, once a case is closed."""
    table = CONSEQUENCE_RECOVERED if recovered else CONSEQUENCE_UNRECOVERED
    return table.get(risk_type, "")
