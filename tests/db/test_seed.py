from __future__ import annotations

from afin.db.seed import (
    DATASET_V1,
    DATASET_V2,
    DEFAULT_SEED,
    PAYMENT_FAILURE_SCENARIOS,
    SCENARIOS,
    TOTAL_PAYMENTS,
    TOTAL_PAYMENTS_V1,
    generate,
)
from afin.domain.enums import RiskType
from afin.domain.enums import CustomerRiskFlag, PaymentState


def test_v1_is_frozen_at_the_size_every_experiment_cites():
    """The committed experiment records all cite 50 payment-failure cases."""
    assert TOTAL_PAYMENTS_V1 == 50
    assert len(generate(version=DATASET_V1).payments) == 50


def test_v1_contains_only_payment_failures():
    assert {p["risk_type"] for p in generate(version=DATASET_V1).payments} == {
        RiskType.PAYMENT_FAILURE.value
    }


def test_v2_covers_all_three_risk_types_in_the_brief():
    kinds = {p["risk_type"] for p in generate(version=DATASET_V2).payments}
    assert kinds == {
        RiskType.PAYMENT_FAILURE.value,
        RiskType.CHECKOUT_ABANDONMENT.value,
        RiskType.OVERDUE_RECEIVABLE.value,
    }
    assert TOTAL_PAYMENTS == len(generate(version=DATASET_V2).payments)


def test_v2_is_a_superset_of_v1():
    """Extending the brief must not disturb the cases already studied."""
    v1 = {p["id"]: p for p in generate(version=DATASET_V1).payments}
    v2 = {p["id"]: p for p in generate(version=DATASET_V2).payments}
    for pid, row in v1.items():
        for field in ("amount_minor", "failure_category", "scenario_tag", "retry_count"):
            assert v2[pid][field] == row[field], f"{pid}.{field} drifted"


def test_generation_is_reproducible_for_a_fixed_seed():
    assert generate(DEFAULT_SEED).manifest_sha256() == generate(DEFAULT_SEED).manifest_sha256()


def test_a_different_seed_produces_a_different_dataset():
    assert generate(1).manifest_sha256() != generate(2).manifest_sha256()


def test_manifest_is_insertion_order_independent():
    ds = generate()
    first = ds.manifest_sha256()
    ds.payments.reverse()
    ds.customers.reverse()
    assert ds.manifest_sha256() == first


def test_every_scenario_is_represented():
    tags = {p["scenario_tag"] for p in generate().payments}
    assert tags == {s.tag for s in SCENARIOS}


def test_every_payment_has_exactly_one_customer():
    ds = generate()
    ids = [c["id"] for c in ds.customers]
    assert len(ids) == len(set(ids))
    assert {p["customer_id"] for p in ds.payments} == set(ids)


def test_amounts_are_positive_integers_in_minor_units():
    for p in generate().payments:
        assert isinstance(p["amount_minor"], int)
        assert p["amount_minor"] > 0


def test_no_payment_starts_with_recovered_money():
    """A dataset that pre-loads recoveries would make every metric circular."""
    for p in generate().payments:
        assert p["recovered_amount_minor"] == 0
        assert p["payment_state"] != PaymentState.RECOVERED.value


def test_scenario_flags_land_on_the_right_rows():
    by_tag: dict[str, list[dict]] = {}
    ds = generate()
    for p in ds.payments:
        by_tag.setdefault(p["scenario_tag"], []).append(p)
    customers = {c["id"]: c for c in ds.customers}

    assert all(p["is_disputed"] for p in by_tag["customer_dispute"])
    assert all(not p["is_disputed"] for p in by_tag["transient_bank_failure"])
    assert all(p["retry_count"] >= 3 for p in by_tag["max_retries_reached"])
    assert all(
        customers[p["customer_id"]]["opted_out"] for p in by_tag["customer_opt_out"]
    )
    assert all(
        customers[p["customer_id"]]["risk_flag"] == CustomerRiskFlag.FRAUD_WATCH.value
        for p in by_tag["possible_fraud"]
    )
    assert all(
        p["window_expires_at"] < p["failed_at"] + __import__("datetime").timedelta(days=400)
        for p in by_tag["recovery_window_expired"]
    )


def test_customer_history_is_internally_consistent():
    for c in generate().customers:
        assert c["lifetime_failures"] <= c["lifetime_payments"]
        assert c["prior_successful_payments"] == c["lifetime_payments"] - c["lifetime_failures"]
        assert c["prior_successful_payments"] >= 0
