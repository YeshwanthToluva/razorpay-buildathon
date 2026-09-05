"""Versioned, hashable policy configuration.

The config is hashed into every experiment run record. "Why did Experiment 17
differ from Experiment 12?" is only answerable if the exact thresholds in force
are recoverable from the run row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

#: v1 is what every committed experiment ran under: fourteen rules, no risk-type
#: precondition and no delivery allowlist. v2 adds both, so it is a different
#: policy and must not be compared to v1 results as though it were the same one.
POLICY_VERSION = "policy-v2"


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    #: Hard cap on automated attempts against the same instrument.
    max_retries: int = 3
    #: Above this, an automated action needs a human. Rs 10,000 in paise.
    high_value_threshold_minor: int = 1_000_000
    #: Minimum gap between attempts; back-to-back retries annoy issuers.
    retry_cooldown_hours: int = 6
    #: Cap on outbound customer contacts per payment.
    max_contacts: int = 3
    #: A scheduled retry must land inside a sensible horizon.
    max_schedule_delay_hours: int = 72
    #: Addresses an outbound message may be delivered to. Empty means the
    #: system may not send to anyone, which is the safe default: a misconfigured
    #: deployment sends nothing rather than everything.
    email_allowlist: tuple[str, ...] = ()
    #: True when there is no automated payment rail and every collection attempt
    #: reaches the customer directly. Charging actions then have to satisfy the
    #: communication rules as well -- opt-out, allowlist, contact budget --
    #: because they now involve contacting a person. Without this the live
    #: channel would message an opted-out customer while policy believed it was
    #: silently re-presenting a card.
    contact_is_the_payment_rail: bool = False
    version: str = POLICY_VERSION

    def fingerprint(self) -> str:
        """Identify the policy actually in force: thresholds *and* rule set.

        Hashing the config alone was not enough. Adding RISK_TYPE_PRECONDITION
        changed what the engine permits while leaving the fingerprint untouched,
        so two runs under materially different policies could have been compared
        as though they matched. The rule identifiers and their order are part of
        the policy, so they are part of its identity.
        """
        from afin.policy.engine import RULES

        payload = json.dumps(
            {
                "config": asdict(self),
                "rules": [rule_id.value for rule_id, _ in RULES],
            },
            sort_keys=True,
        ).encode()
        return hashlib.sha256(payload).hexdigest()[:16]


def _allowlist_from_env() -> tuple[str, ...]:
    """Read AFIN_EMAIL_ALLOWLIST. Absent or empty means send to nobody.

    Loads .env itself rather than assuming someone else already did. This value
    decides who the system may contact, and a security-relevant setting that
    silently depends on module import order is a defect: it would populate when
    a mail tool happened to be imported first and be empty otherwise.
    """
    import os

    from afin.config import load_dotenv

    load_dotenv()
    raw = os.environ.get("AFIN_EMAIL_ALLOWLIST", "")
    return tuple(sorted({a.strip().lower() for a in raw.split(",") if a.strip()}))


DEFAULT_POLICY_CONFIG = PolicyConfig(email_allowlist=_allowlist_from_env())
