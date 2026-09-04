"""Runtime settings. Reads .env without adding a dependency.

LLM credentials are grouped into named *profiles* so the model becomes an
experiment dimension rather than a constant: two runs can differ only by
`--profile` and stay comparable in every other respect.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_dotenv(path: pathlib.Path | None = None) -> None:
    """Populate os.environ from .env. Existing variables win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


class UnknownProfile(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class LLMProfile:
    """One OpenAI-compatible endpoint, named."""

    name: str
    api_key: str
    base_url: str
    model: str
    #: "chat_completions" or "responses". Agent Framework's OpenAIChatClient
    #: targets the Responses API, which many OpenAI-compatible gateways do not
    #: serve; chat completions is the portable default.
    api_style: str = "chat_completions"
    #: Reasoning budget for models that expose one. Low by default: choosing a
    #: recovery action is a small structured decision, and higher effort buys
    #: latency across 50 payments rather than better action selection. It is a
    #: profile setting, and recorded on the run, so it can be varied on purpose.
    reasoning_effort: str = "low"
    #: Minimum seconds between request starts for this profile. Free tiers
    #: throttle aggressively, and discovering the limit by taking a 429 and
    #: backing off wastes far more wall-clock than simply not exceeding it.
    min_interval_seconds: float = 0.0

    def describe(self) -> str:
        return f"{self.name}:{self.model}"


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        return cls(
            database_url=os.environ.get(
                "AFIN_DATABASE_URL",
                "postgresql+psycopg2://postgres:postgres@localhost:5432/agent_finance",
            )
        )

    @staticmethod
    def profiles() -> tuple[str, ...]:
        load_dotenv()
        raw = os.environ.get("AFIN_LLM_PROFILES", "")
        return tuple(p.strip() for p in raw.split(",") if p.strip())

    @staticmethod
    def profile(name: str) -> LLMProfile:
        load_dotenv()
        prefix = f"AFIN_LLM_{name.upper().replace('-', '_')}_"
        api_key = os.environ.get(prefix + "API_KEY", "")
        base_url = os.environ.get(prefix + "BASE_URL", "")
        model = os.environ.get(prefix + "MODEL", "")
        api_style = os.environ.get(prefix + "API_STYLE", "chat_completions")
        reasoning_effort = os.environ.get(prefix + "REASONING_EFFORT", "low")
        min_interval = float(os.environ.get(prefix + "MIN_INTERVAL_SECONDS", "0") or 0)
        if not (api_key and model):
            raise UnknownProfile(
                f"LLM profile {name!r} is not configured; expected "
                f"{prefix}API_KEY and {prefix}MODEL in .env. "
                f"Known profiles: {', '.join(Settings.profiles()) or 'none'}"
            )
        return LLMProfile(
            name=name,
            api_key=api_key,
            base_url=base_url,
            model=model,
            api_style=api_style,
            reasoning_effort=reasoning_effort,
            min_interval_seconds=min_interval,
        )
