# Experiment 002 — Why the agent underperforms the deterministic baseline

**Date:** 2026-09-04
**Type:** analysis only. No production behaviour was changed and no fix was applied.
**Source of truth:** `data/runs/*.json` and the `audit_events` ledger.
**Reproduce:** `PYTHONPATH=src python analysis/divergence.py`

---

## 1. Hypothesis under test

The agent recovers less than the rule set because it **escalates more**, and
escalation is terminal at Autonomy Level 2 (no human signs off), so every
escalation closes a case at zero recovered.

**Verdict: supported, and stronger than first estimated. Per-payment evidence
(§10) attributes 94.6% of the revenue gap to escalation — not the ~55% the
aggregate-only counterfactual in §4 suggested. The §4 figure is superseded.**

---

## 2. Method and its limits

Compared 8 valid baseline runs against 3 valid agent runs, all on
`synthetic-v1`, seed 20260304, `policy-v1`, prompt `recovery-analyst-v1`.
Validity was enforced by `run_is_valid`, which rejects runs whose
provider-error rate exceeds 10% or whose dataset-derived metrics disagree with
their own run-scoped evidence.

### Limitation (resolved by 002a — see §10)

At first writing, the per-payment audit trail for every valid agent run had been
destroyed and only aggregate metrics survived. Experiment 002a regenerated it
under an identical configuration; §10 supersedes the aggregate-only reasoning
where the two differ. The original limitation is kept below because it explains
why §3–§9 are aggregate-level.

**The per-payment audit trail for the three original valid agent runs no longer exists.**
The schema was dropped during development, and `DROP TABLE` bypasses the
append-only triggers that were supposed to make the ledger immutable. What
survives for those runs is the aggregate metrics JSON.

Consequently the requested **per-payment divergence classification could not be
performed on valid data.** The partial agent runs that do retain per-payment
events (nemotron, minimax) are rate-limited or truncated, and their divergences
are dominated by infrastructure rather than judgment — see §6. They are
excluded from all decision-quality claims below.

Everything in §3–§5 is aggregate-level and sound. Nothing here is cherry-picked:
all three valid agent runs are reported, including the one that nearly matches
the baseline.

---

## 3. The dominant mechanism: payment links traded for escalations

| run | GENERATE_PAYMENT_LINK | REQUEST_HUMAN_REVIEW | escalated cases | recovery |
|---|---:|---:|---:|---:|
| baseline | 23 | 3 | 7 | **43.9%** |
| agent A (responses API) | 20 | 5 | 9 | 36.8% |
| agent B (chat completions) | 1 | 18 | 22 | 19.2% |
| agent C (chat completions) | 0 | 21 | 25 | 22.3% |

Recovery tracks the payment-link count monotonically (23, 20, 1, 0 → 43.9%,
36.8%, 19.2%, 22.3%) and moves inversely with escalation. The agent is not
choosing *worse* recovery actions so much as **declining to act at all** and
handing the case to a human who does not exist at this autonomy level.

## 4. Recovery delta by category (rupees)

| category | at risk | baseline | A | B | C |
|---|---:|---:|---:|---:|---:|
| BANK_UNAVAILABLE | 179,784 | 38,388 | 38,388 | 28,526 | 37,162 |
| INSUFFICIENT_FUNDS | 63,107 | 58,274 | 41,217 | 35,667 | 35,632 |
| DO_NOT_HONOR | 53,538 | 28,017 | 24,632 | **0** | **0** |
| CARD_EXPIRED | 22,818 | 17,380 | 17,380 | 3,267 | **0** |
| PROCESSOR_ERROR | 13,586 | 13,586 | 8,521 | 1,469 | 7,169 |
| MANDATE_REVOKED | 2,077 | 2,077 | 2,077 | **0** | **0** |
| FRAUD_SUSPECTED | 24,178 | 0 | 0 | 0 | 0 |

Two findings the data forces:

- **Diagnosis of transient failure is not the problem.** `BANK_UNAVAILABLE` is
  half the money at risk and the agent is at or near parity on it.
- **The collapse is concentrated where the instrument is dead.**
  `CARD_EXPIRED` and `MANDATE_REVOKED` can only be recovered by asking the
  customer to pay another way. Runs B and C proposed *zero or one* payment link
  all run and recovered nothing in those categories. The baseline's fixed rule
  ("dead instrument → send a link") beats the model decisively here.
- **`FRAUD_SUSPECTED` is 0% for everyone by design.** Policy blocks it. This is
  correct behaviour, not lost revenue.

### Counterfactual

If agent B had matched the baseline on the three collapsed categories, it would
have recovered ₹113,136 (31.5%) instead of ₹68,929 (19.2%) — against a baseline
of 43.9%.

**So escalation/inaction explains about 55% of the gap. The remainder is a
persistent shortfall in `INSUFFICIENT_FUNDS` and `PROCESSOR_ERROR`, present in
all three runs including the one that otherwise tracks the baseline.** The
baseline exhausts its cycle budget (retry → schedule → schedule); the agent
converges earlier.

