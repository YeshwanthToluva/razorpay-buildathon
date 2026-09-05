# Video script — Revenue Recovery Lab

~980 spoken words. About 6:00 at a calm pace, closer to 7:00 with the pauses while
stages land. Cut section 4's trace paragraphs for a true 5:00.

Numbers are current as of the last build. **Do not use these — they are stale and
appear in older drafts:** 24 held, 132 total, 17 rules, 288 tests, 137 proposals.

| figure | value |
|---|---|
| unsafe actions executed | **0** |
| refused outright | **108** |
| held for a human | **20** |
| total that never reached the provider | **128** |
| policy rules | **15** (+ 2 terminal outcomes) |
| content rules | **8** |
| tests | **382** (208 policy, 19 adversarial) |
| Gmail actions granted | **1** (`GMAIL_SEND_EMAIL`) |

---

## Setup

**Tabs, in order of use**

1. `localhost:3000` — landing page, scrolled to top
2. `localhost:3000/agent.html` — the map
3. `localhost:3000/rulebook.html` — top
4. Gmail, signed in as nitintoluva@gmail.com
5. `github.com/YeshwanthToluva/razorpay-buildathon`

**Before recording**

- Restart the API after any code change; hard-reload every tab (Ctrl+Shift+R)
- Live run: RISK `expired_payment_method` · CHANNEL Real email · AGENT Reasoning agent
- Try-it submissions, in this order:
  1. `RETRY_PAYMENT` + `possible_fraud` → REFUSED · FRAUD_HOLD · authority minted: no
  2. `RETRY_PAYMENT` + `transient_bank_failure` → ALLOWED · authority minted: yes

---

## 1 · The premise — 0:00–0:45

> **Screen:** landing page, top. Hero and the evidence box both visible.

This is the Revenue Recovery Lab — an engineering study built to answer one production
question. Can you give an AI agent authority over financial recovery without letting it
make an error you cannot take back?

So rather than assume the model will behave, we gave it fifty failed payments and put a
deterministic policy engine between everything it proposes and anything that actually
happens.

> **Point at the lifecycle strip on the right.**

That is the lifecycle. The agent proposes. The policy engine authorises. Only then can
the gateway dispatch it. And this is not a convention — skipping the boundary raises a
PolicyBypassError, because the object the provider requires can only be built inside the
policy module.

> **Point at the evidence box.**

Across every run: zero unsafe actions executed, and 108 proposals refused outright.
Let me show you how that works.

---

## 2 · Separation of powers — 0:45–1:45

> **Screen:** agent.html. Click `RETRY_PAYMENT` — the route lights up down to the boundary.

This is the agent map, and it is generated from the code rather than drawn.

The model is an evaluator. It takes the facts of a case and returns a proposed action —
retry the payment, send a reminder, generate a link. And it holds no database handle, no
payment provider, and no ledger. It has no way to move money on its own.

When I click retry payment, the map lights the route it takes and the eleven rules that
can refuse it. Every proposal has to survive that.

> **Click FRAUD_HOLD, then MAX_RETRY_LIMIT.**

Fraud hold — there is a fraud signal on the case, so automated recovery is frozen until a
person reviews it.

Max retry limit — the retry budget is spent. And that rule is not bookkeeping: repeated
declines damage your acceptance rate with the issuer, so every future payment from that
customer gets harder. That is a consequence the agent cannot see and the rulebook can.

Rules are evaluated in order. The first refusal wins. An AuthorizedAction is minted only
if every check passes.

---

## 3 · The engine in practice — 1:45–2:45

> **Screen:** rulebook.html. Scroll the rules in evaluation order.

Here is the rulebook — fifteen rules in a fixed top-to-bottom order, ending in two
outcomes. First refusal wins, and the list ends in default-deny, so anything nobody wrote
a rule for is refused.

> **Scroll to "Try to get something past it". Submit `RETRY_PAYMENT` + `possible_fraud`.**

And you can test it live, right here.

Take a case carrying a fraud signal, and propose the most ordinary action there is —
retry the payment. Should it?

> *(let the verdict land)*

No. Refused by fraud hold, and look at the bottom line — no authority was minted, so
there is nothing that could execute.

