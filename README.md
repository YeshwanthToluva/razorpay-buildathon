# Agent Influence in Finance

Experimental laboratory studying how an AI agent's autonomy, memory, and tool
access affect its ability to recover at-risk revenue — and whether deterministic
controls can reliably keep it inside safe boundaries.

**Synthetic data only. No real financial transactions.**

## Central architectural invariant

    MEMORY         information the agent may use
    POLICY         what the agent is allowed to do
    TOOLS          capabilities the agent may request
    AUTHORIZATION  whether a requested capability may execute
    AUDIT          what actually happened

The LLM proposes. It never executes. Financial state is mutated only by an
outcome obtained from the provider, which is reachable only through an
`AuthorizedAction` that only the policy engine can mint.

    unsafe actions proposed  >= 0   (an experimental observation)
    unsafe actions executed  == 0   (an invariant; a run that breaks it fails)

## Sprint 1 scope — Autonomy Level 2

Synthetic dataset -> agent -> structured proposal -> deterministic policy ->
simulator -> audit ledger -> metrics.

## Setup

    cp .env.example .env      # fill in credentials
    createdb agent_finance
