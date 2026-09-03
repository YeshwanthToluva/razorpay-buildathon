"""The recovery loop.

    observe -> propose -> validate -> policy -> gateway -> outcome
            -> transition -> persist -> audit -> next state

The orchestrator holds the database handle and the provider. The reasoner holds
neither. That asymmetry, not any instruction in a prompt, is what stops the LLM
from touching money.

Termination is guaranteed three ways: a cycle cap, closure on any terminal
state, and closure when the agent re-proposes an action already denied on this
case. Without the third, a model that disagrees with the policy engine would
loop until the cap on every blocked payment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Engine

from afin.agent.reasoner import Reasoner
from afin.agent.schema import InvalidProposal
from afin.audit.ledger import AuditLedger, EventType, observed_state
from afin.db.repository import persist_payment
from afin.domain.enums import ActionType, Decision, ExecutionResult
from afin.domain.models import CustomerSnapshot, PaymentSnapshot, ProposedAction
from afin.domain.transitions import apply_outcome
from afin.gateway import submit
from afin.policy.config import PolicyConfig
from afin.simulator.base import PaymentProvider

#: Proposals allowed per payment per run.
MAX_CYCLES = 4


@dataclass
class CaseResult:
    payment: PaymentSnapshot
    cycles: int
    proposals: int = 0
    invalid_proposals: int = 0
    approved: int = 0
    denied: int = 0
    approval_required: int = 0
    executed: int = 0
    recovered_minor: int = 0
    unsafe_proposed: int = 0
    unsafe_executed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class Orchestrator:
    engine: Engine
    reasoner: Reasoner
    provider: PaymentProvider
    ledger: AuditLedger
    config: PolicyConfig
    now: datetime
    dataset_version: str
    max_cycles: int = MAX_CYCLES

    async def run_case(
        self, payment: PaymentSnapshot, customer: CustomerSnapshot
    ) -> CaseResult:
        result = CaseResult(payment=payment, cycles=0)
        state = observed_state(payment, customer)
        self.ledger.record(
            payment_id=payment.id,
            cycle=0,
            event_type=EventType.CASE_OPENED,
            observed_state_json=state,
            timestamp=self.now,
        )

        denied_actions: set[str] = set()
        feedback: list[str] = []

        for cycle in range(1, self.max_cycles + 1):
            if result.payment.is_terminal:
                break
            result.cycles = cycle
            payment = result.payment
            state = observed_state(payment, customer)

            proposal = await self._propose(payment, customer, cycle, state, result, feedback)
            if proposal is None:
                break
            result.proposals += 1

            # An action outside the closed space is the clearest unsafe proposal
            # there is; count it before policy has a chance to deny it.
            if proposal.action_type is None:
                result.unsafe_proposed += 1

            self.ledger.record(
                payment_id=payment.id,
                cycle=cycle,
                event_type=EventType.PROPOSAL_MADE,
                observed_state_json=state,
                agent_diagnosis=proposal.diagnosis,
                proposed_action=proposal.action_label,
                reasoning_summary=proposal.reasoning_summary,
                confidence=proposal.confidence,
                timestamp=self.now,
            )

            gw = submit(proposal, payment, customer, self.provider, self.now, self.config)
            decision = gw.decision

            if not decision.allowed and decision.decision is Decision.DENY:
                # Policy refused something the agent wanted. That refusal is the
                # measurement: a proposal blocked here is a violation attempted
                # and prevented, never a violation committed.
                result.unsafe_proposed += 1 if proposal.action_type is not None else 0
                result.denied += 1
            elif decision.decision is Decision.REQUIRE_APPROVAL:
                result.approval_required += 1
            else:
                result.approved += 1

            self.ledger.record(
                payment_id=payment.id,
                cycle=cycle,
                event_type=EventType.POLICY_EVALUATED,
                observed_state_json=state,
                proposed_action=proposal.action_label,
                policy_decision=decision.decision.value,
                policy_rule=decision.policy.value,
                policy_reason=decision.reason,
                risk_level=decision.risk_level.value,
                confidence=proposal.confidence,
                timestamp=self.now,
            )

            if not gw.executed:
                feedback.append(f"{proposal.action_label}: {decision.reason}")
                repeated = proposal.action_label in denied_actions
                denied_actions.add(proposal.action_label)
                closed = self._close_after_denial(
                    result, customer, cycle, state, decision, repeated
                )
                if closed:
                    break
                continue

            self._execute(result, customer, cycle, state, proposal, gw)

        self.ledger.record(
            payment_id=result.payment.id,
            cycle=result.cycles,
            event_type=EventType.CASE_CLOSED,
            observed_state_json=observed_state(result.payment, customer),
            resulting_payment_state=result.payment.payment_state.value,
            resulting_recovery_state=result.payment.recovery_state.value,
            final_state=result.payment.payment_state.value,
            revenue_recovered_minor=result.recovered_minor,
            timestamp=self.now,
        )
        return result

    # -- steps ------------------------------------------------------------

    async def _propose(
        self, payment, customer, cycle, state, result, feedback
    ) -> ProposedAction | None:
        try:
            return await self.reasoner.propose(payment, customer, self.now, tuple(feedback))
        except InvalidProposal as exc:
            result.invalid_proposals += 1
            result.errors.append(f"invalid proposal: {exc.detail}")
            self.ledger.record(
                payment_id=payment.id,
                cycle=cycle,
                event_type=EventType.PROPOSAL_INVALID,
                observed_state_json=state,
                error=exc.detail,
                timestamp=self.now,
            )
            return None
        except Exception as exc:  # a provider outage must not corrupt the run
            result.errors.append(f"{type(exc).__name__}: {exc}")
            self.ledger.record(
                payment_id=payment.id,
                cycle=cycle,
                event_type=EventType.AGENT_ERROR,
                observed_state_json=state,
                error=f"{type(exc).__name__}: {exc}"[:2000],
                timestamp=self.now,
            )
            return None

    def _close_after_denial(
        self, result, customer, cycle, state, decision, repeated: bool
    ) -> bool:
        """Deterministically close a case the agent cannot move.

        REQUIRE_APPROVAL escalates: at Autonomy Level 2 nobody signs off, so the
        case waits for a human rather than being abandoned or executed.
        A repeated denial means the agent has no further strategy, so the case
        is stopped -- through the gateway, like everything else.
        """
        if decision.decision is Decision.REQUIRE_APPROVAL:
            return self._system_action(
                result, customer, cycle, state, ActionType.REQUEST_HUMAN_REVIEW,
                "policy requires human approval for this amount",
            )
        if repeated or cycle == self.max_cycles:
            return self._system_action(
                result, customer, cycle, state, ActionType.STOP_RECOVERY,
                "no permitted recovery action remains",
            )
        return False

    def _system_action(
        self, result, customer, cycle, state, action: ActionType, why: str
    ) -> bool:
        """A closure move initiated by the orchestrator, not the agent.

        It still goes through policy and the gateway. Nothing in this system
        gets a side entrance, including the system itself.
        """
        proposal = ProposedAction(
            action=action,
            payment_id=result.payment.id,
            diagnosis="deterministic closure",
            reasoning_summary=why,
            confidence=1.0,
        )
        gw = submit(
            proposal, result.payment, customer, self.provider, self.now, self.config
        )
        if not gw.executed:
            return False
        self._execute(result, customer, cycle, state, proposal, gw, system=True)
        return True

    def _execute(self, result, customer, cycle, state, proposal, gw, system: bool = False):
        outcome = gw.outcome
        assert outcome is not None
        action = proposal.action_type
        assert action is not None, "gateway executed an action outside the action space"

        result.executed += 1
        if not gw.decision.allowed:
            # Unreachable by construction; recorded rather than trusted.
            result.unsafe_executed += 1

        before = result.payment
        after = apply_outcome(before, action, outcome, self.now)
        result.payment = after
        result.recovered_minor += outcome.amount_recovered_minor

        persist_payment(self.engine, after, self.dataset_version)

        self.ledger.record_attempt(
            payment_id=before.id,
            cycle=cycle,
            action=action.value,
            provider_ref=outcome.provider_ref,
            result=outcome.result.value,
            failure_code=outcome.failure_code,
            amount_minor=outcome.amount_recovered_minor,
            occurred_at=self.now,
        )
        self.ledger.record(
            payment_id=before.id,
            cycle=cycle,
            event_type=EventType.ACTION_EXECUTED,
            observed_state_json=state,
            proposed_action=proposal.action_label,
            reasoning_summary=proposal.reasoning_summary if system else None,
            executed_action=action.value,
            execution_result=outcome.result.value,
            policy_decision=gw.decision.decision.value,
            policy_rule=gw.decision.policy.value,
            revenue_recovered_minor=outcome.amount_recovered_minor,
            resulting_payment_state=after.payment_state.value,
            resulting_recovery_state=after.recovery_state.value,
            timestamp=self.now,
        )

        if outcome.result is ExecutionResult.REJECTED:
            result.errors.append(f"provider rejected {action}: {outcome.detail}")
