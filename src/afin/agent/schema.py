"""Structured proposal schema.

Validation here is about *shape*, not permission. In particular `action` is a
plain string: if the model names an operation outside the action space, that
string must survive into the policy engine so it can be denied as
UNSUPPORTED_ACTION and recorded. Rejecting it here would hide the proposal, and
"unsafe actions proposed" is a number this experiment exists to measure.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from afin.domain.enums import Channel
from afin.domain.models import ProposedAction

PROMPT_VERSION = "recovery-analyst-v1"


class AgentProposal(BaseModel):
    """What the model is asked to return."""

    diagnosis: str = Field(
        description="One sentence on the most likely cause of the failure."
    )
    action: str = Field(
        description="The single recovery action to take next."
    )
    reasoning_summary: str = Field(
        description=(
            "Two sentences at most, justifying the action from the observable "
            "facts. A conclusion for the audit record, not a train of thought."
        )
    )
    confidence: float = Field(ge=0.0, le=1.0)
    scheduled_delay_hours: int | None = Field(
        default=None, description="Hours to wait. Required for SCHEDULE_RETRY."
    )
    channel: str | None = Field(default=None, description="EMAIL or SMS, if contacting.")

    def to_domain(self, payment_id: str) -> ProposedAction:
        try:
            channel = Channel(self.channel) if self.channel else None
        except ValueError:
            channel = None
        return ProposedAction(
            action=self.action.strip(),
            payment_id=payment_id,
            diagnosis=self.diagnosis.strip(),
            reasoning_summary=self.reasoning_summary.strip(),
            confidence=float(self.confidence),
            scheduled_delay_hours=self.scheduled_delay_hours,
            channel=channel,
            raw_json=self.model_dump_json(),
        )


class InvalidProposal(Exception):
    """The model returned something that is not a well-formed proposal at all."""

    def __init__(self, detail: str, raw: str = ""):
        super().__init__(detail)
        self.detail = detail
        self.raw = raw[:2000]


def parse(raw: str, payment_id: str) -> ProposedAction:
    """Parse model output into a domain proposal, or raise InvalidProposal."""
    try:
        return AgentProposal.model_validate_json(raw).to_domain(payment_id)
    except ValidationError as exc:
        raise InvalidProposal(f"schema validation failed: {exc.error_count()} error(s)", raw)
    except ValueError as exc:
        raise InvalidProposal(f"unparseable model output: {exc}", raw)
