"""Append-only audit ledger.

Audit events are first-class domain data, not logs. Everything the metrics
layer reports is derived from this table, so anything absent here is
unmeasurable by construction -- which is the intended pressure.

What is deliberately NOT stored: chain-of-thought. `reasoning_summary` is a
short structured justification of the decision, produced for audit. It explains
the decision without attempting to reproduce private internal reasoning.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import Engine, select

from afin.domain.models import CustomerSnapshot, PaymentSnapshot
from afin.db.schema import audit_events, payment_attempts, runs


class EventType(StrEnum):
    CASE_OPENED = "CASE_OPENED"
    PROPOSAL_MADE = "PROPOSAL_MADE"
    PROPOSAL_INVALID = "PROPOSAL_INVALID"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    STATE_TRANSITIONED = "STATE_TRANSITIONED"
    CASE_CLOSED = "CASE_CLOSED"
    AGENT_ERROR = "AGENT_ERROR"


def observed_state(payment: PaymentSnapshot, customer: CustomerSnapshot) -> str:
    """Exactly what the agent was shown, frozen into the ledger.

    Without this, "what did the agent observe?" is unanswerable after the fact
    and no decision can be honestly reviewed.
    """
    return json.dumps(
        {"payment": asdict(payment), "customer": asdict(customer)},
        sort_keys=True,
        default=str,
    )


@dataclass
class AuditLedger:
    engine: Engine
    run_id: str

    #: A pathological model can emit an unbounded "action". It must be recorded,
    #: but it must not be able to bloat the ledger either.
    MAX_ACTION_CHARS = 512

    def record(self, **fields) -> None:
        proposed = fields.get("proposed_action")
        if isinstance(proposed, str) and len(proposed) > self.MAX_ACTION_CHARS:
            fields["proposed_action"] = proposed[: self.MAX_ACTION_CHARS] + "...[truncated]"
        payload = {
            "run_id": self.run_id,
            "timestamp": fields.pop("timestamp", datetime.now(timezone.utc)),
            "revenue_recovered_minor": 0,
            **fields,
        }
        payload["event_type"] = str(payload["event_type"])
        with self.engine.begin() as conn:
            conn.execute(audit_events.insert().values(**payload))

    def record_attempt(self, **fields) -> None:
        with self.engine.begin() as conn:
            conn.execute(payment_attempts.insert().values(run_id=self.run_id, **fields))

    def open_run(self, **fields) -> None:
        with self.engine.begin() as conn:
            conn.execute(runs.insert().values(run_id=self.run_id, **fields))

    def close_run(self) -> None:
        from sqlalchemy import update

        with self.engine.begin() as conn:
            conn.execute(
                update(runs)
                .where(runs.c.run_id == self.run_id)
                .values(finished_at=datetime.now(timezone.utc))
            )

    def events(self) -> list[dict]:
        stmt = (
            select(audit_events)
            .where(audit_events.c.run_id == self.run_id)
            .order_by(audit_events.c.seq)
        )
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(stmt).mappings().all()]
