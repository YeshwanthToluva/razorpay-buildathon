"""Divergence classification and evidence completeness (experiment 002e)."""

from __future__ import annotations

import json
import pathlib

import pytest

from analysis.divergence import (
    OPTIMAL,
    classify,
    context_fidelity,
    load_ledger,
    per_payment,
)

LEDGER = pathlib.Path("data/ledger")
CONTROL = LEDGER / "agent-gpt-control-20260904T104215-72b641.json"
TREATMENT = LEDGER / "agent-gpt-treatment-20260904T104643-382f4d.json"
BASELINE = LEDGER / "baseline-20260904T074124-6b9ad6.json"


def payment(rev=0, acts=(), category="BANK_UNAVAILABLE", denied=(), invalid=0):
    return {
        "acts": list(acts), "denied": list(denied), "rev": rev, "final": None,
        "invalid": invalid, "scenario": "s", "category": category, "amount": 1000,
    }


# --- classification -------------------------------------------------------

def test_matching_recovery_is_correct_action():
    b = payment(rev=5000, acts=["RETRY_PAYMENT"])
    a = payment(rev=5000, acts=["RETRY_PAYMENT"])
    assert classify("p", b, a) == "correct_diagnosis_correct_action"


def test_agent_losing_money_by_escalating_is_excessive_escalation():
    b = payment(rev=5000, acts=["GENERATE_PAYMENT_LINK"], category="CARD_EXPIRED")
    a = payment(rev=0, acts=["REQUEST_HUMAN_REVIEW"], category="CARD_EXPIRED")
    assert classify("p", b, a) == "excessive_escalation"


def test_agent_losing_money_by_stopping_is_premature_stopping():
    b = payment(rev=5000, acts=["SCHEDULE_RETRY"])
    a = payment(rev=0, acts=["RETRY_PAYMENT", "STOP_RECOVERY"])
    assert classify("p", b, a) == "premature_stopping"


def test_agent_recovering_more_is_agent_better():
    b = payment(rev=0, acts=["STOP_RECOVERY"])
    a = payment(rev=4833, acts=["SCHEDULE_RETRY"])
    assert classify("p", b, a) == "agent_better"


def test_an_invalid_proposal_is_an_execution_failure_not_a_judgement():
    """A provider blip must never be scored as the agent choosing to give up."""
    b = payment(rev=5000, acts=["RETRY_PAYMENT"])
    a = payment(rev=0, acts=[], invalid=1)
    assert classify("p", b, a) == "execution_failure"


def test_both_recovering_nothing_under_policy_is_not_counted_against_the_agent():
    b = payment(rev=0, acts=[], denied=[("RETRY_PAYMENT", "DENY", "DISPUTE_BLOCK")])
    a = payment(rev=0, acts=[], denied=[("RETRY_PAYMENT", "DENY", "DISPUTE_BLOCK")])
    assert classify("p", b, a) == "policy_blocked_correctly"


def test_optimal_action_table_covers_every_failure_category():
    from afin.domain.enums import FailureCategory

    assert set(OPTIMAL) == {c.value for c in FailureCategory}


# --- evidence completeness ------------------------------------------------

@pytest.mark.parametrize("path", [CONTROL, TREATMENT, BASELINE])
def test_exported_ledger_exists_and_covers_every_payment(path):
    """Evidence-incomplete runs must never be silently analysed."""
    if not path.exists():
        pytest.skip(f"{path.name} not exported")
    pp = per_payment(load_ledger(str(path)))
    assert len(pp) == 50, f"{path.name} holds {len(pp)} payments, expected 50"
    assert all(v["scenario"] for v in pp.values()), "a case is missing CASE_OPENED state"


@pytest.mark.parametrize("path", [CONTROL, TREATMENT])
def test_exported_ledger_reproduces_the_reported_recovery(path):
    """The export must regenerate the headline metric, not just resemble it."""
    if not path.exists():
        pytest.skip(f"{path.name} not exported")
    run_id = path.stem
    metrics = json.loads((pathlib.Path("data/runs") / f"{run_id}.json").read_text())
    ledger_total = sum(v["rev"] for v in per_payment(load_ledger(str(path))).values())
    assert ledger_total == metrics["revenue_recovered_minor"]


def test_context_fidelity_is_measurable_in_both_arms():
    for path in (CONTROL, TREATMENT):
        if not path.exists():
            pytest.skip("ledgers not exported")
        c = context_fidelity(str(path))
        assert c["total"] > 0
        assert c["supported"] + c["contradicted"] + c["missing"] == c["total"]
        assert 0.0 <= c["fidelity_rate"] <= 1.0
