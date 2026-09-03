"""The policy engine must be pure: no clock, no randomness, no I/O, no state."""

from __future__ import annotations

import ast
import pathlib

from afin.domain.enums import ActionType
from afin.policy.config import DEFAULT_POLICY_CONFIG, PolicyConfig
from afin.policy.engine import evaluate
from tests.policy.conftest import propose, request

_ENGINE = pathlib.Path(__file__).parents[2] / "src" / "afin" / "policy" / "engine.py"

#: Names whose presence in the engine would make it non-deterministic.
_FORBIDDEN = {
    "now", "utcnow", "today", "time", "monotonic",
    "random", "choice", "randint", "uniform", "shuffle",
    "open", "connect", "execute", "get", "post", "request",
}


def test_engine_source_contains_no_impure_calls():
    tree = ast.parse(_ENGINE.read_text())
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                called.add(fn.attr)
            elif isinstance(fn, ast.Name):
                called.add(fn.id)
    leaked = called & _FORBIDDEN
    assert not leaked, f"policy engine calls impure names: {sorted(leaked)}"


def test_engine_imports_nothing_from_the_agent_or_a_provider():
    tree = ast.parse(_ENGINE.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)

    banned = {m for m in modules if any(
        part in m for part in ("agent", "simulator", "openai", "db", "audit", "sqlalchemy")
    )}
    assert not banned, f"policy engine must stay independent of the LLM stack: {banned}"


def test_identical_input_yields_identical_output():
    r = request(retry_count=2)
    first = evaluate(r)
    for _ in range(500):
        assert evaluate(r) == first


def test_evaluation_does_not_mutate_its_inputs():
    r = request(retry_count=1, contact_count=1)
    before = (r.payment, r.customer, r.proposal, r.config)
    evaluate(r)

    assert (r.payment, r.customer, r.proposal, r.config) == before
    assert r.payment.retry_count == 1
    assert r.payment.contact_count == 1


def test_engine_is_total_over_the_action_space():
    """Every action, valid or invented, produces a decision rather than raising."""
    for action in list(ActionType) + ["", "WIRE_FUNDS", "null", "  "]:
        d = evaluate(request(proposal=propose(action=action)))
        assert isinstance(d.allowed, bool)


def test_config_is_fingerprinted_for_run_reproducibility():
    a, b = PolicyConfig(), PolicyConfig()
    assert a.fingerprint() == b.fingerprint()
    assert PolicyConfig(max_retries=5).fingerprint() != DEFAULT_POLICY_CONFIG.fingerprint()
