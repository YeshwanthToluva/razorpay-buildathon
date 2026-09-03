from __future__ import annotations

from afin.db.seed import DEFAULT_SEED, SCENARIOS, TOTAL_PAYMENTS, generate
from afin.domain.enums import CustomerRiskFlag, PaymentState


def test_dataset_has_the_agreed_size():
    assert TOTAL_PAYMENTS == 50
    assert len(generate().payments) == 50


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
