"""The mail tool may do exactly one thing."""

from __future__ import annotations

import pytest

from afin.tools.notify import (
    ALLOWED_TOOL_ACTIONS,
    ComposioGmail,
    LocalOutbox,
    Message,
    ToolScopeError,
)


def test_only_sending_is_permitted():
    assert ALLOWED_TOOL_ACTIONS == {"GMAIL_SEND_EMAIL"}


@pytest.mark.parametrize(
    "action",
    ["GMAIL_FETCH_EMAILS", "GMAIL_LIST_DRAFTS", "GMAIL_DELETE_MESSAGE",
     "GMAIL_GET_CONTACTS", "GMAIL_MOVE_TO_TRASH"],
)
def test_every_other_gmail_capability_is_refused(action):
    """A toolkit that can read mail is a far larger capability than this needs."""
    with pytest.raises(ToolScopeError):
        ComposioGmail(api_key="k", user_id="u", action=action)


def test_the_default_transport_reaches_nobody(tmp_path, monkeypatch):
    """Without a configured transport, messages go to disk, not to a person."""
    monkeypatch.chdir(tmp_path)
    box = LocalOutbox()
    res = box.send(Message(to="someone@example.com", subject="s", body_html="<p>b</p>"))
    assert res.delivered is True
    assert res.channel == "local-outbox"
    assert (tmp_path / "data" / "outbox").exists()
    assert len(box.sent) == 1


def test_a_redirect_is_recorded_rather_than_hidden():
    m = Message(to="me@example.com", subject="s", body_html="<p>b</p>",
                redirected_from="cust_0015@synthetic.invalid")
    assert m.redirected_from == "cust_0015@synthetic.invalid"
