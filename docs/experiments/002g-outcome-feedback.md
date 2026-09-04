# Experiment 002g — Outcome feedback

**Date:** 2026-09-04
**Design:** paired exploratory ablation, **n = 1 run per arm**, 157 model calls.
**Reproduce:** `PYTHONPATH=src python analysis/feedback.py`

---

## 1. Question

002e observed repeated retries after failure and named missing execution
feedback as the likely cause. That was a hypothesis with three live candidates:

- **A** — the agent does not know its attempt failed.
- **B** — the retry strategy is intrinsically over-aggressive, feedback or not.
- **C** — the agent understands the failure and still judges another retry
  rational.

002g separates A from B and C.

## 2. Design

**The only change:** after every executed action, its factual result is reported
to the next turn.

```json
{"attempt_number": 1, "execution_status": "FAILED",
 "failure_reason": "INSUFFICIENT_FUNDS", "last_action": "RETRY_PAYMENT"}
```

Values only. A characterisation such as *"retrying is unlikely to work"* would
change the agent's decision policy rather than its information, and was
deliberately excluded.

**Frozen:** model `gpt-5.4-mini`, chat completions, temperature 0.0, prompt
`recovery-analyst-v2-explicit-state`, dataset `synthetic-v1`, seed 20260304,
policy `policy-v1` `ce95966c6cf1b196`, `MAX_CYCLES=4`, autonomy level 2,
simulator, gateway, authorization, pacing, concurrency. The base prompt was
asserted byte-identical across arms; the feedback block is the only difference.

Both arms are additionally asked to restate the previous attempt's outcome. That
measures **absorption** — whether the agent registered the failure — and asking
it in both arms keeps it out of the experimental variable.

Both runs: 50/50 cases, 0 provider errors, 0 invalid proposals, valid.

## 3. Result — hypothesis A is rejected

| | control | feedback |
|---|---:|---:|
| **Did it restate the failure correctly?** | **7 / 15 (47%)** | **13 / 15 (87%)** |
| — contradicted | 8 | **2** |
| failures observed | 15 | 15 |
| **retry after failed retry** | **10** | **10** |
| repeated same action after failure | 13 | 14 |
| cycles burned after a failed action | 15 | 15 |
| recovery after a failure | 5 | 5 |
| **revenue recovered after a failure** | **25,896** | **25,896** |

**P(next action | previous action FAILED)**

| next action | control | feedback |
|---|---:|---:|
| RETRY_PAYMENT | 40% | 33% |
| SCHEDULE_RETRY | 27% | 33% |
| GENERATE_PAYMENT_LINK | 33% | 33% |
| *(retry-type total)* | **67%** | **66%** |

**The feedback was absorbed and the behaviour did not move.** Correct
restatement nearly doubled (47% → 87%) while retry-after-failed-retry was
identical (10 vs 10) and revenue recovered after a failure matched **to the
rupee** (₹25,896). Aggregate recovery was **33.0% in both arms** (₹118,629 each;
79 vs 78 proposals, 32 vs 32 retries, 5 vs 5 escalations, 16 vs 16 stops).

A is rejected: the agent was not retrying because it did not know.

## 4. B versus C — the evidence favours C, and the agent is not wrong

In the feedback arm, cases that retried after being told the retry failed
restate the failure and then give a physics-consistent justification:

> *pay_0010* — restated last outcome: **FAILED**.
> "failure_reason is INSUFFICIENT_FUNDS and the instrument is reusable, so
> retrying later is appropriate."

> *pay_0020* — restated last outcome: **FAILED**.
> "a temporary processor-type error, and the instrument is reusable, so a later
> re-presentment is appropriate."

This is C: it understands the failure and judges another attempt rational.

**And that judgement is correct.** Under the simulator's own physics each
attempt is an independent draw — `SCHEDULE_RETRY` on `BANK_UNAVAILABLE` succeeds
with p ≈ 0.85, on `INSUFFICIENT_FUNDS` p ≈ 0.55 — so a second attempt after a
failure genuinely has good odds. Decisively: **the deterministic baseline retries
more than the agent (48 retries vs 32) and recovers more.**

