"""The endpoints the console actually calls.

/api/policy/evaluate is what the site's blocked-attempt modal and the rulebook's
"try it" form re-run against the live engine, so a 500 here does not look like a
broken endpoint -- it looks like the API is down, and the demo quietly loses the
feature that proves the boundary is real. It broke exactly that way once, when
dead code was removed and the name it defined was left in use.
"""

from __future__ import annotations

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from apps.api.main import app

    return TestClient(app)


def test_health_reports_the_policy_it_is_running(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["policy"] and body["fingerprint"]


def test_an_invented_action_is_refused_and_mints_nothing(client):
    r = client.post("/api/policy/evaluate",
                    json={"action": "WIRE_FUNDS", "scenario": "transient_bank_failure"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["in_action_space"] is False
    assert body["allowed"] is False
    assert body["rule"] == "UNSUPPORTED_ACTION"
    assert body["authority_minted"] is False


def test_a_real_action_on_a_barred_case_is_refused_by_name(client):
    body = client.post("/api/policy/evaluate",
                       json={"action": "RETRY_PAYMENT", "scenario": "possible_fraud"}).json()
    assert body["in_action_space"] is True
    assert body["allowed"] is False
    assert body["rule"] == "FRAUD_HOLD"
    assert body["authority_minted"] is False
    # the trace is the point: it shows which rules were consulted to get there
    assert body["rules_evaluated"][0] == "UNSUPPORTED_ACTION"


def test_a_permitted_action_mints_authority(client):
    body = client.post("/api/policy/evaluate",
                       json={"action": "RETRY_PAYMENT",
                             "scenario": "transient_bank_failure"}).json()
    assert body["allowed"] is True
    assert body["authority_minted"] is True


@pytest.mark.parametrize("path", ["/api/scenarios", "/api/policy/rules", "/api/mechanics"])
def test_the_pages_data_endpoints_answer(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path}: {r.text[:200]}"
    assert r.json()
