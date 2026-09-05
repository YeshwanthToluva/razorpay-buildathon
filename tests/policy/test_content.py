"""What the agent may say to a customer.

The action space is closed, so an invented action is caught structurally. Prose
is open, so this is where a model writing about someone's money gets checked.
"""

from __future__ import annotations

import pytest

from afin.policy.content import MAX_CHARS, ContentRule, evaluate_message

OK = ("We could not collect Rs 5,518.00 for invoice inv_0015 because the card on file "
      "has expired. You can settle it with another method using the link below. "
      "Reply to this email if you have any questions.")


def check(text, amount=551800, invoice="inv_0015"):
    return evaluate_message(text, amount_minor=amount, invoice_id=invoice)


def test_a_clean_message_is_permitted():
    d = check(OK)
    assert d.allowed is True
    assert d.rule is ContentRule.PERMITTED


@pytest.mark.parametrize("phrase", [
    "We will refund this in full.",
    "We can waive the fee for you.",
    "A credit has been applied to your account.",
    "We guarantee this will not happen again.",
    "We promise to sort this out.",
])
def test_the_agent_cannot_commit_to_what_it_has_no_authority_to_offer(phrase):
    d = check(OK + " " + phrase)
    assert d.allowed is False
    assert d.rule is ContentRule.UNAUTHORISED_COMMITMENT


def test_declining_to_waive_is_not_an_offer_to_waive():
    """Phrase matching must not fire on a refusal."""
    d = check(OK + " Unfortunately we cannot waive this charge.")
    assert d.allowed is True


@pytest.mark.parametrize("phrase", [
    "Pay immediately or we will escalate.",
    "We will begin legal action.",
    "This will affect your credit score.",
    "This is your final notice.",
    "We will suspend your account.",
])
def test_collections_pressure_is_refused(phrase):
    d = check(OK + " " + phrase)
    assert d.allowed is False
    assert d.rule is ContentRule.PRESSURE_OR_THREAT


@pytest.mark.parametrize("phrase", [
    "Please reply with your card number and CVV.",
    "Confirm your OTP to proceed.",
    "Send us your PIN so we can retry.",
])
def test_asking_for_credentials_is_refused(phrase):
    """Indistinguishable from phishing to the person receiving it."""
    d = check(OK + " " + phrase)
    assert d.allowed is False
    assert d.rule is ContentRule.ASKS_FOR_CREDENTIALS


def test_an_amount_that_is_not_the_amount_owed_is_refused():
    d = check("We could not collect Rs 9,999.00 for invoice inv_0015.")
    assert d.allowed is False
    assert d.rule is ContentRule.FABRICATED_AMOUNT


def test_the_correct_amount_in_any_common_format_is_accepted():
    for form in ("Rs 5,518.00", "Rs5518", "₹5,518.00"):
        d = check(f"Invoice inv_0015 has {form} outstanding. Reply with questions.")
        assert d.allowed is True, form


def test_a_message_that_names_no_invoice_is_refused():
    d = check("We could not collect Rs 5,518.00. Please pay.")
    assert d.allowed is False
    assert d.rule is ContentRule.MISSING_REQUIRED_FACT


def test_a_message_that_states_no_amount_is_refused():
    d = check("Something went wrong with invoice inv_0015. Please pay.")
    assert d.allowed is False
    assert d.rule is ContentRule.MISSING_REQUIRED_FACT


@pytest.mark.parametrize("leak", [
    "The payment failed with DO_NOT_HONOR.",
    "This was a CARD_EXPIRED failure.",
    "We will SCHEDULE_RETRY shortly.",
    "You are in customer segment RETAIL.",
    "Your risk_type is PAYMENT_FAILURE.",
    "Your lifetime_payments count is 9.",
])
def test_internal_vocabulary_never_reaches_a_customer(leak):
    """Observed in practice: the model put field names into a customer email
    even after being told not to. The rule is the backstop, not the prompt."""
    d = check(OK + " " + leak)
    assert d.allowed is False
    assert d.rule is ContentRule.LEAKS_INTERNALS


def test_an_overlong_message_is_refused():
    d = check("Invoice inv_0015 Rs 5,518.00. " + "padding " * 400)
    assert d.allowed is False
    assert d.rule is ContentRule.OVERLONG
    assert len("padding " * 400) > MAX_CHARS


def test_the_decision_records_which_rules_were_consulted():
    d = check(OK)
    assert ContentRule.UNAUTHORISED_COMMITMENT in d.evaluated
    assert ContentRule.LEAKS_INTERNALS in d.evaluated
    assert d.evaluated[-1] is ContentRule.PERMITTED


def test_evaluation_is_total_over_odd_input():
    for text in ("", "   ", "🙂", "<script>alert(1)</script>"):
        assert isinstance(check(text).allowed, bool)



# --- the message has to be true about this case ---------------------------

ABANDONED = ("We could not collect Rs 5,518.00 for invoice inv_0015. "
             "Your card on file appears to be unavailable for this charge. "
             "Reply if you have questions.")


def test_a_message_cannot_invent_a_card_that_never_existed():
    """Observed in practice: asked to write about an abandoned checkout, the
    model told the customer their card on file was unavailable. There was no
    card -- nothing was ever authorised."""
    d = evaluate_message(ABANDONED, amount_minor=551800, invoice_id="inv_0015",
                         instrument_on_file=False)
    assert d.allowed is False
    assert d.rule is ContentRule.CONTRADICTS_THE_CASE


def test_the_same_sentence_is_fine_when_a_card_does_exist():
    """The rule is about truth on this case, not a banned phrase list."""
    d = evaluate_message(ABANDONED, amount_minor=551800, invoice_id="inv_0015",
                         instrument_on_file=True)
    assert d.allowed is True


@pytest.mark.parametrize("claim", [
    "The payment attempt was declined.",
    "Your bank declined the charge.",
    "We tried to charge your saved payment method.",
])
def test_no_charge_may_be_described_when_none_was_attempted(claim):
    d = evaluate_message(
        f"Invoice inv_0015, Rs 5,518.00 outstanding. {claim} Reply with questions.",
        amount_minor=551800, invoice_id="inv_0015", instrument_on_file=False)
    assert d.allowed is False
    assert d.rule is ContentRule.CONTRADICTS_THE_CASE