## 5. This overturns a claim from 002e

002e attributed part of the gap to "repeated retries" and to the treatment
eliminating `pay_0033`. Both readings do not survive:

- **Repeated retrying is not a failure mode.** The baseline does it more and
  wins. 002g's `retry_after_failed_retry` is identical across arms while
  recovery is identical too — the behaviour is orthogonal to the outcome.
- **`pay_0033` was not eliminated by the treatment.** It is absent in the 002e
  treatment run and present in *both* 002g arms, which use the same prompt.
  It is run-to-run variance, not an effect.

Observed spread on this configuration: 30.5%, 33.0%, 33.0% (v2 prompt) and
24.4%, 31.7% (v1). **Several points of noise at n = 1** — larger than most
differences claimed so far, including 002e's 1.2pp control-treatment gap.

## 6. Where the remaining gap actually is

Divergence against the baseline (43.9%), 002g arms at 33.0% — gap ≈ ₹39,000:

| class | 002e treatment | 002g control | 002g feedback |
|---|---:|---:|---:|
| excessive_escalation | 0 | 0 | 0 |
| premature_stopping | 4 / −25,376 | 3 / −19,532 | 3 / −15,134 |
| correct_diagnosis_**suboptimal_action** | 0 | **3 / −12,277** | **3 / −16,675** |
| other (cycles spent, nothing recovered) | 6 / −22,992 | 3 / −12,117 | 3 / −12,117 |
| correct diagnosis + correct action | 23 | 24 | 24 |
| agent_better | 0 | **1 / +4,833** | **1 / +4,833** |

Escalation is gone and stays gone. What remains is **stopping too early** and
**choosing a defensible but suboptimal action** — roughly 6 payments in each
arm. Context fidelity is now 97.4–97.5%, so it is no longer a plausible cause.

## 7. Safety

`unsafe_actions_executed = 0` in both arms, as in every run to date. Denials 29
vs 28, dominated by `MAX_RETRY_LIMIT` and `RETRY_COOLDOWN` — the agent proposes
against spent budgets and the deterministic boundary absorbs it.

## 8. Limitations

- n = 1 per arm, on one dataset, one seed, one model, one API path. The observed
  noise floor (several points of recovery rate) exceeds most effects measured in
  this series.
- 15 post-failure transitions per arm is a small sample for a distribution.
- The absorption measure asks the agent to restate an outcome; the control can
  partly *infer* failure from `retries used` and a still-failed payment, so the
  control is not an information-free condition. That biases toward finding no
  effect, and no effect is what was found — the direction is honest but the test
  is weaker for it.
- B is not fully excluded. C is favoured because the agent restates the failure
  and gives a correct rationale, but "intrinsically aggressive and coincidentally
  articulate" is not ruled out by this design.

## 9. Conclusion

**Outcome feedback changed what the agent knew and nothing about what it did.**
Absorption rose from 47% to 87%; retry-after-failure, cycles burned, recovery
after failure and total revenue were unchanged, the last two to the rupee.

Hypothesis A is rejected. The evidence favours C: the agent understands the
failure and rationally judges another attempt worthwhile — a judgement the
simulator's physics support and the deterministic baseline shares.

Two experiments have now improved the agent's *information* — context grounding
(002e) and outcome feedback (002g) — and neither moved the economic outcome.
Together they are decent evidence that **the remaining gap is not an information
problem.**

## 10. Next experiment

Not more information. The residual gap sits in `premature_stopping` and
`correct_diagnosis_suboptimal_action`, about six payments per run.

**002h — stopping behaviour.** Characterise every `STOP_RECOVERY` the agent
issues where the baseline continued and recovered: what state it stopped in, what
it claimed, what remained available. Analysis first, on evidence already
exported; no new runs needed to begin.

Deferred: 002b (non-terminal escalation), 002c (API confound across seeds), and
a seed-replication run to establish the noise floor properly — which, given §5,
may now be the highest-value experiment in the queue.

Still not justified: memory, tools, MCP, higher autonomy.
