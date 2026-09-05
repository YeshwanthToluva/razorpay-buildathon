"""Policy result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from afin.domain.enums import Decision, RiskLevel


class PolicyRule(StrEnum):
    """Identifier of the rule that decided. Recorded verbatim in the ledger."""

    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    PAYMENT_MISMATCH = "PAYMENT_MISMATCH"
    TERMINAL_STATE = "TERMINAL_STATE"
    SAFETY_VALVE = "SAFETY_VALVE"
    DISPUTE_BLOCK = "DISPUTE_BLOCK"
    FRAUD_HOLD = "FRAUD_HOLD"
    OPT_OUT_COMMUNICATION = "OPT_OUT_COMMUNICATION"
    RECIPIENT_NOT_ALLOWLISTED = "RECIPIENT_NOT_ALLOWLISTED"
    RECOVERY_WINDOW_EXPIRED = "RECOVERY_WINDOW_EXPIRED"
    MAX_RETRY_LIMIT = "MAX_RETRY_LIMIT"
    RETRY_COOLDOWN = "RETRY_COOLDOWN"
    ACTION_PRECONDITION = "ACTION_PRECONDITION"
    RISK_TYPE_PRECONDITION = "RISK_TYPE_PRECONDITION"
    CONTACT_BUDGET = "CONTACT_BUDGET"
    HIGH_VALUE_APPROVAL = "HIGH_VALUE_APPROVAL"
    PERMITTED = "PERMITTED"
    DEFAULT_DENY = "DEFAULT_DENY"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    decision: Decision
    reason: str
    policy: PolicyRule
    risk_level: RiskLevel
    #: Every rule consulted, in order, up to and including the deciding one.
    evaluated: tuple[PolicyRule, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "decision": self.decision.value,
            "reason": self.reason,
            "policy": self.policy.value,
            "risk_level": self.risk_level.value,
            "evaluated": [r.value for r in self.evaluated],
        }
