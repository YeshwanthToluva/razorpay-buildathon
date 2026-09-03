# 0002 — The model is an experiment dimension, not a constant

**Status:** accepted, Sprint 1

## Context

Sprint 1 was specified around one model. In practice five were tried in a single
session, on three different gateways, and they behaved so differently that
treating "the LLM" as a fixed component would have made every result
unreproducible.

## Decision

LLM credentials are grouped into named profiles in `.env`:

    AFIN_LLM_<NAME>_{API_KEY,BASE_URL,MODEL,API_STYLE,REASONING_EFFORT}

`--profile <name>` selects one. Everything else — dataset, seed, policy,
prompt version, simulator — is held constant, and the run row records the
reasoner, model, prompt version, policy fingerprint, dataset version, seed and
model configuration.

## Consequences and observed facts

- **API style is load-bearing.** Agent Framework's `OpenAIChatClient` targets
  the Responses API; the aiplanet gateway serves only chat completions and
  answers `404 provider_not_found`. The same model, prompt, seed and policy gave
  materially different recovery numbers across the two APIs, so `api_style` is
  a recorded profile setting rather than an implementation detail.
- **Reasoning effort is transmitted**, because the options object is a plain
  dict and extra keys reach the request body. This was verified before being
  relied upon; a setting that silently does nothing is worse than no setting.
  It defaults to `low`: choosing a recovery action is a small structured
  decision, and high effort buys latency across 50 payments x 4 cycles.
- **Not every model can hold the contract.** Some ignore the response schema,
  some fill every field with commentary about the prompt, some time out. These
  are recorded as `PROPOSAL_INVALID` and in the experiment record, not silently
  retried away.
- **Rate limits are not results.** Schema violations are never retried, because
  resampling until the output parses would flatter a model that cannot follow
  the schema. Transient faults (429, 5xx) are retried with backoff, and a run
  whose provider-error rate exceeds 10% is stamped NOT VALID AS AN EXPERIMENT.