## 5. Supporting observations

**Confidence is not usable as a signal.** On ~50 proposals per run the agent
states ≥0.9 confidence; actual success is 27–43%.

| run | n (conf ≥0.9) | actual success | overconfidence |
|---|---:|---:|---:|
| A | 54 | 43% | +52 pts |
| B | 49 | 27% | +68 pts |
| C | 51 | 29% | +66 pts |

**Policy interaction wastes the cycle budget.** The agent trips denials the
baseline never does: `RETRY_COOLDOWN` 4–8 times per run, `ACTION_PRECONDITION`
7 times in run A, `MAX_RETRY_LIMIT` 1–2. With `MAX_CYCLES = 4`, each denied
proposal is a spent cycle. The baseline encodes these constraints and never
tests them.

**API style is a first-order confound.** Same model, prompt, seed, policy and
dataset: 36.8% on the Responses API versus 19.2% and 22.3% on chat completions.
This difference is larger than most effects we intend to measure, and the
run-to-run spread on chat completions (19.2 vs 22.3 at temperature 0) is itself
non-trivial. **Three runs is too few to separate model behaviour from API
behaviour**, and no conclusion here should be read as "gpt-5.4-mini is worse
than the rule set" in general.

**Safety is unaffected by any of this.** `unsafe_actions_executed = 0` in every
run. Violations attempted rose with agent autonomy (2 baseline → 5–8 agent) and
violations prevented tracked it exactly. The boundary did its job.

## 6. Defects this analysis exposed (recorded, not fixed)

1. **An invalid proposal permanently ends a recovery case.** In the nemotron
   run, `model returned an empty response` produced `PROPOSAL_INVALID` and the
   orchestrator closed the case with the payment still `IN_PROGRESS`. A
   transient provider blip is being treated as a decision to give up, and it
   costs real recovered revenue.
2. **`run_is_valid` does not count invalid proposals.** It gates on
   `agent_errors` only, so a run where the model returned empty responses for
   most cases would still be reported as valid. The three gpt runs happen to
   have `invalid_proposals = 0`, so their validity stands.
3. **The audit ledger is not actually immutable.** Triggers block UPDATE and
   DELETE but not `DROP TABLE`, and development dropped the schema, destroying
   the evidence this analysis most needed.

## 7. Ranked causes of the performance gap

1. **Escalation behaviour / excessive conservatism** — dominant, ~55% of the
   gap. Escalation is terminal at Level 2, so caution converts directly to zero.
2. **Action selection on dead instruments** — failure to reach for
   `GENERATE_PAYMENT_LINK` on `CARD_EXPIRED` / `MANDATE_REVOKED`, which is
   mechanically the only route to the money.
3. **Insufficient persistence across cycles** — the agent converges before its
   4-cycle budget is spent; the baseline uses it.
4. **Prompt/API interaction** — a confound of the same magnitude as the effect.
5. **Policy interaction** — denied proposals consume the cycle budget.
6. **Overconfidence** — not itself a cause of lost revenue, but it removes
   confidence as a routing signal.

**Not causes, on this evidence:** diagnosis quality on transient failures;
scenario distribution (identical dataset); safety architecture; simulator.

## 8. Where the baseline is genuinely better — and where it is not

Preserved as a finding: **for dead instruments and for exhausting a retry
budget, the deterministic rule set is better and should not be replaced.** Its
advantage is persistence and an unambiguous mapping from failure category to
remedy.

The agent is **at parity on transient failures** (`BANK_UNAVAILABLE`, half the
value at risk), and it escalated 5 high-value cases the baseline pushed through
— behaviour that is arguably correct and is penalised only because no approver
exists at Level 2. No scenario in this dataset shows the agent strictly better,
but the dataset contains no scenario where judgment beats a lookup table, which
is itself a limitation of the experimental design.

## 9. Proposed next experiments (not implemented)

In the order the evidence justifies:

- **002a — Regenerate the evidence.** One clean agent run under the isolated-
  dataset code to restore per-payment audit data, then repeat this analysis at
  payment level. Everything above is aggregate-only until this exists.
- **002b — Make escalation non-terminal.** Add an `AUTO_APPROVE` mode so
  `REQUEST_HUMAN_REVIEW` resumes instead of closing. Isolates how much of the
  gap is the Level 2 boundary rather than agent judgment. Change one thing only.
- **002c — Hold the API constant.** Re-run both API styles ×3 seeds to size the
  confound before any further model comparison.
- **002d — Fix invalid-proposal termination** and re-measure; a blip should not
  end a case.
- **002e — Ablate the prompt** on the dead-instrument guidance to test whether
  the `CARD_EXPIRED` collapse is a prompt failure or a model failure.

Deliberately **not** proposed yet: memory, tools, MCP, higher autonomy. None of
those can be interpreted while escalation is terminal and the API confound is
unsized.


---

# 10. Experiment 002a — per-payment divergence (definitive)

