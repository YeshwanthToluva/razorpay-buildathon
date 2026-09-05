"""Database schema.

Money is BIGINT minor units throughout. `audit_events` is append-only, enforced
by database triggers rather than convention: an audit ledger that application
code can quietly rewrite is not an audit ledger.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    ForeignKeyConstraint,
    PrimaryKeyConstraint,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

datasets = Table(
    "datasets",
    metadata,
    Column("dataset_version", String(128), primary_key=True),
    Column("seed", Integer, nullable=False),
    Column("generated_at", DateTime(timezone=True), nullable=False),
    Column("manifest_sha256", String(64), nullable=False),
    Column("payment_count", Integer, nullable=False),
)

customers = Table(
    "customers",
    metadata,
    Column("id", String(32), nullable=False),
    Column("dataset_version", String(128), ForeignKey("datasets.dataset_version"), nullable=False),
    Column("segment", String(32), nullable=False),
    Column("opted_out", Boolean, nullable=False, default=False),
    Column("preferred_channel", String(16), nullable=False),
    Column("lifetime_payments", Integer, nullable=False),
    Column("lifetime_failures", Integer, nullable=False),
    Column("prior_successful_payments", Integer, nullable=False),
    Column("risk_flag", String(32), nullable=False),
    # An id identifies a row only within its dataset: two dataset versions
    # legitimately both contain cust_0001, and they are different customers.
    PrimaryKeyConstraint("dataset_version", "id"),
)

payments = Table(
    "payments",
    metadata,
    Column("id", String(32), nullable=False),
    Column("dataset_version", String(128), ForeignKey("datasets.dataset_version"), nullable=False),
    Column("customer_id", String(32), nullable=False),
    Column("invoice_id", String(32), nullable=False),
    Column("amount_minor", BigInteger, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("payment_state", String(32), nullable=False),
    Column("recovery_state", String(32), nullable=False),
    Column("risk_type", String(32), nullable=False),
    Column("failure_category", String(48), nullable=False),
    Column("failure_code", String(64), nullable=False),
    Column("retry_count", Integer, nullable=False, default=0),
    Column("contact_count", Integer, nullable=False, default=0),
    Column("is_disputed", Boolean, nullable=False, default=False),
    Column("failed_at", DateTime(timezone=True), nullable=False),
    Column("window_expires_at", DateTime(timezone=True), nullable=False),
    Column("last_attempt_at", DateTime(timezone=True), nullable=True),
    Column("recovered_amount_minor", BigInteger, nullable=False, default=0),
    Column("scenario_tag", String(64), nullable=False),
    PrimaryKeyConstraint("dataset_version", "id"),
    ForeignKeyConstraint(
        ["dataset_version", "customer_id"],
        ["customers.dataset_version", "customers.id"],
    ),
)

runs = Table(
    "runs",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("experiment", String(64), nullable=False),
    Column("autonomy_level", Integer, nullable=False),
    Column("reasoner", String(64), nullable=False),
    Column("model", String(64), nullable=True),
    Column("model_config_json", Text, nullable=True),
    Column("prompt_version", String(64), nullable=True),
    Column("policy_version", String(32), nullable=False),
    Column("policy_fingerprint", String(32), nullable=False),
    Column("dataset_version", String(128), nullable=False),
    Column("random_seed", Integer, nullable=False),
    Column("notes", Text, nullable=True),
)

payment_attempts = Table(
    "payment_attempts",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", String(64), nullable=False),
    Column("payment_id", String(32), nullable=False),
    Column("cycle", Integer, nullable=False),
    Column("action", String(32), nullable=False),
    Column("provider_ref", String(64), nullable=True),
    Column("result", String(16), nullable=False),
    Column("failure_code", String(64), nullable=True),
    Column("amount_minor", BigInteger, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("seq", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", String(64), nullable=False),
    Column("payment_id", String(32), nullable=False),
    Column("cycle", Integer, nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("event_type", String(48), nullable=False),
    Column("observed_state_json", Text, nullable=False),
    Column("agent_diagnosis", Text, nullable=True),
    # Text, not a bounded string: an invented "action" may be arbitrary prose,
    # and the whole point of letting it reach the ledger is that it gets
    # recorded. executed_action stays bounded -- only real actions execute.
    Column("proposed_action", Text, nullable=True),
    # A concise, structured justification. Never chain-of-thought.
    Column("reasoning_summary", Text, nullable=True),
    Column("confidence", Float, nullable=True),
    # The model's structured answer, verbatim as parsed. This is the JSON the
    # schema asked for -- NOT chain-of-thought. Providers that return a separate
    # `reasoning_content` field (nemotron, muse-glimmer) have it read from
    # nowhere and stored nowhere.
    Column("raw_proposal_json", Text, nullable=True),
    # Observable factual claims restated by the model, for context-fidelity
    # measurement (experiment 002e). These are CLAIMS, not state: they are
    # compared against the payment record, never trusted as it.
    Column("claimed_opted_out", Boolean, nullable=True),
    Column("claimed_prior_successful_payments", Integer, nullable=True),
    Column("claimed_last_attempt_outcome", String(16), nullable=True),
    Column("policy_decision", String(24), nullable=True),
    Column("policy_rule", String(48), nullable=True),
    Column("policy_reason", Text, nullable=True),
    Column("risk_level", String(16), nullable=True),
    Column("executed_action", String(32), nullable=True),
    Column("execution_result", String(16), nullable=True),
    Column("revenue_recovered_minor", BigInteger, nullable=False, default=0),
    Column("resulting_payment_state", String(32), nullable=True),
    Column("resulting_recovery_state", String(32), nullable=True),
    Column("final_state", String(32), nullable=True),
    Column("error", Text, nullable=True),
)

#: Append-only enforcement. Applied after create_all.
APPEND_ONLY_DDL = """
CREATE OR REPLACE FUNCTION afin_audit_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only (attempted %)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_no_update ON audit_events;
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION afin_audit_append_only();

DROP TRIGGER IF EXISTS audit_no_delete ON audit_events;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION afin_audit_append_only();
"""


#: Additive migration. create_all() creates missing tables but never alters an
#: existing one, and dropping tables is what destroyed the 002 evidence, so new
#: columns are added in place and idempotently.
ADDITIVE_COLUMNS_DDL = """
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS claimed_opted_out BOOLEAN;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS claimed_prior_successful_payments INTEGER;
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS claimed_last_attempt_outcome VARCHAR(16);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS risk_type VARCHAR(32) NOT NULL DEFAULT 'PAYMENT_FAILURE';
ALTER TABLE payments ALTER COLUMN failure_category TYPE VARCHAR(48);
-- Widened in place for experiment 002e: prompt version names outgrew 32 chars.
-- Widening a varchar never rewrites or loses data.
ALTER TABLE runs ALTER COLUMN prompt_version TYPE VARCHAR(64);
ALTER TABLE runs ALTER COLUMN dataset_version TYPE VARCHAR(128);
ALTER TABLE payments ALTER COLUMN dataset_version TYPE VARCHAR(128);
ALTER TABLE customers ALTER COLUMN dataset_version TYPE VARCHAR(128);
ALTER TABLE datasets ALTER COLUMN dataset_version TYPE VARCHAR(128);
"""