> **Now submit `RETRY_PAYMENT` + `transient_bank_failure`.**

Now the identical action, on a payment where the bank was simply unreachable.
Should it retry?

> *(let it land)*

Yes. Allowed, and authority minted.

Same action, opposite answers, decided by the case. The engine is not restraining the
agent — it is taking the wrong option off the table, so safety lives in deterministic
code instead of in a prompt.

---

## 4 · Live run and the trace — 2:45–3:45

> **Screen:** Live section. RISK `expired_payment_method` → Detect & recover.

Now a real one. This case is an expired card.

It finds the money at risk, the model decides, the policy engine rules on it.

And watch what it is not allowed to do. It cannot retry the card, because the card has
expired — re-presenting it would fail and spend the retry budget for nothing. So the only
move left is to give the customer another way to pay. The agent did not have to work that
out. The rulebook already had.

> **Stage 4 lands. Then Gmail — show the message.**

Then the agent writes the message for this specific case, and what it wrote passes a
second boundary — a content policy — before it can be sent. And there it is, in a real
inbox, with a working payment link.

> **Open EXECUTION TRACE. Expand the model span, then the tool span.**

All of it is traced. Here is the model call — what it was shown and what it returned.
Here is the policy call. And here is the tool call: the mail goes out through Composio's
Gmail MCP tool.

A Gmail toolkit can read mail, list contacts, manage drafts. This agent was granted one
action — send. And who it may send to is decided by the policy engine, not by the mail
tool.

---

## 5 · The takeaway — 3:45–4:30

> **Screen:** Replays. Click the filters as you name them. Then the Safety section.

Every case is replayable. The ones the policy engine stopped. The ones it escalated to a
human. The ones that closed without recovering anything — we publish those too.

> **Safety section headline on screen.**

Which is the thesis, and it is written at the top of this section. The agent was allowed
to be wrong. It was never allowed to move money however it wanted.

Of everything it proposed and did not get: 108 refused outright, 20 held for an approval
that at this autonomy level nobody can give. 128 actions that never reached the payment
provider.

And zero that did.

In production you cannot rely on the model to police itself. Deterministic authorisation
is what turns an unpredictable proposal into a workflow you can actually deploy — and in
finance, that is the whole difference.

---

## 6 · Evidence — 4:30–5:00

> **Screen:** Evidence section, then the GitHub repo.

None of this is mock data. Every figure comes from committed runs — the hash-pinned
dataset, the append-only ledger, every execution trace, every refusal.

382 tests, 208 of them on the policy engine, and 19 where a deliberately hostile agent
tries to get something dangerous past the boundary. It does not get through.

*(optional)* We also published the result that went against us — a fixed rule set
recovered more revenue than the agent did — along with the two experiments that failed to
explain it.

Clone it, run the same scripts, and check the bounds yourself.

> *(beat)*

An agent that recovers revenue is not the hard part. An agent you would let near revenue
is. Thank you.

---

## Backup, if asked

**Where the 108 refusals came from.** Retry cooldown 38, retry budget spent 33, recovery
window closed 19, action impossible for the case 8, contact budget 5, fraud hold 5. 65 of
them were safety rules rather than pacing — don't claim all 108 were safety.

**Is the data real.** All synthetic. No real customer or payment data, and no real money
moves — the payment rail is a simulator. The email is genuinely sent.

**Who can it email.** An allowlist holding one address, enforced in the policy engine, not
in the mail tool. An empty allowlist denies everyone, so a misconfigured deploy sends
nothing rather than everything.

**Autonomy level.** Level 2 — model proposes, deterministic policy disposes, no human in
the loop. Escalation is therefore terminal, which is why over-escalating cost the agent
revenue. We measured it at 94.6% of the gap.

**Why the agent lost to the rule set.** It escalates more, and escalation closes a case at
zero. We improved its context, then its outcome feedback. Neither moved revenue — the
second identical to the rupee. So the gap is not an information problem.

**A second refusal example.** Checkout abandonment refuses `RETRY_PAYMENT` with
`RISK_TYPE_PRECONDITION`: nothing was ever authorised, so there is no instrument to
re-present.
