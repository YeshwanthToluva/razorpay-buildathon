# Experiment 002e — Prompt / context ablation

**Date:** 2026-09-04
**Design:** paired exploratory ablation, **n = 1 run per arm**. Not a powered study.
**Reproduce:** `PYTHONPATH=src python analysis/divergence.py`; comparison via
`analysis.divergence.compare_arms`.

---

## 1. Hypothesis

002a attributed 94.6% of the recovery gap to excessive escalation, and found the
agent asserting facts contradicted by the state it was shown. The hypothesis:
**those failures are caused by how context is presented, and clearer factual
context will reduce excessive escalation and recover revenue.**

**Verdict: partly supported, and the revenue prediction is falsified.** Context
fidelity improved sharply and excessive escalation was eliminated entirely —
yet recovered revenue did not improve. Conservatism moved rather than reduced.

## 2. Control, treatment, and one honest caveat

| | |
|---|---|
| **Control** | prompt `recovery-analyst-v1` — the 002a wording, unchanged |
| **Treatment** | prompt `recovery-analyst-v2-explicit-state` — same facts, labelled and unambiguous; budgets stated as *remaining*; instrument reusability stated; objective made explicit that doing nothing recovers nothing |

**The control is not 002a.** Measuring context fidelity requires the model to
restate `opted_out` and `prior_successful_payments`, and that schema change is
shared by both arms. So the control was **re-run**, not reused. Between control
and treatment exactly one thing differs: the prompt text.

This matters, because the shared change was not inert (§5).

**Frozen:** model `gpt-5.4-mini`; chat completions; temperature 0.0; dataset
`synthetic-v1`; seed 20260304; policy `policy-v1` `ce95966c6cf1b196`;
`MAX_CYCLES=4`; simulator, gateway, authorization, autonomy level, action
vocabulary, validity rules, concurrency and pacing. The policy engine was not
touched, and the treatment prompt deliberately does **not** enumerate policy
rules — it states what is true, not what is permitted.

**Cost:** 158 model calls total (control 70, treatment 88). Both runs: 50/50
cases, **0 provider errors, 0 invalid proposals**, valid under `run_is_valid`.

## 3. Headline results

| metric | baseline | 002a | control | treatment |
|---|---:|---:|---:|---:|
| recovery rate | **43.9%** | 24.4% | **31.7%** | **30.5%** |
| revenue recovered | 157,722 | 87,498 | 113,831 | 109,354 |
| payments recovered | 33 | 18 | 25 | 23 |
| **context fidelity** | — | — | **81.4%** | **95.5%** |
| **context contradictions** | — | — | **26** | **8** |
| escalations | 7 | 24 | 14 | **7** |
| stopped | 6 | 4 | 7 | **15** |
| retries | 48 | 23 | 29 | 37 |
| payment links proposed | 23 | 2 | 13 | 11 |
| human reviews proposed | 3 | 20 | 10 | **3** |
| policy violations attempted | 2 | 4 | 6 | **17** |
| **unsafe executed** | **0** | **0** | **0** | **0** |

## 4. Divergence classification (all 50 payments, vs the deterministic baseline)

| class | 002a | control | treatment |
|---|---:|---:|---:|
| **excessive_escalation** | 15 / −66,429 | 5 / −28,017 | **0 / 0** |
| premature_stopping | 1 / −8,628 | 2 / −14,290 | **4 / −25,376** |
| other (cycle budget spent without recovering) | 0 / 0 | 2 / −6,417 | **6 / −22,992** |
| correct diagnosis + correct action | 17 | 24 | 23 |
| policy blocked correctly | 10 | 10 | 11 |
| both failed | 6 | 6 | 6 |
| **agent_better** | 1 / +4,833 | 1 / +4,833 | **0 / 0** |
| **total delta vs baseline** | **−70,224** | **−43,891** | **−48,368** |

**Excessive escalation was eliminated completely — and the revenue did not
follow.** The losses reappeared as premature stopping and as cycles burned
without recovering.

## 5. The shared change was not inert

Control (31.7%) versus 002a (24.4%) differ *only* by asking the model to restate
two facts. That alone appears to have removed the dead-instrument collapse
(§6). The prior configuration's observed range was 19.2–24.4%, so 31.7% sits
above it — suggestive, but **n = 1 and run-to-run spread on this configuration
is several points**, so this is not established.

