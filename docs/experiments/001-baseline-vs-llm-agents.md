# Experiment 001 — Deterministic baseline vs LLM agents at Autonomy Level 2

**Date:** 2026-09-04
**Status:** results pending final run; setup and method fixed in advance

## Hypothesis

An LLM agent with a richer view of each case (failure category semantics,
customer payment history, recovery window position) will choose better recovery
actions than a fixed rule set, and so recover more of the at-risk revenue.

Secondary hypothesis, and the one that actually matters: **whatever the agent
proposes, `unsafe_actions_executed` stays at zero.**

## Change

One variable: the reasoner.

- `baseline` — `RuleBasedReasoner`, deterministic, no model. Autonomy Level 0.
- `agent --profile <p>` — `LLMReasoner`. Autonomy Level 2: the model proposes,
  deterministic policy approves or refuses, no human in the loop.

Everything downstream is held constant: identical dataset, identical simulator
seed, identical policy configuration and fingerprint, identical prompt version.

## Setup

    dataset_version     synthetic-v1 (50 payments, 13 scenarios)
    random_seed         20260304
    policy_version      policy-v1
    max_cycles          4 proposals per payment
    autonomy_level      0 (baseline) / 2 (agent)
    prompt_version      recovery-analyst-v1

Models: `gpt-5.4-mini` (LiteLLM), `z-ai/glm-5.2:free` and
`nvidia/nemotron-3-super-120b-a12b:free` (OpenRouter).

## Expected result

Agent arms recover more than the baseline; violations attempted may rise, but
violations prevented tracks it exactly and unsafe executions stay at zero.

## Actual result

<!-- filled from data/runs/*.json -->

## Observations

### Models that could not participate

Recorded because a negative result about tooling is still a result.

- `nvidia/nemotron-3.5-lightning-30b-a3b` (profile `nema`) — does not perform
  the task. It responds with meta-commentary about the shape of the prompt
  ("the user has been systematically typing out the pattern for PAYMENT
  RECOVERY ANALYST"). Every field of the schema is filled with prose. It was
  this behaviour that exposed the ledger column bug below.
- `minimax/minimax-m3:free` — ignores the response schema and returns
  non-JSON; surfaces as `PROPOSAL_INVALID`.
- `google/gemma-4-31b-it:free` — client-level failure on every request.

### Defects this experiment exposed

1. **`proposed_action` was `varchar(64)`.** The architecture deliberately lets
   an invented action reach the ledger so policy can deny it and metrics can
   count it — but the column was sized for valid action names, so a model that
   returned prose there crashed the insert and destroyed the whole run. The
   design said "record everything the agent proposes" and the schema quietly
   said "record it only if it was already well-behaved". Now `Text`, truncated
   at 512 characters by the ledger.

2. **Single-column primary keys on `payments` and `customers`.** Two dataset
   versions both containing `cust_0001` collided, and `persist_payment` updated
   by id alone — which would have written one experiment's financial state into
   another's rows. Keys are now `(dataset_version, id)`.

3. **API style is a confound.** Agent Framework's `OpenAIChatClient` uses the
   Responses API; the aiplanet gateway serves only chat completions. The same
   model, prompt, seed and policy produced materially different recovery
   numbers across the two APIs (36.8% vs 19.2% value recovery on `gpt-5.4-mini`).
   Anything comparing models must hold `api_style` constant, and it is now
   recorded per profile rather than assumed.

## Failure modes

## Interpretation

## Next experiment

Introduce the `MemoryStore` interface with a customer-memory implementation and
compare arms A (no memory) and B (customer memory) on this identical setup.
