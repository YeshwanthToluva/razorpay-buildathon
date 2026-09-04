"""Metrics are derived from the ledger, so these drive real events through it."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from afin.audit.ledger import AuditLedger, EventType
from afin.db.engine import create_schema, get_engine
from afin.db.schema import audit_events
from afin.metrics.exporter import render
from afin.metrics.recovery import RunMetrics, UNSAFE_RULES, compute, run_is_valid
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine():
    try:
        eng = get_engine()
        create_schema(eng)
        with eng.connect() as conn:
            conn.execute(select(audit_events).limit(1))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"postgres unavailable: {exc}")
    return eng


@pytest.fixture
def ledger(engine):
    return AuditLedger(engine=engine, run_id=f"metrics-{uuid.uuid4().hex[:8]}")


NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


def test_a_denied_unsafe_proposal_counts_as_attempted_and_prevented(engine, ledger):
    ledger.record(
        payment_id="pay_0001", cycle=1, event_type=EventType.PROPOSAL_MADE,
        observed_state_json="{}", proposed_action="RETRY_PAYMENT", confidence=0.9,
        timestamp=NOW,
    )
    ledger.record(
        payment_id="pay_0001", cycle=1, event_type=EventType.POLICY_EVALUATED,
        observed_state_json="{}", proposed_action="RETRY_PAYMENT",
        policy_decision="DENY", policy_rule="DISPUTE_BLOCK", timestamp=NOW,
    )
    m = compute(engine, ledger.run_id, "does-not-exist")

    assert m.actions_proposed == 1
    assert m.actions_denied == 1
    assert m.policy_violations_attempted == 1
    assert m.policy_violations_prevented == 1
    assert m.unsafe_actions_executed == 0


def test_pacing_denials_are_not_counted_as_safety_violations(engine, ledger):
    """A cooldown is throttling, not an attempt to do something dangerous."""
    for rule in ("RETRY_COOLDOWN", "CONTACT_BUDGET"):
        ledger.record(
            payment_id="pay_0002", cycle=1, event_type=EventType.POLICY_EVALUATED,
            observed_state_json="{}", policy_decision="DENY", policy_rule=rule,
            timestamp=NOW,
        )
    m = compute(engine, ledger.run_id, "does-not-exist")

    assert m.actions_denied == 2
    assert m.policy_violations_attempted == 0
    assert "RETRY_COOLDOWN" not in UNSAFE_RULES


def test_an_execution_without_an_allow_is_reported_as_unsafe(engine, ledger):
    """The alarm itself must work, so it is proven on a hand-written event."""
    ledger.record(
        payment_id="pay_0003", cycle=1, event_type=EventType.ACTION_EXECUTED,
        observed_state_json="{}", executed_action="RETRY_PAYMENT",
        execution_result="SUCCESS", policy_decision="DENY", timestamp=NOW,
    )
    m = compute(engine, ledger.run_id, "does-not-exist")

    assert m.unsafe_actions_executed == 1, "the invariant alarm does not fire"


def test_approval_required_is_counted_separately_from_approved(engine, ledger):
    ledger.record(
        payment_id="pay_0004", cycle=1, event_type=EventType.POLICY_EVALUATED,
        observed_state_json="{}", policy_decision="REQUIRE_APPROVAL",
        policy_rule="HIGH_VALUE_APPROVAL", timestamp=NOW,
    )
    m = compute(engine, ledger.run_id, "does-not-exist")

    assert m.approvals_required == 1
    assert m.actions_approved == 0
    assert m.actions_denied == 0


def test_confidence_calibration_buckets_proposals_against_outcomes(engine, ledger):
    for i, (conf, recovered) in enumerate([(0.95, 1000), (0.95, 0), (0.15, 0)]):
        ledger.record(
            payment_id=f"pay_{i}", cycle=1, event_type=EventType.PROPOSAL_MADE,
            observed_state_json="{}", confidence=conf, proposed_action="RETRY_PAYMENT",
            timestamp=NOW,
        )
        ledger.record(
            payment_id=f"pay_{i}", cycle=1, event_type=EventType.ACTION_EXECUTED,
            observed_state_json="{}", executed_action="RETRY_PAYMENT",
            execution_result="SUCCESS", policy_decision="ALLOW",
            revenue_recovered_minor=recovered, timestamp=NOW,
        )
    m = compute(engine, ledger.run_id, "does-not-exist")

    assert m.confidence_calibration["0.9-1.0"] == {"n": 2, "recovered": 1, "hit_rate": 0.5}
    assert m.confidence_calibration["0.1-0.2"]["hit_rate"] == 0.0


def test_metrics_of_an_empty_run_are_zero_not_an_error(engine, ledger):
    m = compute(engine, ledger.run_id, "does-not-exist")

    assert m.payments_processed == 0
    assert m.recovery_rate == 0.0
    assert m.unsafe_actions_executed == 0


def test_prometheus_export_always_publishes_the_invariant(engine, ledger):
    text = render(compute(engine, ledger.run_id, "does-not-exist"))

    assert "afin_unsafe_actions_executed" in text
    assert "# TYPE afin_recovery_rate gauge" in text
    assert f'run_id="{ledger.run_id}"' in text


def test_a_run_whose_payment_metrics_came_from_another_run_is_invalid():
    """The contamination that per-run dataset isolation now prevents.

    Reproduces a real defect: an early arm reported 27 successful interventions
    of its own alongside 23 recovered payments read from a shared table, and its
    revenue figure was quoted as a result before the mismatch was noticed.
    """
    m = RunMetrics(
        run_id="r",
        payments_processed=50,
        successful_interventions=27,
        payments_recovered=23,
    )
    valid, why = run_is_valid(m)

    assert valid is False
    assert "27 successful interventions vs 23" in why


def test_a_self_consistent_run_passes_the_cross_check():
    m = RunMetrics(
        run_id="r",
        payments_processed=50,
        successful_interventions=27,
        payments_recovered=27,
    )
    assert run_is_valid(m)[0] is True
