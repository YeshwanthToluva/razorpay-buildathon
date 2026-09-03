"""Transient provider faults must not be reported as agent behaviour."""

from __future__ import annotations

import pytest

from afin.agent.llm import LLMReasoner
from afin.metrics.recovery import RunMetrics, run_is_valid


class FakeStatusError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"Error code: {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 502, 503, 504])
def test_transient_statuses_are_recognised_as_retryable(status):
    assert LLMReasoner._status_of(FakeStatusError(status)) == status
    assert status in LLMReasoner.RETRYABLE_STATUS


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_statuses_are_not_retried(status):
    """Retrying a bad key or an unknown model just wastes the run."""
    assert status not in LLMReasoner.RETRYABLE_STATUS


def test_status_is_recovered_from_a_wrapped_exception():
    """Agent Framework wraps provider errors, so the code hides in __cause__."""
    wrapped = RuntimeError("service failed to complete the prompt")
    wrapped.__cause__ = FakeStatusError(429)
    assert LLMReasoner._status_of(wrapped) == 429


def test_status_is_recovered_from_the_message_when_no_attribute_exists():
    err = RuntimeError("... {'status': 429, 'title': 'Too Many Requests'}")
    assert LLMReasoner._status_of(err) == 429


def test_an_unrelated_error_has_no_status_and_is_not_retried():
    assert LLMReasoner._status_of(ValueError("bad json")) is None


def test_a_run_where_the_provider_was_mostly_unreachable_is_not_valid():
    """The failure this guard exists for: 40/50 errors still yields a rate."""
    valid, why = run_is_valid(
        RunMetrics(run_id="r", payments_processed=50, agent_errors=40)
    )
    assert valid is False
    assert "40 of 50" in why


def test_a_run_with_a_few_transient_failures_is_still_valid():
    valid, _ = run_is_valid(RunMetrics(run_id="r", payments_processed=50, agent_errors=3))
    assert valid is True


def test_an_empty_run_is_not_valid():
    valid, why = run_is_valid(RunMetrics(run_id="r"))
    assert valid is False
    assert "no payments" in why


def test_the_validity_boundary_is_the_stated_tolerance():
    assert run_is_valid(RunMetrics(run_id="r", payments_processed=50, agent_errors=5))[0]
    assert not run_is_valid(RunMetrics(run_id="r", payments_processed=50, agent_errors=6))[0]
