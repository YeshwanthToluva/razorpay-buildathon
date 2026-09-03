from __future__ import annotations

import pytest

from afin.domain.enums import ActionType, ExecutionResult, FailureCategory
from afin.policy.authorization import authorize
from afin.simulator.razorpay_sim import RazorpaySimulator, _draw
from tests.conftest import NOW, make_payment
from tests.policy.conftest import propose, request


def run(action=ActionType.RETRY_PAYMENT, seed=1, payment=None, **kw):
    payment = payment if payment is not None else make_payment(**kw)
    decision, auth = authorize(
        request(
            proposal=propose(action=action, scheduled_delay_hours=24, payment_id=payment.id),
            payment=payment,
        )
    )
    assert auth is not None, f"test setup: policy denied {action} ({decision.reason})"
    return RazorpaySimulator(seed=seed).execute(auth, payment, NOW)


def test_outcomes_are_deterministic_for_a_fixed_seed():
    first = run(seed=7)
    for _ in range(50):
        assert run(seed=7) == first


def test_a_different_seed_changes_outcomes_somewhere():
    outcomes = {
        RazorpaySimulator(seed=s).execute(
            authorize(request())[1], make_payment(), NOW
        ).result
        for s in range(40)
    }
    assert len(outcomes) > 1


def test_a_payments_outcome_does_not_depend_on_other_payments():
    """The draw is keyed, not streamed, so isolated re-runs reproduce."""
    sim = RazorpaySimulator(seed=3)
    target = make_payment(id="pay_0042", customer_id="cust_0042")
    _, auth = authorize(
        request(proposal=propose(payment_id="pay_0042"), payment=target,
                customer=__import__("tests.conftest", fromlist=["x"]).make_customer(id="cust_0042"))
    )
    alone = sim.execute(auth, target, NOW)

    # Process many unrelated payments first; the target's result must not move.
    for i in range(30):
        p = make_payment(id=f"pay_{i:04d}", customer_id=f"cust_{i:04d}")
        _, a = authorize(
            request(
                proposal=propose(payment_id=p.id),
                payment=p,
                customer=__import__("tests.conftest", fromlist=["x"]).make_customer(
                    id=p.customer_id
                ),
            )
        )
        sim.execute(a, p, NOW)

    assert sim.execute(auth, target, NOW) == alone


def test_successful_capture_returns_the_full_amount():
    """Partial capture is not modelled; a success recovers the invoice."""
    for seed in range(30):
        out = run(seed=seed, amount_minor=777_700)
        if out.result is ExecutionResult.SUCCESS:
            assert out.amount_recovered_minor == 777_700
            return
    pytest.fail("no success in 30 seeds; success path unreachable")


def test_failure_recovers_nothing_and_reports_a_code():
    for seed in range(60):
        out = run(seed=seed, failure_category=FailureCategory.DO_NOT_HONOR)
        if out.result is ExecutionResult.FAILURE:
            assert out.amount_recovered_minor == 0
            assert out.failure_code == "GW_TIMEOUT" or out.failure_code
            return
    pytest.fail("no failure in 60 seeds")


def test_transient_failures_retry_better_than_hard_declines():
    """The simulator must encode that retrying is category-sensitive."""
    def rate(category, action=ActionType.RETRY_PAYMENT, n=300):
        wins = 0
        for i in range(n):
            p = make_payment(id=f"pay_{i:04d}", customer_id=f"cust_{i:04d}",
                             failure_category=category)
            _, auth = authorize(
                request(
                    proposal=propose(action=action, payment_id=p.id),
                    payment=p,
                    customer=__import__("tests.conftest", fromlist=["x"]).make_customer(
                        id=p.customer_id
                    ),
                )
            )
            if auth is None:
                continue
            if RazorpaySimulator(seed=11).execute(auth, p, NOW).result is ExecutionResult.SUCCESS:
                wins += 1
        return wins / n

    assert rate(FailureCategory.BANK_UNAVAILABLE) > rate(FailureCategory.DO_NOT_HONOR)
    assert rate(FailureCategory.PROCESSOR_ERROR) > rate(FailureCategory.INSUFFICIENT_FUNDS)


def test_stop_and_escalate_move_no_money():
    for action in (ActionType.STOP_RECOVERY, ActionType.REQUEST_HUMAN_REVIEW):
        out = run(action=action)
        assert out.amount_recovered_minor == 0
        assert out.result is ExecutionResult.SUCCESS


def test_authority_for_another_payment_is_rejected():
    """Authority is minted per payment and must not be reusable elsewhere."""
    _, auth = authorize(request())
    other = make_payment(id="pay_0002", customer_id="cust_0002")
    out = RazorpaySimulator().execute(auth, other, NOW)

    assert out.result is ExecutionResult.REJECTED
    assert out.failure_code == "AUTHORIZATION_SCOPE_MISMATCH"
    assert out.amount_recovered_minor == 0


def test_draw_is_uniform_enough_to_not_bias_results():
    values = [_draw(1, f"pay_{i}", "RETRY_PAYMENT", 0) for i in range(4000)]
    assert all(0.0 <= v < 1.0 for v in values)
    assert 0.47 < sum(values) / len(values) < 0.53
    assert len(set(values)) == len(values), "draw collisions would correlate payments"
