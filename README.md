# Agent Influence in Finance

An experimental laboratory for studying how an AI agent's autonomy, memory and
tool access affect its ability to recover at-risk revenue — and whether
deterministic controls can reliably keep it inside safe boundaries.

**Synthetic data only. No real financial transactions.**

## The central architectural claim

    MEMORY         information the agent may use
    POLICY         what the agent is allowed to do
    TOOLS          capabilities the agent may request
    AUTHORIZATION  whether a requested capability may execute
    AUDIT          what actually happened

These stay separate. Memory never grants permission. The model proposes; it
never executes. The claim is enforced by types rather than by convention:

    Reasoner  -> ProposedAction     a frozen dataclass; holds no handles
    Gateway   -> PolicyDecision     pure, deterministic evaluation
    Policy    -> AuthorizedAction   mintable only inside afin.policy
    Provider  -> ProviderOutcome    requires an AuthorizedAction
    Reducer   -> PaymentSnapshot    requires a ProviderOutcome

`AuthorizedAction.__post_init__` raises `PolicyBypassError` unless constructed
with a module-private token, so a route from proposal to money that skips policy
is an exception at the moment of the attempt, not a review comment someone might
miss.

The measurement that matters:

    unsafe actions proposed  >= 0   an experimental observation
    unsafe actions executed  == 0   an invariant; a run reporting otherwise
                                    is an architectural failure, and says so

## Sprint 1 scope — Autonomy Level 2

    synthetic dataset -> agent -> structured proposal -> deterministic policy
      -> action gateway -> simulator -> audit ledger -> metrics

Not built yet, by design: Mem0, Composio, Gmail, MCP tools, counterfactual
evaluation, adaptive learning, a full evaluation CLI. Those are later
experiments and are listed in the long-term plan, not in this code.

## Layout

    src/afin/
      domain/       states, action space, the single financial-state reducer
      policy/       the deterministic boundary + authorization minting
      gateway.py    the only route from a proposal to a provider
      simulator/    mock Razorpay adapter; seeded, no sockets
      agent/        proposal schema, reasoner port, prompts, orchestration
      audit/        append-only ledger
      metrics/      ledger-derived metrics + prometheus export
      db/           schema, repository, deterministic seed generator
      experiment/   run harness and run comparison
    docs/decisions/    architecture decision records
    docs/experiments/  experiment records, including negative results
    tests/             policy, simulator, agent, adversarial, integration

## Setup

    cp .env.example .env          # fill in credentials; .env is gitignored
    createdb agent_finance

Dependencies (`agent-framework`, `sqlalchemy`, `psycopg2`, `pydantic`, `pytest`)
are already present in the ai-planet platform venv; nothing was added to it.

    PY=/home/nitin/Documents/ai-planet/platform/aiplanet_platform/venv/bin/python

## Running

    $PY -m pytest tests/                                      # full suite
    $PY -m pytest tests/ -m "not integration"                 # no database

    PYTHONPATH=src $PY -m afin.experiment.run --arm baseline
    PYTHONPATH=src $PY -m afin.experiment.run --arm agent --profile gpt
    PYTHONPATH=src $PY -m afin.experiment.run --arm agent --profile nema

    PYTHONPATH=src $PY -m afin.experiment.compare data/runs/<a>.json data/runs/<b>.json

Each run writes `data/runs/<run_id>.json` and `<run_id>.prom`, and records its
reasoner, model, prompt version, policy fingerprint, dataset version and seed —
so two runs that differ can be explained rather than argued about.

## Arms

    baseline       Autonomy Level 0. Deterministic rules, no model.
    agent --gpt    Autonomy Level 2. gpt-5.4-mini via LiteLLM.
    agent --nema   Autonomy Level 2. nvidia/nemotron-3.5-lightning-30b-a3b.

The baseline is not decoration: without a deterministic control on the identical
dataset, seed and policy, an agent's recovery rate is a number with nothing to
compare against.

## Reproducibility

The dataset is generated from a seeded PRNG and hashed. The simulator's
per-attempt draw is SHA-256 of `(seed, payment_id, action, attempt)` rather than
a stream, so a payment's outcome does not depend on how many payments ran before
it — re-running one case in isolation reproduces, and adding a row to the dataset
does not silently change every later result.
