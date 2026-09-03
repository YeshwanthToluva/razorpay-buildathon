# 0001 — Policy is authoritative; the model is advisory

**Status:** accepted, Sprint 1

## Context

The agent must be free to reason about recovery, including reasoning badly. The
question this laboratory exists to answer is not "can the model be trusted?" but
"does it matter whether the model can be trusted?"

## Decision

The LLM produces data. It never produces effects.

    Reasoner  -> ProposedAction        (a frozen dataclass; no handles)
    Gateway   -> PolicyDecision        (pure, deterministic evaluation)
    Policy    -> AuthorizedAction      (mintable only inside afin.policy)
    Provider  -> ProviderOutcome       (requires an AuthorizedAction)
    Reducer   -> PaymentSnapshot       (requires a ProviderOutcome)

Each arrow is a type the previous stage cannot fabricate. `AuthorizedAction`
raises `PolicyBypassError` unless constructed with a module-private token, so a
path from proposal to money that skips policy is not a code-review finding
someone might miss -- it is an exception at the moment of the attempt.

## Consequences

- The reasoner holds no database handle, no provider and no ledger. This is the
  actual enforcement; the prompt is not a security control.
- `REQUIRE_APPROVAL` sets `allowed=False`. At Autonomy Level 2 nobody signs off,
  so a soft allow would execute high-value actions unattended.
- Invented actions reach the policy engine rather than being rejected at the
  schema layer, so `UNSUPPORTED_ACTION` can deny them and the ledger records
  that the agent asked. Suppressing the proposal is not the same as being safe.
- The orchestrator's own closure moves go through the gateway too. Nothing gets
  a side entrance, including the system itself.

## What would falsify this

`unsafe_actions_executed > 0` in any run. The metric is computed from the
ledger, not from an in-process counter, and the metrics tests prove the alarm
fires by writing an executed-without-an-allow event by hand -- a safety metric
only ever observed reading zero is indistinguishable from a broken one.
