"""The reasoning port.

A Reasoner receives frozen snapshots and returns a proposal. It has no database
handle, no provider, and no ledger, so no implementation of this interface --
LLM-backed or otherwise -- can execute anything or mutate anything. Swapping
implementations is how autonomy levels are compared while everything downstream
is held constant.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from afin.domain.models import CustomerSnapshot, PaymentSnapshot, ProposedAction


class Reasoner(Protocol):
    name: str
    #: Recorded on the run row; None for non-model reasoners.
    model: str | None

    async def propose(
        self,
        payment: PaymentSnapshot,
        customer: CustomerSnapshot,
        now: datetime,
        feedback: Sequence[str] = (),
        cycle: int = 1,
        max_cycles: int = 4,
    ) -> ProposedAction:
        """Propose the next action.

        `feedback` carries policy denials already issued on this case in this
        run. It exists so an agent can change strategy after being blocked --
        one of the behaviours this laboratory is meant to study -- rather than
        proposing the same denied action until the cycle cap stops it.
        """
        ...
