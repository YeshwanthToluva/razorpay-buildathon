"""Closed vocabularies for the recovery domain.

Every enum here is a *closed set*. An agent that names something outside these
sets has proposed an unsupported action, which the policy engine denies. This is
deliberate: the LLM must not be able to invent financial operations.
"""

from enum import StrEnum


class RiskType(StrEnum):
    """How the revenue came to be at risk.

    The brief covers three. They share one action space and one policy boundary,
    but they differ in what is mechanically possible: a failed payment has an
    authorised instrument that can be re-presented, an abandoned checkout never
    had one, and an overdue receivable only has one if a mandate exists.
    """

    PAYMENT_FAILURE = "PAYMENT_FAILURE"
    CHECKOUT_ABANDONMENT = "CHECKOUT_ABANDONMENT"
    OVERDUE_RECEIVABLE = "OVERDUE_RECEIVABLE"


#: Money the customer already owes, because something was delivered: a live
#: subscription, or goods and services already provided. Failing to recover it
#: is a collections loss, and it leaves an obligation to settle.
OWED_RISKS: frozenset[RiskType] = frozenset(
    {RiskType.PAYMENT_FAILURE, RiskType.OVERDUE_RECEIVABLE}
)

#: Revenue that would only have existed if the sale completed. Nothing was
#: delivered and nothing is owed, so failing to recover it is a lost sale rather
#: than a debt. Summing it with owed revenue overstates what is collectable.
PROSPECTIVE_RISKS: frozenset[RiskType] = frozenset({RiskType.CHECKOUT_ABANDONMENT})


#: Risk types where no payment instrument was ever authorised, so there is
#: nothing to re-present. Charging actions are impossible, not merely unwise.
NO_INSTRUMENT_ON_FILE: frozenset[RiskType] = frozenset({RiskType.CHECKOUT_ABANDONMENT})


class PaymentState(StrEnum):
    """Financial truth. Mutated only by :func:`afin.domain.transitions.apply_outcome`."""

    FAILED = "FAILED"
    RECOVERED = "RECOVERED"
    DISPUTED = "DISPUTED"
    REFUNDED = "REFUNDED"
    WRITTEN_OFF = "WRITTEN_OFF"


TERMINAL_PAYMENT_STATES: frozenset[PaymentState] = frozenset(
    {
        PaymentState.RECOVERED,
        PaymentState.DISPUTED,
        PaymentState.REFUNDED,
        PaymentState.WRITTEN_OFF,
    }
)


class RecoveryState(StrEnum):
    """Orchestration truth. Mutated by the deterministic reducer after each cycle."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_CUSTOMER = "AWAITING_CUSTOMER"
    ESCALATED = "ESCALATED"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"


# ESCALATED is terminal at Autonomy Level 2 because there is no human in the
# loop yet. At Levels 1 and 3 it becomes resumable. This frozenset is the seam.
TERMINAL_RECOVERY_STATES: frozenset[RecoveryState] = frozenset(
    {RecoveryState.COMPLETED, RecoveryState.STOPPED, RecoveryState.ESCALATED}
)


class FailureCategory(StrEnum):
    """Why the payment failed. Drives both simulator physics and agent diagnosis."""

    BANK_UNAVAILABLE = "BANK_UNAVAILABLE"
    PROCESSOR_ERROR = "PROCESSOR_ERROR"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    CARD_EXPIRED = "CARD_EXPIRED"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    DO_NOT_HONOR = "DO_NOT_HONOR"
    FRAUD_SUSPECTED = "FRAUD_SUSPECTED"
    # checkout abandonment
    CHECKOUT_DROPPED = "CHECKOUT_DROPPED"
    PAYMENT_METHOD_DECLINED_AT_CHECKOUT = "PAYMENT_METHOD_DECLINED_AT_CHECKOUT"
    # overdue receivables
    INVOICE_OVERDUE = "INVOICE_OVERDUE"
    MANDATE_ABSENT = "MANDATE_ABSENT"


#: Categories where re-presenting the *same* instrument can plausibly succeed.
#: CARD_EXPIRED and MANDATE_REVOKED are excluded: the instrument itself is dead,
#: so a retry is guaranteed waste and the policy engine denies it.
RETRYABLE_CATEGORIES: frozenset[FailureCategory] = frozenset(
    {
        FailureCategory.BANK_UNAVAILABLE,
        FailureCategory.PROCESSOR_ERROR,
        FailureCategory.INSUFFICIENT_FUNDS,
        FailureCategory.DO_NOT_HONOR,
        # An overdue invoice with a live mandate can still be collected.
        FailureCategory.INVOICE_OVERDUE,
    }
)


class ActionType(StrEnum):
    """The complete recovery action space."""

    RETRY_PAYMENT = "RETRY_PAYMENT"
    SCHEDULE_RETRY = "SCHEDULE_RETRY"
    SEND_PAYMENT_REMINDER = "SEND_PAYMENT_REMINDER"
    GENERATE_PAYMENT_LINK = "GENERATE_PAYMENT_LINK"
    REQUEST_HUMAN_REVIEW = "REQUEST_HUMAN_REVIEW"
    STOP_RECOVERY = "STOP_RECOVERY"


#: Actions that move money. Blocked outright on disputed or fraud-flagged payments.
FINANCIAL_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.RETRY_PAYMENT, ActionType.SCHEDULE_RETRY}
)

#: Actions that reach the customer. Blocked for opted-out customers.
COMMUNICATION_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.SEND_PAYMENT_REMINDER, ActionType.GENERATE_PAYMENT_LINK}
)

#: Always available while the case is open, regardless of every other rule.
#: The system must always be able to stop and always be able to escalate;
#: removing this guarantee would let policy trap a case with no legal exit.
SAFETY_VALVE_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.REQUEST_HUMAN_REVIEW, ActionType.STOP_RECOVERY}
)


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Decision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class ExecutionResult(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PENDING = "PENDING"
    REJECTED = "REJECTED"


class CustomerRiskFlag(StrEnum):
    NONE = "NONE"
    FRAUD_WATCH = "FRAUD_WATCH"


class Channel(StrEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    NONE = "NONE"
