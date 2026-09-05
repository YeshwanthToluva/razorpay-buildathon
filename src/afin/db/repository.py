"""The only module that reads and writes payment rows.

The agent never sees this. It receives frozen snapshots.
"""

from __future__ import annotations

from sqlalchemy import Engine, select, update

from afin.domain.enums import (
    Channel,
    CustomerRiskFlag,
    FailureCategory,
    PaymentState,
    RecoveryState,
    RiskType,
)
from afin.domain.models import CustomerSnapshot, PaymentSnapshot
from afin.db.schema import customers, payments


def _to_payment(row) -> PaymentSnapshot:
    return PaymentSnapshot(
        id=row.id,
        customer_id=row.customer_id,
        invoice_id=row.invoice_id,
        risk_type=RiskType(row.risk_type),
        amount_minor=row.amount_minor,
        currency=row.currency,
        payment_state=PaymentState(row.payment_state),
        recovery_state=RecoveryState(row.recovery_state),
        failure_category=FailureCategory(row.failure_category),
        failure_code=row.failure_code,
        retry_count=row.retry_count,
        contact_count=row.contact_count,
        is_disputed=row.is_disputed,
        failed_at=row.failed_at,
        window_expires_at=row.window_expires_at,
        last_attempt_at=row.last_attempt_at,
        recovered_amount_minor=row.recovered_amount_minor,
        scenario_tag=row.scenario_tag,
    )


def _to_customer(row) -> CustomerSnapshot:
    return CustomerSnapshot(
        id=row.id,
        segment=row.segment,
        opted_out=row.opted_out,
        preferred_channel=Channel(row.preferred_channel),
        lifetime_payments=row.lifetime_payments,
        lifetime_failures=row.lifetime_failures,
        prior_successful_payments=row.prior_successful_payments,
        risk_flag=CustomerRiskFlag(row.risk_flag),
    )


def load_cases(
    engine: Engine, dataset_version: str
) -> list[tuple[PaymentSnapshot, CustomerSnapshot]]:
    """Every payment in the dataset, paired with its customer, ordered by id.

    Loaded as two queries rather than a join: `payments.id` and `customers.id`
    collide in a joined row mapping, and silently reading the wrong `id` would
    attach the wrong customer to a payment.
    """
    with engine.connect() as conn:
        payment_rows = conn.execute(
            select(payments)
            .where(payments.c.dataset_version == dataset_version)
            .order_by(payments.c.id)
        ).all()
        customer_rows = conn.execute(
            select(customers).where(customers.c.dataset_version == dataset_version)
        ).all()

    by_id = {row.id: _to_customer(row) for row in customer_rows}
    return [(_to_payment(row), by_id[row.customer_id]) for row in payment_rows]


def persist_payment(
    engine: Engine, payment: PaymentSnapshot, dataset_version: str
) -> None:
    """Write back a payment the reducer produced. Never called with agent output.

    Scoped to a dataset version: an unscoped update by id would silently write
    the same row in every dataset that happens to contain that id.
    """
    stmt = (
        update(payments)
        .where(payments.c.id == payment.id)
        .where(payments.c.dataset_version == dataset_version)
        .values(
            payment_state=payment.payment_state.value,
            recovery_state=payment.recovery_state.value,
            retry_count=payment.retry_count,
            contact_count=payment.contact_count,
            last_attempt_at=payment.last_attempt_at,
            recovered_amount_minor=payment.recovered_amount_minor,
        )
    )
    with engine.begin() as conn:
        conn.execute(stmt)
