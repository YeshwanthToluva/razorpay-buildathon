"""Deterministic synthetic dataset.

SYNTHETIC ONLY. No real customer, payment or communication data appears here or
is permitted to. Every value is generated from a seeded PRNG, so a given
(seed, version) always produces a byte-identical dataset -- which is what makes
"Experiment 17 differed from Experiment 12" answerable.

The 50 rows are composed from twelve named scenarios. A scenario fixes the
*situation* (failure category, counters, customer flags), never the outcome:
what happens when the system acts is the simulator's business, and baking
outcomes into the dataset would make every measured recovery rate circular.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, delete

from afin.domain.enums import (
    Channel,
    CustomerRiskFlag,
    FailureCategory,
    PaymentState,
    RecoveryState,
)
from afin.db.schema import customers, datasets, payments

DATASET_VERSION = "synthetic-v1"
DEFAULT_SEED = 20260304

#: Dataset "now". Fixed so window arithmetic is reproducible across real days.
EPOCH = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

_FAILURE_CODES = {
    FailureCategory.BANK_UNAVAILABLE: "GW_TIMEOUT",
    FailureCategory.PROCESSOR_ERROR: "PROCESSOR_5XX",
    FailureCategory.INSUFFICIENT_FUNDS: "INSUFFICIENT_FUNDS",
    FailureCategory.CARD_EXPIRED: "CARD_EXPIRED",
    FailureCategory.MANDATE_REVOKED: "MANDATE_REVOKED",
    FailureCategory.DO_NOT_HONOR: "DO_NOT_HONOR",
    FailureCategory.FRAUD_SUSPECTED: "FRAUD_RISK_HIGH",
}


@dataclass(frozen=True, slots=True)
class Scenario:
    tag: str
    count: int
    category: FailureCategory
    #: Inclusive amount range in paise.
    amount_range: tuple[int, int] = (50_000, 900_000)
    retry_count: int = 0
    contact_count: int = 0
    is_disputed: bool = False
    opted_out: bool = False
    risk_flag: CustomerRiskFlag = CustomerRiskFlag.NONE
    #: Hours from EPOCH until the recovery window closes. Negative = already shut.
    window_hours: int = 336  # 14 days
    hours_since_failure: int = 24
    #: Hours since the last attempt, when retry_count > 0.
    hours_since_attempt: int | None = None
    #: Healthy customers have a long clean history; risky ones do not.
    history: tuple[int, int] = (20, 1)


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("transient_bank_failure", 7, FailureCategory.BANK_UNAVAILABLE,
             hours_since_attempt=12),
    Scenario("insufficient_funds", 7, FailureCategory.INSUFFICIENT_FUNDS,
             history=(14, 3), hours_since_attempt=30),
    Scenario("expired_payment_method", 4, FailureCategory.CARD_EXPIRED),
    Scenario("repeated_payment_failure", 4, FailureCategory.DO_NOT_HONOR,
             retry_count=2, contact_count=1, history=(9, 5), hours_since_attempt=20),
    Scenario("customer_dispute", 4, FailureCategory.DO_NOT_HONOR, is_disputed=True,
             amount_range=(120_000, 800_000)),
    Scenario("possible_fraud", 3, FailureCategory.FRAUD_SUSPECTED,
             risk_flag=CustomerRiskFlag.FRAUD_WATCH, history=(3, 2)),
    Scenario("customer_opt_out", 4, FailureCategory.INSUFFICIENT_FUNDS, opted_out=True,
             hours_since_attempt=48),
    Scenario("high_value_transaction", 4, FailureCategory.BANK_UNAVAILABLE,
             amount_range=(1_200_000, 6_500_000), history=(40, 1),
             hours_since_attempt=18),
    Scenario("successful_retry", 4, FailureCategory.PROCESSOR_ERROR,
             history=(31, 1), hours_since_attempt=14),
    Scenario("failed_retry_then_recovery", 3, FailureCategory.INSUFFICIENT_FUNDS,
             retry_count=1, history=(12, 4), hours_since_attempt=26),
    Scenario("mandate_revoked", 1, FailureCategory.MANDATE_REVOKED, contact_count=1),
    Scenario("recovery_window_expired", 2, FailureCategory.BANK_UNAVAILABLE,
             window_hours=-48, hours_since_failure=400, hours_since_attempt=120),
    Scenario("max_retries_reached", 3, FailureCategory.DO_NOT_HONOR, retry_count=3,
             contact_count=2, history=(8, 6), hours_since_attempt=36),
)

TOTAL_PAYMENTS = sum(s.count for s in SCENARIOS)


@dataclass
class GeneratedDataset:
    version: str
    seed: int
    customers: list[dict] = field(default_factory=list)
    payments: list[dict] = field(default_factory=list)

    def manifest_sha256(self) -> str:
        """Hash of the dataset content, independent of insertion order."""
        payload = json.dumps(
            {
                "customers": sorted(self.customers, key=lambda r: r["id"]),
                "payments": sorted(self.payments, key=lambda r: r["id"]),
            },
            sort_keys=True,
            default=str,
        ).encode()
        return hashlib.sha256(payload).hexdigest()


def generate(seed: int = DEFAULT_SEED, version: str = DATASET_VERSION) -> GeneratedDataset:
    """Build the dataset in memory. Pure given (seed, version)."""
    rng = random.Random(seed)
    ds = GeneratedDataset(version=version, seed=seed)

    index = 0
    for scenario in SCENARIOS:
        for _ in range(scenario.count):
            index += 1
            cid = f"cust_{index:04d}"
            pid = f"pay_{index:04d}"

            lifetime, failures = scenario.history
            lifetime += rng.randint(-2, 6)
            failures = min(max(failures + rng.randint(-1, 1), 0), lifetime)

            ds.customers.append(
                {
                    "id": cid,
                    "dataset_version": version,
                    "segment": rng.choice(["RETAIL", "RETAIL", "SMB", "ENTERPRISE"]),
                    "opted_out": scenario.opted_out,
                    "preferred_channel": (
                        Channel.NONE.value
                        if scenario.opted_out
                        else rng.choice([Channel.EMAIL.value, Channel.EMAIL.value,
                                         Channel.SMS.value])
                    ),
                    "lifetime_payments": lifetime,
                    "lifetime_failures": failures,
                    "prior_successful_payments": lifetime - failures,
                    "risk_flag": scenario.risk_flag.value,
                }
            )

            low, high = scenario.amount_range
            # Round to whole rupees so amounts read like real invoices.
            amount = rng.randint(low // 100, high // 100) * 100

            failed_at = EPOCH - timedelta(hours=scenario.hours_since_failure)
            last_attempt = (
                None
                if scenario.retry_count == 0 or scenario.hours_since_attempt is None
                else EPOCH - timedelta(hours=scenario.hours_since_attempt)
            )

            ds.payments.append(
                {
                    "id": pid,
                    "dataset_version": version,
                    "customer_id": cid,
                    "invoice_id": f"inv_{index:04d}",
                    "amount_minor": amount,
                    "currency": "INR",
                    "payment_state": (
                        PaymentState.DISPUTED.value
                        if scenario.is_disputed
                        else PaymentState.FAILED.value
                    ),
                    "recovery_state": (
                        RecoveryState.IN_PROGRESS.value
                        if scenario.retry_count > 0
                        else RecoveryState.PENDING.value
                    ),
                    "failure_category": scenario.category.value,
                    "failure_code": _FAILURE_CODES[scenario.category],
                    "retry_count": scenario.retry_count,
                    "contact_count": scenario.contact_count,
                    "is_disputed": scenario.is_disputed,
                    "failed_at": failed_at,
                    "window_expires_at": EPOCH + timedelta(hours=scenario.window_hours),
                    "last_attempt_at": last_attempt,
                    "recovered_amount_minor": 0,
                    "scenario_tag": scenario.tag,
                }
            )

    return ds


def load_into(engine: Engine, ds: GeneratedDataset) -> None:
    """Replace any existing rows for this dataset version."""
    with engine.begin() as conn:
        conn.execute(delete(payments).where(payments.c.dataset_version == ds.version))
        conn.execute(delete(customers).where(customers.c.dataset_version == ds.version))
        conn.execute(delete(datasets).where(datasets.c.dataset_version == ds.version))
        conn.execute(
            datasets.insert().values(
                dataset_version=ds.version,
                seed=ds.seed,
                generated_at=datetime.now(timezone.utc),
                manifest_sha256=ds.manifest_sha256(),
                payment_count=len(ds.payments),
            )
        )
        conn.execute(customers.insert(), ds.customers)
        conn.execute(payments.insert(), ds.payments)