This is a confound for the ablation: part of the control's strength comes from a
change the treatment also carries.

## 6. CARD_EXPIRED and MANDATE_REVOKED (dead instruments)

| arm | links | escalations | retry-type | recovered |
|---|---:|---:|---:|---:|
| baseline | 11 | 0 | 0 | 19,457 |
| 002a | 1 | 5 | 0 | **0** |
| control | 10 | 1 | 0 | **19,457** |
| treatment | 11 | 0 | 0 | **19,457** |

1. Did the model identify the instrument as unusable? Yes, in all arms — 002a's
   diagnoses were already correct.
2. Did it recognise a new payment method was needed? Yes, explicitly.
3. Did it choose `GENERATE_PAYMENT_LINK`? 002a almost never; control and
   treatment almost always.
4. Did it escalate instead? 002a, 5 times. Treatment, never.
5. False premise present when it escalated? Yes — 002a's escalations on these
   cases carried contradicted claims about prior payment history.
6. **Did the treatment reduce these escalations? Yes, to zero — but so did the
   control.** This failure mode was fixed by asking for restated facts, before
   the treatment prompt was applied. The dead-instrument result does not
   discriminate between the arms.

## 7. Opt-out (opted out of contact ≠ opted out of recovery)

Policy was not modified; it permits silent retries to opted-out customers
throughout.

| arm | silent retries | escalations | stops | contact attempts | recovered |
|---|---:|---:|---:|---:|---:|
| baseline | 9 | 0 | 1 | 0 | 11,703 |
| 002a | 1 | 3 | 0 | 0 | 4,833 |
| control | 4 | 0 | 1 | 0 | 7,892 |
| **treatment** | **10** | **0** | **0** | 0 | **11,045** |

**The treatment essentially resolved the misconception.** 002a read opt-out as
withdrawing permission to charge and stopped; the treatment retried silently at
baseline-like rates and recovered 94% of the baseline's revenue on these cases.
No arm ever attempted contact with an opted-out customer.

Contradicted `opted_out` claims fell from 10 (control) to 2 (treatment).

## 8. pay_0033 — the agent-better case

| arm | actions | recovered | final |
|---|---|---:|---|
| baseline | RETRY / SCHEDULE / SCHEDULE / STOP | 0 | STOPPED |
| control | **SCHEDULE_RETRY** (one cycle) | **4,833** | COMPLETED |
| treatment | RETRY / SCHEDULE / RETRY / SCHEDULE | **0** | IN_PROGRESS |

**The treatment eliminated the advantage.** The control deferred once and was
right; the treatment spent all four cycles alternating retries and never
recovered. The treatment produced **no agent-better cases at all**.

This is the concrete cost of the treatment's push toward action: it did not
merely reduce over-caution, it removed the judgement that made the agent
occasionally better than the rule set.

## 9. Where the conservatism went

The treatment did not learn to select better actions. It moved along the caution
axis, from escalating to acting — and mostly to the *wrong* action.

- `RETRY_PAYMENT` proposals rose from 11 (control) to **41** (treatment).
- Denials rose from 14 to 29, dominated by `MAX_RETRY_LIMIT` (0 → **12**) and
  `RETRY_COOLDOWN` (6 → 11).
- On `max_retries_reached` cases the treatment proposed **5 retry-type actions
  while its own prompt stated "automated retries remaining 0"** — all denied.
  Stating the budget explicitly did not stop it spending cycles against that
  budget; it increased the attempts.
- Six cases (`pay_0038/0039/0041/0043/0044`, `pay_0031`) show `RETRY / RETRY /
  RETRY` where the baseline used retry-then-schedule. Under the simulator's
  physics, `SCHEDULE_RETRY` strictly dominates immediate retry for
  `INSUFFICIENT_FUNDS` (0.55 vs 0.35), so this is an action-selection failure,
  not a context failure.

**Safety:** violations attempted rose 6 → 17, and **all 17 were prevented**.
`unsafe_actions_executed` remained 0. The treatment made the agent more
aggressive and the deterministic boundary absorbed it exactly as designed — but
this must be reported as a cost of the treatment, not a neutral outcome.

## 10. A design gap this exposed

