"""Context-fidelity classification (experiment 002e).

Deterministic comparison of the model's restated facts against the state it was
actually shown. No prose parsing, no inferred intent.
"""

from __future__ import annotations

import pytest

from afin.metrics.recovery import (
    CHECKED_CLAIMS,
    ClaimVerdict,
    RunMetrics,
    classify_claim,
    run_is_valid,
)


@pytest.mark.parametrize("claimed,actual", [(True, True), (False, False), (0, 0), (17, 17)])
def test_a_matching_claim_is_supported(claimed, actual):
    assert classify_claim(claimed, actual) is ClaimVerdict.SUPPORTED


@pytest.mark.parametrize(
    "claimed,actual",
    [
        (True, False),   # claimed an opt-out that was not there
        (False, True),   # missed an opt-out that was
        (0, 17),         # "no prior successful payments" when there were 17
        (0, 2),          # the brief's example
        (5, 20),
    ],
)
def test_a_mismatching_claim_is_contradicted(claimed, actual):
    assert classify_claim(claimed, actual) is ClaimVerdict.CONTRADICTED


def test_the_brief_example_is_a_contradiction():
    """'no prior successful payments' when there were 2 must count as one."""
    assert classify_claim(0, 2) is ClaimVerdict.CONTRADICTED


@pytest.mark.parametrize("actual", [True, False, 0, 17])
def test_an_absent_claim_is_missing_not_contradicted(actual):
    """Silence is not a false statement; conflating them inflates contradictions."""
    assert classify_claim(None, actual) is ClaimVerdict.MISSING


def test_false_is_not_treated_as_absent():
    """A claim of False is a real claim -- Python falsiness must not swallow it."""
    assert classify_claim(False, False) is ClaimVerdict.SUPPORTED
    assert classify_claim(False, True) is ClaimVerdict.CONTRADICTED


def test_zero_is_not_treated_as_absent():
    assert classify_claim(0, 0) is ClaimVerdict.SUPPORTED
    assert classify_claim(0, 3) is ClaimVerdict.CONTRADICTED


def test_both_required_fields_are_checked():
    assert set(CHECKED_CLAIMS) == {
        "claimed_opted_out",
        "claimed_prior_successful_payments",
    }
    assert CHECKED_CLAIMS["claimed_opted_out"] == ("customer", "opted_out")
    assert CHECKED_CLAIMS["claimed_prior_successful_payments"] == (
        "customer",
        "prior_successful_payments",
    )


# --- run validity must account for invalid proposals ----------------------

def test_a_run_of_mostly_invalid_proposals_is_not_valid():
    """Previously only provider errors gated validity, so this run passed."""
    valid, why = run_is_valid(
        RunMetrics(run_id="r", payments_processed=50, invalid_proposals=40)
    )
    assert valid is False
    assert "invalid" in why


def test_provider_errors_and_invalid_proposals_are_counted_together():
    """Neither alone breaches 10% of 50; together they do."""
    assert run_is_valid(
        RunMetrics(run_id="r", payments_processed=50, agent_errors=2, invalid_proposals=2)
    )[0] is True
    assert run_is_valid(
        RunMetrics(run_id="r", payments_processed=50, agent_errors=3, invalid_proposals=3)
    )[0] is False
