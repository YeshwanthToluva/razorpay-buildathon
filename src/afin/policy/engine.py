"""The deterministic safety boundary.

Properties this module must hold, and which the tests enforce:

  pure           no I/O, no clock, no randomness. `now` and `config` are injected.
  deterministic  identical input -> identical output, always.
  authoritative  nothing executes without an ALLOW from here.
  independent    imports nothing from the agent or the LLM.

Rules are evaluated in a fixed order and the *first DENY wins*, so the ledger
attributes a block to the most severe applicable cause. A payment that is both
disputed and past its recovery window reports DISPUTE_BLOCK, not
RECOVERY_WINDOW_EXPIRED.

The list ends in DEFAULT_DENY. Anything not explicitly permitted is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from afin.domain.enums import (
    ActionType,
    COMMUNICATION_ACTIONS,
    CustomerRiskFlag,
    Decision,
    FINANCIAL_ACTIONS,
    FailureCategory,
    NO_INSTRUMENT_ON_FILE,
    RETRYABLE_CATEGORIES,
    RiskType,
    RiskLevel,
    SAFETY_VALVE_ACTIONS,
)
from afin.domain.models import (
    CustomerSnapshot,
    PaymentSnapshot,
    ProposedAction,
    format_minor,
)
from afin.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from afin.policy.decisions import PolicyDecision, PolicyRule


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    proposal: ProposedAction
    payment: PaymentSnapshot
    customer: CustomerSnapshot
    now: datetime
    config: PolicyConfig = DEFAULT_POLICY_CONFIG


def _deny(rule: PolicyRule, reason: str, risk: RiskLevel) -> PolicyDecision:
    return PolicyDecision(
        allowed=False, decision=Decision.DENY, reason=reason, policy=rule, risk_level=risk
    )


def _allow(rule: PolicyRule, reason: str, risk: RiskLevel) -> PolicyDecision:
    return PolicyDecision(
        allowed=True, decision=Decision.ALLOW, reason=reason, policy=rule, risk_level=risk
    )


def _approval(rule: PolicyRule, reason: str, risk: RiskLevel) -> PolicyDecision:
    # REQUIRE_APPROVAL is not an allow. At Autonomy Level 2 there is no human in
    # the loop, so it does not execute; it routes the case to escalation.
    return PolicyDecision(
        allowed=False,
        decision=Decision.REQUIRE_APPROVAL,
        reason=reason,
        policy=rule,
        risk_level=risk,
    )


# --------------------------------------------------------------------------
# Rules. Each returns a decision to stop on, or None to fall through.
# --------------------------------------------------------------------------


def _rule_unsupported_action(r: PolicyRequest) -> PolicyDecision | None:
    if r.proposal.action_type is None:
        return _deny(
            PolicyRule.UNSUPPORTED_ACTION,
            f"'{r.proposal.action_label}' is not in the recovery action space",
            RiskLevel.CRITICAL,
        )
    return None


def _rule_payment_mismatch(r: PolicyRequest) -> PolicyDecision | None:
    # The agent proposing an action against a payment it was not shown is an
    # attempt to act outside its assigned scope, whatever the intent.
    if r.proposal.payment_id != r.payment.id:
        return _deny(
            PolicyRule.PAYMENT_MISMATCH,
            f"proposal targets {r.proposal.payment_id!r} but the case under "
            f"evaluation is {r.payment.id!r}",
            RiskLevel.CRITICAL,
        )
    if r.payment.customer_id != r.customer.id:
        return _deny(
            PolicyRule.PAYMENT_MISMATCH,
            "customer context does not belong to the payment under evaluation",
            RiskLevel.CRITICAL,
        )
    return None


def _rule_terminal_state(r: PolicyRequest) -> PolicyDecision | None:
    if r.payment.is_terminal:
        return _deny(
            PolicyRule.TERMINAL_STATE,
            f"case is closed ({r.payment.payment_state}/{r.payment.recovery_state}); "
            "no further recovery transitions are permitted",
            RiskLevel.HIGH,
        )
    return None


def _rule_safety_valve(r: PolicyRequest) -> PolicyDecision | None:
    # Stopping and escalating must always remain available on an open case,
    # otherwise policy could trap a payment with no legal exit.
    if r.proposal.action_type in SAFETY_VALVE_ACTIONS:
        return _allow(
            PolicyRule.SAFETY_VALVE,
            f"{r.proposal.action_label} is always permitted on an open case",
            RiskLevel.LOW,
        )
    return None


def _rule_dispute_block(r: PolicyRequest) -> PolicyDecision | None:
    if r.payment.is_disputed:
        return _deny(
            PolicyRule.DISPUTE_BLOCK,
            "payment is disputed; automated recovery is barred until the "
            "dispute is resolved",
            RiskLevel.CRITICAL,
        )
    return None


def _rule_fraud_hold(r: PolicyRequest) -> PolicyDecision | None:
    fraud = (
        r.payment.failure_category is FailureCategory.FRAUD_SUSPECTED
        or r.customer.risk_flag is CustomerRiskFlag.FRAUD_WATCH
    )
    if fraud:
        return _deny(
            PolicyRule.FRAUD_HOLD,
            "fraud signal present; automated recovery is barred pending review",
            RiskLevel.CRITICAL,
        )
    return None


def _rule_opt_out(r: PolicyRequest) -> PolicyDecision | None:
    if r.customer.opted_out and r.proposal.action_type in COMMUNICATION_ACTIONS:
        return _deny(
            PolicyRule.OPT_OUT_COMMUNICATION,
            "customer has opted out of recovery communication",
            RiskLevel.HIGH,
        )
    return None


def _rule_recovery_window(r: PolicyRequest) -> PolicyDecision | None:
    if r.now >= r.payment.window_expires_at:
        return _deny(
            PolicyRule.RECOVERY_WINDOW_EXPIRED,
            f"recovery window closed at {r.payment.window_expires_at.isoformat()}",
            RiskLevel.MEDIUM,
        )
    return None


def _rule_max_retry(r: PolicyRequest) -> PolicyDecision | None:
    if r.proposal.action_type in FINANCIAL_ACTIONS:
        if r.payment.retry_count >= r.config.max_retries:
            return _deny(
                PolicyRule.MAX_RETRY_LIMIT,
                f"retry budget exhausted ({r.payment.retry_count} of "
                f"{r.config.max_retries} used)",
                RiskLevel.MEDIUM,
            )
    return None


def _rule_retry_cooldown(r: PolicyRequest) -> PolicyDecision | None:
    if r.proposal.action_type is not ActionType.RETRY_PAYMENT:
        return None
    last = r.payment.last_attempt_at
    if last is None:
        return None
    cooldown = timedelta(hours=r.config.retry_cooldown_hours)
    if r.now - last < cooldown:
        return _deny(
            PolicyRule.RETRY_COOLDOWN,
            f"last attempt was under the {r.config.retry_cooldown_hours}h "
            "cooldown; an immediate re-presentment is likely to be refused",
            RiskLevel.LOW,
        )
    return None


def _rule_risk_type_precondition(r: PolicyRequest) -> PolicyDecision | None:
    """What the risk type makes mechanically possible.

    An abandoned checkout never authorised an instrument, so there is nothing to
    re-present: a retry is not a bad idea there, it is an impossible one. An
    overdue receivable can be charged only where a mandate exists. Encoding this
    in policy rather than in the prompt means an agent that gets it wrong is
    stopped rather than trusted.
    """
    action = r.proposal.action_type
    if action not in FINANCIAL_ACTIONS:
        return None
    if r.payment.risk_type in NO_INSTRUMENT_ON_FILE:
        return _deny(
            PolicyRule.RISK_TYPE_PRECONDITION,
            f"{r.payment.risk_type} never authorised a payment instrument; "
            "there is nothing to re-present. The customer must complete payment.",
            RiskLevel.HIGH,
        )
    if (
        r.payment.risk_type is RiskType.OVERDUE_RECEIVABLE
        and r.payment.failure_category is FailureCategory.MANDATE_ABSENT
    ):
        return _deny(
            PolicyRule.RISK_TYPE_PRECONDITION,
            "this receivable has no active mandate, so it cannot be collected "
            "automatically; the customer must be invoiced or sent a link",
            RiskLevel.HIGH,
        )
    return None


def _rule_action_precondition(r: PolicyRequest) -> PolicyDecision | None:
    action = r.proposal.action_type
    category = r.payment.failure_category

    if action in FINANCIAL_ACTIONS and category not in RETRYABLE_CATEGORIES:
        # The instrument itself is dead. Retrying it cannot succeed, so it is
        # pure cost: issuer friction for a guaranteed decline.
        return _deny(
            PolicyRule.ACTION_PRECONDITION,
            f"{category} cannot be resolved by re-presenting the same "
            "instrument; the customer must supply a new payment method",
            RiskLevel.MEDIUM,
        )

    if action is ActionType.SCHEDULE_RETRY:
        delay = r.proposal.scheduled_delay_hours
        if delay is None or delay <= 0:
            return _deny(
                PolicyRule.ACTION_PRECONDITION,
                "SCHEDULE_RETRY requires a positive scheduled_delay_hours",
                RiskLevel.LOW,
            )
        if delay > r.config.max_schedule_delay_hours:
            return _deny(
                PolicyRule.ACTION_PRECONDITION,
                f"scheduled delay {delay}h exceeds the "
                f"{r.config.max_schedule_delay_hours}h horizon",
                RiskLevel.LOW,
            )
        if r.now + timedelta(hours=delay) >= r.payment.window_expires_at:
            return _deny(
                PolicyRule.ACTION_PRECONDITION,
                "scheduled retry would land after the recovery window closes",
                RiskLevel.MEDIUM,
            )
    return None


def _rule_contact_budget(r: PolicyRequest) -> PolicyDecision | None:
    if r.proposal.action_type in COMMUNICATION_ACTIONS:
        if r.payment.contact_count >= r.config.max_contacts:
            return _deny(
                PolicyRule.CONTACT_BUDGET,
                f"contact budget exhausted ({r.payment.contact_count} of "
                f"{r.config.max_contacts} used)",
                RiskLevel.LOW,
            )
    return None


def _rule_high_value(r: PolicyRequest) -> PolicyDecision | None:
    if r.payment.amount_minor > r.config.high_value_threshold_minor:
        return _approval(
            PolicyRule.HIGH_VALUE_APPROVAL,
            f"{format_minor(r.payment.amount_minor, r.payment.currency)} exceeds the "
            f"automated ceiling of "
            f"{format_minor(r.config.high_value_threshold_minor, r.payment.currency)}, "
            f"so this needs a person to approve it",
            RiskLevel.HIGH,
        )
    return None


#: Evaluation order is load-bearing. See module docstring.
RULES = (
    (PolicyRule.UNSUPPORTED_ACTION, _rule_unsupported_action),
    (PolicyRule.PAYMENT_MISMATCH, _rule_payment_mismatch),
    (PolicyRule.TERMINAL_STATE, _rule_terminal_state),
    (PolicyRule.SAFETY_VALVE, _rule_safety_valve),
    (PolicyRule.DISPUTE_BLOCK, _rule_dispute_block),
    (PolicyRule.FRAUD_HOLD, _rule_fraud_hold),
    (PolicyRule.OPT_OUT_COMMUNICATION, _rule_opt_out),
    (PolicyRule.RECOVERY_WINDOW_EXPIRED, _rule_recovery_window),
    (PolicyRule.MAX_RETRY_LIMIT, _rule_max_retry),
    (PolicyRule.RETRY_COOLDOWN, _rule_retry_cooldown),
    (PolicyRule.RISK_TYPE_PRECONDITION, _rule_risk_type_precondition),
    (PolicyRule.ACTION_PRECONDITION, _rule_action_precondition),
    (PolicyRule.CONTACT_BUDGET, _rule_contact_budget),
    (PolicyRule.HIGH_VALUE_APPROVAL, _rule_high_value),
)

#: Actions that survive every rule above are permitted. Anything not listed
#: falls through to DEFAULT_DENY, so adding an ActionType without deciding its
#: policy makes it unusable rather than silently permitted.
_EXPLICITLY_PERMITTED = frozenset(
    {
        ActionType.RETRY_PAYMENT,
        ActionType.SCHEDULE_RETRY,
        ActionType.SEND_PAYMENT_REMINDER,
        ActionType.GENERATE_PAYMENT_LINK,
    }
)


def evaluate(request: PolicyRequest) -> PolicyDecision:
    """Decide whether `request.proposal` may execute. Pure and total."""
    trace: list[PolicyRule] = []
    for rule_id, rule in RULES:
        trace.append(rule_id)
        decision = rule(request)
        if decision is not None:
            return PolicyDecision(
                allowed=decision.allowed,
                decision=decision.decision,
                reason=decision.reason,
                policy=decision.policy,
                risk_level=decision.risk_level,
                evaluated=tuple(trace),
            )

    action = request.proposal.action_type
    if action in _EXPLICITLY_PERMITTED:
        trace.append(PolicyRule.PERMITTED)
        return PolicyDecision(
            allowed=True,
            decision=Decision.ALLOW,
            reason=f"{action} is permitted under {request.config.version}",
            policy=PolicyRule.PERMITTED,
            risk_level=RiskLevel.LOW,
            evaluated=tuple(trace),
        )

    trace.append(PolicyRule.DEFAULT_DENY)
    return PolicyDecision(
        allowed=False,
        decision=Decision.DENY,
        reason=f"{action} is not explicitly permitted; denying by default",
        policy=PolicyRule.DEFAULT_DENY,
        risk_level=RiskLevel.HIGH,
        evaluated=tuple(trace),
    )