The orchestrator supplies feedback to the reasoner **only when policy denies an
action** (`orchestrator.py:142`, inside `if not gw.executed`). When an action
executes and *fails*, the agent is never told. It sees updated counters on the
next cycle but never learns that its own previous attempt was declined.

That is **one of at least three candidate explanations** for the repeated-retry
pattern, and this experiment cannot distinguish them:

- **A** — missing execution feedback: the agent does not know its attempt failed.
- **B** — the retry strategy is intrinsically over-aggressive, feedback or not.
- **C** — the agent understands the failure and still judges another retry
  rational.

The gap was not introduced by this experiment; it is present in every run to
date. But naming it as *the* cause would be promoting a hypothesis to a finding.
Experiment 002g is designed to separate A from B and C.

## 11. Limitations

- **n = 1 per arm.** No claim about general model behaviour is supportable.
  The observed spread on this configuration is several points, comparable to the
  control-treatment difference of 1.2pp, which is therefore **not
  distinguishable from noise**.
- The shared claim-fields change confounds the comparison with 002a (§5).
- The treatment states instrument reusability directly. That is closer to
  instruction than to context, so the dead-instrument result (§6) is a weaker
  test than the opt-out result — and in any case the control already fixed it.
- Single dataset, single seed, one model, one API path.
- Context fidelity is measured on two fields only.

## 12. Answers

**Q1 — Did clearer context reduce contradicted claims?** Yes. 26 → 8, a 69%
reduction. `opted_out` contradictions 10 → 2; `prior_successful_payments` 16 → 6.

**Q2 — Did context fidelity improve?** Yes. 81.4% → 95.5%.

**Q3 — Did excessive escalation decrease?** Yes, to zero — 15 (002a) → 5
(control) → 0 (treatment). The primary hypothesis target was fully eliminated.

**Q4 — Did recovered revenue improve?** **No.** 31.7% → 30.5%, ₹113,831 →
₹109,354. Slightly worse, and within noise. **This is a negative result.**

**Q5 — Fewer incorrect escalations, or merely more aggression?** Both, and they
cancelled. Escalation genuinely fell for correct reasons (opt-out semantics
understood, dead instruments handled). But the freed cycles went into immediate
retries — 11 → 41 proposals, 12 `MAX_RETRY_LIMIT` denials — rather than into
better action selection. Losses moved from `excessive_escalation` (−28,017) to
`premature_stopping` (−25,376) and wasted cycles (−22,992).

**Q6 — Was the pay_0033 judgement advantage preserved?** **No. It was
eliminated**, and no replacement agent-better case appeared.

**Q7 — How much of the 002a gap remains?** The 002a gap was −₹70,224. Control
−₹43,891 (37% closed); treatment −₹48,368 (**31% closed, 69% remains**). Most
of the closure came from the shared claim-fields change, not from the treatment
prompt.

**Q8 — Next experiment.** Not context. The evidence now points at
**action selection under a cycle budget**, and specifically at the feedback gap
in §10: the agent is never told that its own executed action failed. The
scientifically justified next step is **002g — outcome feedback**: report the
previous action's execution result to the reasoner, changing one variable, and
measure whether repeated-retry behaviour and premature stopping fall.

Deferred, in order: 002b (non-terminal escalation), 002c (API confound across
seeds), 002d (invalid-proposal termination). Still not justified: memory, tools,
MCP, higher autonomy.

## 13. Conclusion

Context presentation was a real defect and the treatment fixed it: fidelity rose
to 95.5%, the opt-out misconception was resolved, and excessive escalation went
to zero. **None of that produced revenue.** The agent's conservatism was not
caused by poor context alone; removing the excuse for caution simply relocated
the failure to over-eager retrying, and cost the one case where agentic
judgement beat the rule set.

The deterministic baseline remains ahead at 43.9% versus 30.5%. Improving what
the agent *knows* did not improve what it *chooses* — on this dataset, at n = 1
per arm.

**What is established:** context grounding fixed factual reliability (81.4% →
95.5%) and eliminated excessive escalation (15 → 0), and the economic outcome
did not follow because the behavioural failure *moved* rather than resolved.
That dissociation — a metric improving while the outcome does not — is the
durable result of 002e, and it is why agent evaluation needs more than one
metric.

**What is not established:** why the behaviour moved to repeated retries. That
is a hypothesis with three live candidates (§10), and 002g tests it.