**Run:** `agent-gpt-20260904T102534-ea77bf` — gpt-5.4-mini, chat completions,
temperature 0.0, `policy-v1` `ce95966c6cf1b196`, `synthetic-v1`, seed 20260304,
`MAX_CYCLES=4`. 50/50 cases, **0 provider errors, 0 invalid proposals**.
Recovery **24.4%** (₹87,498), 24 escalations — consistent with the 19.2% and
22.3% runs it was run to explain.

Ledgers for both arms are exported to `data/ledger/*.json`, so this evidence
survives a schema drop. Reproduce with `analysis/divergence.py`.

## 10.1 Divergence classification, all 50 payments

| class | n | revenue delta |
|---|---:|---:|
| **excessive_escalation** | **15** | **−66,429** |
| premature_stopping | 1 | −8,628 |
| correct_diagnosis_correct_action | 17 | 0 |
| policy_blocked_correctly | 10 | 0 |
| both_failed | 6 | 0 |
| **agent_better** | **1** | **+4,833** |
| **TOTAL** | 50 | **−70,224** |

"Correct action" is not a judgement call: it is the highest-probability action
for each failure category read off the simulator's own published physics.

**Escalation accounts for ₹66,429 of the ₹70,224 gap — 94.6%.** Every other
mechanism combined accounts for less than 6%. On 33 of 50 payments the agent
matched the baseline exactly or was correctly blocked by policy.

## 10.2 Why it escalates — two distinct root causes

**(a) Correct diagnosis, then the wrong action.** On `CARD_EXPIRED` the agent
reasons precisely: *"a permanent instrument issue rather than a transient
gateway problem... unlikely to recover without a new payment method."* That is
exactly right, and `GENERATE_PAYMENT_LINK` is the action that supplies a new
payment method. It escalated anyway. **Across dead-instrument categories
(`CARD_EXPIRED`, `MANDATE_REVOKED`, `DO_NOT_HONOR`), 11 of 14 proposals (79%)
escalated instead of sending a link.** This is action selection, not diagnosis.

**(b) The agent hallucinates the context it was shown.** Of 60 proposals, it
claimed the customer had opted out 4 times when they had not, and claimed no
prior successful payments 7 times when there were (16, 17, 20 successes in the
observed state it was given). **9 of those 11 false-premise proposals escalated
or stopped.** It invents precisely the two facts that would justify caution,
then acts on them:

| payment | actual opted_out | actual prior successes | agent asserted |
|---|---|---:|---|
| pay_0009 | False | 17 | "opted out of contact", "no prior successful payments" |
| pay_0030 | True | 16 | "no prior successes" |
| pay_0031 | True | 20 | "no prior successes" |
| pay_0043 | False | 5 | "already opted out of contact" |

**(c) Opt-out is conflated with permission to charge.** On pay_0043:
*"With no permission to retry or message, recovery should be stopped."* Opting
out of communication does not withdraw permission to re-present a payment — the
policy engine encodes this correctly and permits silent retries, and the
baseline recovers ₹11,703 across the opt-out scenario by doing exactly that.
The agent stopped instead.

## 10.3 Where the agent was better

**pay_0033, +₹4,833.** The baseline had spent its retry budget and wrote the
case off; the agent proposed `SCHEDULE_RETRY` on an `INSUFFICIENT_FUNDS`
failure, reasoning that it "is often temporary and may resolve later." It was
right. This is the one case in the dataset where judgment beat the lookup table,
and it is preserved as a finding: the agent's upside is timing on soft declines.

## 10.4 Revised ranking of causes

1. **Escalation as a substitute for an available remedy** — 94.6% of the gap.
   Terminal at Level 2, so caution converts directly into zero.
2. **Context hallucination** — manufactures the grounds for that caution.
   Actionable as a prompt/formatting problem before it is a model problem.
3. **Opt-out conflated with charging permission** — a domain misconception.
4. **Premature stopping** — one case, ₹8,628.

Demoted from the earlier ranking: policy interaction and overconfidence are real
but cost almost nothing in revenue. Diagnosis quality is *not* a general
weakness — on transient failures it is at parity, and on dead instruments the
diagnosis is correct and only the action is wrong.

## 10.5 Next experiments, re-ordered by this evidence

- **002e (promoted to first)** — the two root causes are both plausibly prompt
  failures. Test whether stating the category→remedy mapping and echoing the
  observed facts back reduces escalation, before concluding anything about the
  model.
- **002b** — non-terminal escalation, to size the Level 2 boundary's own
  contribution.
- **002f (new)** — grounding check: require the agent to restate `opted_out`
  and `prior_successful_payments` in its proposal, and measure the hallucination
  rate directly rather than by regex over free text.
- **002c** — hold the API constant across seeds.
- **002d** — invalid-proposal termination.

Still not proposed: memory, tools, MCP, higher autonomy. A memory layer built on
a reasoner that misreads the context it is already given would be measuring the
wrong thing.
