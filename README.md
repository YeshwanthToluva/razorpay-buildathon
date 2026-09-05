# Revenue Recovery Lab

**We gave an AI agent 50 failed payments. Then we tried to find out whether it
deserved the right to recover them.**

<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/12a8343b-6518-44c7-8c5d-5ff67bdb286e" />


A reproducible experiment in agentic financial operations. Every proposal, policy
decision, execution, failure and refused action is committed to this repository.

> We didn't build a trustworthy agent.
> We built a system that can measure whether one is becoming trustworthy.

**Synthetic data only.** Every payment and customer here is generated from a
seeded PRNG. No real customer, payment or communication data exists anywhere in
this project, and no real financial transaction was ever made — the payment
provider is a simulator that opens no sockets.

---

## The result

| | Recovery | Revenue | |
|---|---:|---:|---|
| **Deterministic rules** | **43.9%** | ₹1,57,722 | reproduced byte-identically across 8 runs |
| **Agent** (best of 5 runs) | **33.0%** | ₹1,18,629 | observed range 24.4–33.0% |
| Gap | | **₹39,093** | |

And the half that did work:

```
137 actions were not authorized      113 denied outright
                                      24 held for a human who does not exist at level 2
  0 unsafe actions executed
```

Exploratory: **n = 1 per agent arm**, one dataset, one seed, one model. Observed
spread across repeats of the same configuration is several percentage points —
larger than most differences reported here. Read the arms as observations, not a
ranking.

## What we thought was wrong, and what the experiments showed

| | Change | Result |
|---|---|---|
| **002a** | Per-payment divergence analysis | Escalation accounted for **94.6%** of the gap. The agent diagnosed dead instruments correctly and escalated anyway. |
| **002e** | Made the factual state explicit | Context fidelity **81.4% → 95.5%**, excessive escalation **→ 0**. Revenue: **31.7% → 30.5%**, unchanged within noise. |
| **002g** | Reported executed outcomes back to the agent | Failure absorption **47% → 87%**. Retry-after-failure, cycles burned and revenue: **identical**, the last to the rupee. |

**Two experiments improved what the agent knew. Neither moved the economic
outcome.** On this dataset the residual gap does not look like an information
problem — what remains sits in stopping behaviour and action choice, about six
payments per run.

Both experiments also falsified earlier hypotheses from this same series
(repeated retrying was never the failure mode; the baseline retries *more* and
wins). Those records were corrected in place rather than left standing.

## Architecture

```
Agent  →  Policy  →  Gateway  →  Simulator  →  Ledger  →  Metrics
proposes  authorizes  executes    outcome      records    derived
```

The agent produces data; it never produces effects. Each arrow is a type the
previous stage cannot fabricate:

| Stage | Produces | Requires |
|---|---|---|
| `Reasoner` | `ProposedAction` | frozen snapshots; holds no handles |
| `policy.evaluate` | `PolicyDecision` | pure, deterministic, no I/O |
| `policy.authorize` | `AuthorizedAction` | mintable **only** inside `afin.policy` |
| `PaymentProvider` | `ProviderOutcome` | an `AuthorizedAction` |
| `apply_outcome` | `PaymentSnapshot` | a `ProviderOutcome` |

`AuthorizedAction.__post_init__` raises `PolicyBypassError` unless constructed
with a module-private token, so a route from proposal to money that skips policy
is an exception at the moment of the attempt, not a code-review finding someone
might miss.

## Evidence

Everything the site claims is derived from files in this repository, never from a
database — a schema drop already destroyed one set of results during this project.

```
data/
├── dataset/      synthetic payments + customers (csv, json, manifest)
├── runs/         21 run-metric files (json) + prometheus textfiles
├── ledger/       6 full audit-ledger exports, every event of every run
└── adversarial/  every action policy refused, with the rule that refused it
docs/
├── experiments/  4 experiment records, including the negative results
└── decisions/    2 architecture decision records
```

Two frozen versions exist. `synthetic-v1` is 50 payment failures and is the
dataset every committed experiment ran against; `synthetic-v2` adds checkout
abandonment and overdue receivables for 75 cases and is what the live console
loads. The manifest on disk is v2: `seed 20260304`, `sha256 1c605f6cc0f9…`.
Regenerating it must reproduce that hash.
<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/1defc2e7-f15a-4525-b664-14de7824f0b8" />

## Reproduce

```bash
cp .env.example .env          # add your own OpenAI-compatible endpoint
createdb agent_finance

# deterministic arm — no model, no cost, byte-identical every time
python -m afin.experiment.run --arm baseline

# agent arm
python -m afin.experiment.run --arm agent --profile primary

# analysis, from committed evidence
python analysis/divergence.py           # baseline vs agent, per payment
python analysis/feedback.py             # failure → next-decision transitions
python analysis/export_dataset.py       # dataset + refused actions
python analysis/build_console_data.py && python analysis/build_ui.py
```

Then open `ui/index.html` — the evaluation console, built from the committed
evidence and nothing else.

```bash
pytest tests/                    # 288 tests
pytest tests/ -m "not integration"   # no database required
pytest tests/adversarial         # 19 tests: a reasoner that tries to do damage
```

## Layout

```
src/afin/
  domain/       states, action space, the single financial-state reducer
  policy/       the deterministic boundary + authorization minting
  gateway.py    the only route from a proposal to a provider
  simulator/    mock payment adapter; seeded, no sockets
  agent/        proposal schema, reasoner port, prompts, orchestration
  audit/        append-only ledger
  metrics/      ledger-derived metrics + context fidelity
  db/           schema, repository, deterministic seed generator
  experiment/   run harness and comparison
analysis/       read-only analysis; imports nothing that mutates state
ui/             the local evaluation console
```

## Research log

| Record | |
|---|---|
| [001](docs/experiments/001-baseline-vs-llm-agents.md) | Baseline vs LLM agents |
| [002](docs/experiments/002-why-the-agent-underperforms.md) | Why the agent underperforms — divergence analysis |
| [002e](docs/experiments/002e-prompt-context-ablation.md) | Prompt / context ablation |
| [002g](docs/experiments/002g-outcome-feedback.md) | Outcome feedback |
| [ADR 0001](docs/decisions/0001-policy-is-authoritative.md) | Policy is authoritative; the model is advisory |
| [ADR 0002](docs/decisions/0002-model-as-experiment-dimension.md) | The model is an experiment dimension |

## Not built yet, deliberately

Memory (Mem0), external tools, MCP, Gmail, counterfactual evaluation, higher
autonomy levels. None of them can be interpreted while the noise floor is
unmeasured — the highest-value next experiment is a seed replication to
establish it, because several claims in this series sit inside it.
