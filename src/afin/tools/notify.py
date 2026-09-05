"""Outbound communication, behind the Action Gateway.

Two rules govern everything here:

1. This module is only reachable with an AuthorizedAction. It cannot be called
   on a proposal, so the policy engine has already decided the recipient is
   allowed before any transport is touched.
2. Exactly one remote action is permitted. A toolkit that can read mail, manage
   drafts or list contacts is a much larger capability than this agent needs,
   so the wrapper holds a single-action allowlist and refuses anything else --
   including a future caller that asks politely.

Composio is reached over REST rather than through its SDK, because installing
the package upgrades openai 2.24 -> 3.8 in the shared venv, underneath both the
platform and our own reasoner.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

#: The only remote action this system may ever invoke.
ALLOWED_TOOL_ACTIONS: frozenset[str] = frozenset({"GMAIL_SEND_EMAIL"})


class ToolScopeError(RuntimeError):
    """Raised on an attempt to invoke an action outside the permitted set."""


@dataclass(frozen=True, slots=True)
class Message:
    to: str
    subject: str
    body_html: str
    #: Set when the configured demo recipient replaced the customer's own
    #: address. Recorded so the ledger never implies we mailed the customer.
    redirected_from: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: bool
    channel: str
    detail: str
    provider_ref: str | None = None


class Notifier(Protocol):
    name: str

    def send(self, message: Message) -> DeliveryResult: ...


@dataclass
class LocalOutbox:
    """Writes the message to disk instead of sending it.

    The default, so the system has no way to reach anyone until a real
    transport is configured deliberately.
    """

    name: str = "local-outbox"
    sent: list[Message] = field(default_factory=list)

    def send(self, message: Message) -> DeliveryResult:
        import json
        import pathlib
        import time

        out = pathlib.Path("data/outbox")
        out.mkdir(parents=True, exist_ok=True)
        ref = f"local_{int(time.time() * 1000)}"
        (out / f"{ref}.json").write_text(
            json.dumps(
                {"to": message.to, "subject": message.subject,
                 "body_html": message.body_html,
                 "redirected_from": message.redirected_from},
                indent=2,
            )
        )
        self.sent.append(message)
        return DeliveryResult(True, self.name, f"written to data/outbox/{ref}.json", ref)


@dataclass
class ComposioGmail:
    """Sends through Composio's Gmail toolkit. Send-only, by construction."""

    api_key: str
    user_id: str
    base_url: str = "https://backend.composio.dev/api/v3"
    action: str = "GMAIL_SEND_EMAIL"
    timeout: float = 30.0
    name: str = "composio-gmail"

    def __post_init__(self) -> None:
        if self.action not in ALLOWED_TOOL_ACTIONS:
            raise ToolScopeError(
                f"{self.action} is outside the permitted tool actions "
                f"{sorted(ALLOWED_TOOL_ACTIONS)}"
            )

    @classmethod
    def from_env(cls) -> "ComposioGmail | None":
        from afin.config import load_dotenv

        load_dotenv()
        key = os.environ.get("AFIN_COMPOSIO_API_KEY", "").strip()
        user = os.environ.get("AFIN_COMPOSIO_USER_ID", "").strip()
        if not (key and user):
            return None
        return cls(
            api_key=key,
            user_id=user,
            base_url=os.environ.get(
                "AFIN_COMPOSIO_BASE_URL", "https://backend.composio.dev/api/v3"
            ).rstrip("/"),
        )

    def send(self, message: Message) -> DeliveryResult:
        import httpx

        payload = {
            "user_id": self.user_id,
            "arguments": {
                "recipient_email": message.to,
                "subject": message.subject,
                "body": message.body_html,
                "is_html": True,
            },
        }
        try:
            r = httpx.post(
                f"{self.base_url}/tools/execute/{self.action}",
                headers={"x-api-key": self.api_key},
                json=payload,
                timeout=self.timeout,
            )
            body = r.json()
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(False, self.name, f"transport error: {exc}")

        if r.status_code >= 400 or body.get("successful") is False:
            detail = body.get("error") or body.get("message") or r.text[:200]
            return DeliveryResult(False, self.name, f"rejected: {detail}")
        data = body.get("data") or {}
        ref = data.get("id") or data.get("threadId") or "sent"
        return DeliveryResult(True, self.name, f"delivered to {message.to}", str(ref))


def build_notifier() -> Notifier:
    """Composio when it is configured, otherwise the local outbox."""
    return ComposioGmail.from_env() or LocalOutbox()
