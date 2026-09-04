"""Failure -> next-decision transition analysis (experiment 002g)."""

from __future__ import annotations

import pathlib

import pytest

from analysis.feedback import timeline, transitions

LEDGER = pathlib.Path("data/ledger")
CONTROL = LEDGER / "agent-gpt-treatment-nofb-20260904T105956-054a8f.json"
FEEDBACK = LEDGER / "agent-gpt-treatment-fb-20260904T110121-51e5ce.json"


def ledger(events):
    return {"audit_events": events}


def proposal(pid, cycle, action, claimed=None):
    return {
        "payment_id": pid, "cycle": cycle, "event_type": "PROPOSAL_MADE",
        "proposed_action": action, "executed_action": None, "execution_result": None,
        "revenue_recovered_minor": 0, "claimed_last_attempt_outcome": claimed,
    }


def executed(pid, cycle, action, result="FAILURE", recovered=0):
    return {
        "payment_id": pid, "cycle": cycle, "event_type": "ACTION_EXECUTED",
        "proposed_action": action, "executed_action": action, "execution_result": result,
        "revenue_recovered_minor": recovered, "claimed_last_attempt_outcome": None,
    }


def test_a_retry_after_a_failed_retry_is_counted():
    t = transitions(ledger([
        proposal("p", 1, "RETRY_PAYMENT"),
        executed("p", 1, "RETRY_PAYMENT"),
        proposal("p", 2, "RETRY_PAYMENT"),
    ]))
    assert t["failures_observed"] == 1
    assert t["retry_after_failed_retry"] == 1
    assert t["repeated_same_action_after_failure"] == 1


def test_switching_strategy_after_a_failure_is_not_a_repeat():
    t = transitions(ledger([
        proposal("p", 1, "RETRY_PAYMENT"),
        executed("p", 1, "RETRY_PAYMENT"),
        proposal("p", 2, "GENERATE_PAYMENT_LINK"),
    ]))
    assert t["repeated_same_action_after_failure"] == 0
    assert t["retry_after_failed_retry"] == 0
    assert t["next_action"] == {"GENERATE_PAYMENT_LINK": 1}


def test_a_successful_action_is_not_a_failure_transition():
    t = transitions(ledger([
        proposal("p", 1, "RETRY_PAYMENT"),
        executed("p", 1, "RETRY_PAYMENT", result="SUCCESS", recovered=5000),
        proposal("p", 2, "RETRY_PAYMENT"),
    ]))
    assert t["failures_observed"] == 0


def test_recovery_after_a_failure_is_attributed():
    t = transitions(ledger([
        proposal("p", 1, "RETRY_PAYMENT"),
        executed("p", 1, "RETRY_PAYMENT"),
        proposal("p", 2, "SCHEDULE_RETRY"),
        executed("p", 2, "SCHEDULE_RETRY", result="SUCCESS", recovered=7000),
    ]))
    assert t["recovery_after_failure"] == 1
    assert t["recovered_revenue_after_failure"] == 7000


def test_absorption_distinguishes_knowing_from_guessing():
    """The measure that separates 'did not know' from 'knew and retried anyway'."""
    t = transitions(ledger([
        proposal("p", 1, "RETRY_PAYMENT"),
        executed("p", 1, "RETRY_PAYMENT"),
        proposal("p", 2, "RETRY_PAYMENT", claimed="FAILED"),
        executed("p", 2, "RETRY_PAYMENT"),
        proposal("p", 3, "RETRY_PAYMENT", claimed="SUCCEEDED"),
        executed("p", 3, "RETRY_PAYMENT"),
        proposal("p", 4, "RETRY_PAYMENT", claimed=None),
    ]))
    assert t["absorbed_outcome"] == {"SUPPORTED": 1, "CONTRADICTED": 1, "MISSING": 1}


def test_transitions_are_scoped_per_payment():
    """A failure on one payment must not be paired with another's next decision."""
    t = transitions(ledger([
        proposal("a", 1, "RETRY_PAYMENT"),
        executed("a", 1, "RETRY_PAYMENT"),
        proposal("b", 1, "GENERATE_PAYMENT_LINK"),
    ]))
    assert t["next_action"] == {}


def test_timeline_preserves_order():
    tl = timeline(ledger([
        proposal("p", 1, "RETRY_PAYMENT"),
        executed("p", 1, "RETRY_PAYMENT"),
        proposal("p", 2, "SCHEDULE_RETRY"),
    ]))
    assert [e["type"] for e in tl["p"]] == [
        "PROPOSAL_MADE", "ACTION_EXECUTED", "PROPOSAL_MADE"
    ]


@pytest.mark.parametrize("path", [CONTROL, FEEDBACK])
def test_real_002g_ledgers_yield_transitions(path):
    import json

    if not path.exists():
        pytest.skip(f"{path.name} not exported")
    t = transitions(json.loads(path.read_text()))
    assert t["failures_observed"] > 0
    assert sum(t["absorbed_outcome"].values()) == t["cycles_burned_after_failed_action"]
