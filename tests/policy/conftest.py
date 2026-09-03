from __future__ import annotations

from afin.domain.enums import ActionType
from afin.domain.models import ProposedAction
from afin.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from afin.policy.engine import PolicyRequest
from tests.conftest import NOW, make_customer, make_payment


def propose(action=ActionType.RETRY_PAYMENT, payment_id="pay_0001", **kw) -> ProposedAction:
    base = dict(
        action=action,
        payment_id=payment_id,
        diagnosis="transient gateway failure",
        reasoning_summary="prior successful payments suggest the instrument is good",
        confidence=0.8,
    )
    return ProposedAction(**{**base, **kw})


def request(
    proposal=None,
    payment=None,
    customer=None,
    now=NOW,
    config: PolicyConfig = DEFAULT_POLICY_CONFIG,
    **payment_kw,
) -> PolicyRequest:
    return PolicyRequest(
        proposal=proposal if proposal is not None else propose(),
        payment=payment if payment is not None else make_payment(**payment_kw),
        customer=customer if customer is not None else make_customer(),
        now=now,
        config=config,
    )
