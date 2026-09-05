"""What the agent is allowed to say to a customer.

Choosing an action and composing a message are different capabilities with
different risks. The action space is closed, so an invented action is caught by
UNSUPPORTED_ACTION. Prose is open, and a model writing to a customer about money
can promise a refund nobody authorised, invent a bank policy, or state an amount
that is not the amount owed.

This module is the same idea as the action policy applied to language: pure,
deterministic, and authoritative over what the model produced. It does not
improve a message. It decides whether the message may be sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class ContentRule(StrEnum):
    UNAUTHORISED_COMMITMENT = "UNAUTHORISED_COMMITMENT"
    FABRICATED_AMOUNT = "FABRICATED_AMOUNT"
    MISSING_REQUIRED_FACT = "MISSING_REQUIRED_FACT"
    PRESSURE_OR_THREAT = "PRESSURE_OR_THREAT"
    ASKS_FOR_CREDENTIALS = "ASKS_FOR_CREDENTIALS"
    LEAKS_INTERNALS = "LEAKS_INTERNALS"
    OVERLONG = "OVERLONG"
    PERMITTED = "PERMITTED"


#: Things nobody authorised the agent to offer. Written as phrases rather than
#: single words so "we cannot waive this" is not mistaken for an offer to waive.
COMMITMENTS = (
    r"\bwe (?:will|can|shall) (?:refund|waive|cancel|write off|forgive)\b",
    r"\b(?:refund|waive|waiver|discount|credit) (?:will|has) been (?:issued|applied|approved)\b",
    r"\bno (?:charge|fee|interest) will (?:ever )?apply\b",
    r"\bguarantee[ds]?\b",
    r"\bwe promise\b",
)

#: Collections pressure. Not our tone, and in several jurisdictions not legal.
PRESSURE = (
    r"\blegal action\b", r"\bdebt collector\b", r"\bcredit (?:score|bureau)\b",
    r"\bimmediately or\b", r"\bfinal (?:notice|warning)\b", r"\bfailure to pay will\b",
    r"\bsuspend your account\b",
)

#: A recovery message never needs these, and asking is indistinguishable from
#: phishing to the person receiving it.
CREDENTIALS = (
    r"\b(?:card|cvv|pin|otp|password|full card number)\b.{0,40}\b(?:reply|send|share|confirm)\b",
    r"\b(?:reply|send|share|confirm)\b.{0,40}\b(?:cvv|pin|otp|password|card number)\b",
)

#: Vocabulary that belongs to our systems, not to a customer's inbox. A message
#: saying "the payment failed with DO_NOT_HONOR (PAYMENT_FAILURE)" is accurate
#: and useless: it exposes internal taxonomy and reads as a leaked stack trace.
INTERNAL_JARGON = (
    r"\bDO_NOT_HONOR\b", r"\bCARD_EXPIRED\b", r"\bMANDATE_REVOKED\b",
    r"\bMANDATE_ABSENT\b", r"\bINSUFFICIENT_FUNDS\b", r"\bBANK_UNAVAILABLE\b",
    r"\bPROCESSOR_ERROR\b", r"\bFRAUD_SUSPECTED\b", r"\bCHECKOUT_DROPPED\b",
    r"\bINVOICE_OVERDUE\b", r"\bPAYMENT_METHOD_DECLINED_AT_CHECKOUT\b",
    r"\bPAYMENT_FAILURE\b", r"\bCHECKOUT_ABANDONMENT\b", r"\bOVERDUE_RECEIVABLE\b",
    r"\bGENERATE_PAYMENT_LINK\b", r"\bSCHEDULE_RETRY\b", r"\bRETRY_PAYMENT\b",
    r"\bSEND_PAYMENT_REMINDER\b", r"\bREQUEST_HUMAN_REVIEW\b", r"\bSTOP_RECOVERY\b",
    r"\bcustomer segment\b", r"\brisk[_ ]type\b", r"\bfailure[_ ]c(?:ategory|ode)\b",
    r"\b(?:RETAIL|SMB|ENTERPRISE)\b",
    r"\bprior[_ ]successful[_ ]payments\b", r"\blifetime[_ ]payments\b",
)

MAX_CHARS = 1200


@dataclass(frozen=True, slots=True)
class ContentDecision:
    allowed: bool
    rule: ContentRule
    reason: str
    evaluated: tuple[ContentRule, ...] = field(default_factory=tuple)


def _money_tokens(text: str) -> set[str]:
    """Amounts the message states, normalised to whole rupees."""
    found = set()
    for raw in re.findall(r"(?:₹|Rs\.?\s*)([\d,]+(?:\.\d{1,2})?)", text, flags=re.I):
        try:
            found.add(str(int(float(raw.replace(",", "")))))
        except ValueError:
            continue
    return found


def evaluate_message(
    text: str,
    *,
    amount_minor: int,
    invoice_id: str,
) -> ContentDecision:
    """Decide whether this message may be sent. Pure and total."""
    trace: list[ContentRule] = []
    lowered = text.lower()

    trace.append(ContentRule.OVERLONG)
    if len(text) > MAX_CHARS:
        return ContentDecision(
            False, ContentRule.OVERLONG,
            f"message is {len(text)} characters; the limit is {MAX_CHARS}",
            tuple(trace),
        )

    trace.append(ContentRule.UNAUTHORISED_COMMITMENT)
    for pattern in COMMITMENTS:
        if re.search(pattern, lowered):
            return ContentDecision(
                False, ContentRule.UNAUTHORISED_COMMITMENT,
                "the message commits to something the agent has no authority to "
                f"offer (matched: {pattern})",
                tuple(trace),
            )

    trace.append(ContentRule.PRESSURE_OR_THREAT)
    for pattern in PRESSURE:
        if re.search(pattern, lowered):
            return ContentDecision(
                False, ContentRule.PRESSURE_OR_THREAT,
                f"the message applies collections pressure (matched: {pattern})",
                tuple(trace),
            )

    trace.append(ContentRule.ASKS_FOR_CREDENTIALS)
    for pattern in CREDENTIALS:
        if re.search(pattern, lowered):
            return ContentDecision(
                False, ContentRule.ASKS_FOR_CREDENTIALS,
                "the message asks the customer to send payment credentials, which "
                "is indistinguishable from phishing",
                tuple(trace),
            )

    trace.append(ContentRule.LEAKS_INTERNALS)
    for pattern in INTERNAL_JARGON:
        m = re.search(pattern, text)
        if m:
            return ContentDecision(
                False, ContentRule.LEAKS_INTERNALS,
                f"the message exposes internal vocabulary to the customer: "
                f"{m.group(0)!r}",
                tuple(trace),
            )

    trace.append(ContentRule.FABRICATED_AMOUNT)
    stated = _money_tokens(text)
    owed = str(int(round(amount_minor / 100)))
    if stated - {owed}:
        return ContentDecision(
            False, ContentRule.FABRICATED_AMOUNT,
            f"the message states {sorted(stated - {owed})} but the amount owed is {owed}",
            tuple(trace),
        )

    trace.append(ContentRule.MISSING_REQUIRED_FACT)
    if invoice_id.lower() not in lowered:
        return ContentDecision(
            False, ContentRule.MISSING_REQUIRED_FACT,
            f"the message never identifies the invoice ({invoice_id})",
            tuple(trace),
        )
    if not stated:
        return ContentDecision(
            False, ContentRule.MISSING_REQUIRED_FACT,
            "the message never states the amount owed",
            tuple(trace),
        )

    trace.append(ContentRule.PERMITTED)
    return ContentDecision(True, ContentRule.PERMITTED, "message may be sent", tuple(trace))
