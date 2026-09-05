"""Shared fixtures. Everything here is synthetic; no real customer data exists."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from afin.domain.enums import (
    Channel,
    CustomerRiskFlag,
    FailureCategory,
    PaymentState,
    RecoveryState,
    RiskType,
)
from afin.domain.models import CustomerSnapshot, PaymentSnapshot

NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_customer(**overrides) -> CustomerSnapshot:
    base = dict(
        id="cust_0001",
        segment="RETAIL",
        opted_out=False,
        preferred_channel=Channel.EMAIL,
        lifetime_payments=20,
        lifetime_failures=1,
        prior_successful_payments=19,
        risk_flag=CustomerRiskFlag.NONE,
    )
    return CustomerSnapshot(**{**base, **overrides})


def make_payment(**overrides) -> PaymentSnapshot:
    base = dict(
        id="pay_0001",
        customer_id="cust_0001",
        invoice_id="inv_0001",
        risk_type=RiskType.PAYMENT_FAILURE,
        amount_minor=250_000,  # Rs 2,500.00
        currency="INR",
        payment_state=PaymentState.FAILED,
        recovery_state=RecoveryState.PENDING,
        failure_category=FailureCategory.BANK_UNAVAILABLE,
        failure_code="GW_TIMEOUT",
        retry_count=0,
        contact_count=0,
        is_disputed=False,
        failed_at=NOW - timedelta(days=1),
        window_expires_at=NOW + timedelta(days=13),
        last_attempt_at=None,
        recovered_amount_minor=0,
        scenario_tag="transient_bank_failure",
    )
    return PaymentSnapshot(**{**base, **overrides})


@pytest.fixture
def customer() -> CustomerSnapshot:
    return make_customer()


@pytest.fixture
def payment() -> PaymentSnapshot:
    return make_payment()
