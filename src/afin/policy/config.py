"""Versioned, hashable policy configuration.

The config is hashed into every experiment run record. "Why did Experiment 17
differ from Experiment 12?" is only answerable if the exact thresholds in force
are recoverable from the run row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

POLICY_VERSION = "policy-v1"


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
    version: str = POLICY_VERSION

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]


DEFAULT_POLICY_CONFIG = PolicyConfig()
