"""Payment links a real person can click.

A link is a promise that a specific amount is owed on a specific payment in a
specific run. Settling one is a *provider outcome* like any other -- it goes
through the same reducer and the same ledger as a simulated collection, so a
recovery driven by a human clicking Pay is recorded exactly like one driven by
the simulator. Nothing about the audit trail is special-cased for the demo.

Links are stored on disk rather than in memory so restarting the API does not
strand an email that is already in someone's inbox.
"""

from __future__ import annotations

import json
import pathlib
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

STORE = pathlib.Path("data/links")


@dataclass
class PayLink:
    token: str
    run_id: str
    payment_id: str
    dataset_version: str
    amount_minor: int
    currency: str
    invoice_id: str
    recipient: str
    created_at: str
    settled_at: str | None = None
    #: Present when the customer's own address was replaced by the demo
    #: recipient, so the record never implies we mailed the customer.
    redirected_from: str | None = None

    @property
    def settled(self) -> bool:
        return self.settled_at is not None


def _path(token: str) -> pathlib.Path:
    return STORE / f"{token}.json"


def create(
    *,
    run_id: str,
    payment_id: str,
    dataset_version: str,
    amount_minor: int,
    currency: str,
    invoice_id: str,
    recipient: str,
    redirected_from: str | None = None,
) -> PayLink:
    STORE.mkdir(parents=True, exist_ok=True)
    link = PayLink(
        token=secrets.token_urlsafe(9),
        run_id=run_id,
        payment_id=payment_id,
        dataset_version=dataset_version,
        amount_minor=amount_minor,
        currency=currency,
        invoice_id=invoice_id,
        recipient=recipient,
        created_at=datetime.now(timezone.utc).isoformat(),
        redirected_from=redirected_from,
    )
    _path(link.token).write_text(json.dumps(asdict(link), indent=2))
    return link


def load(token: str) -> PayLink | None:
    p = _path(token)
    if not p.exists():
        return None
    return PayLink(**json.loads(p.read_text()))


def mark_settled(token: str) -> PayLink | None:
    """Settle once. A second click reports the first settlement, never a second.

    Without this a refresh would collect the same money twice, which would show
    up in the ledger as revenue that was never actually paid.
    """
    link = load(token)
    if link is None or link.settled:
        return link
    link.settled_at = datetime.now(timezone.utc).isoformat()
    _path(token).write_text(json.dumps(asdict(link), indent=2))
    return link
