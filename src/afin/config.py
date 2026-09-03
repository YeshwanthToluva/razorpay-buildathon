"""Runtime settings. Reads .env without adding a dependency."""

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


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    openai_api_key: str
    openai_base_url: str
    openai_model: str

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        return cls(
            database_url=os.environ.get(
                "AFIN_DATABASE_URL",
                "postgresql+psycopg2://postgres:postgres@localhost:5432/agent_finance",
            ),
            openai_api_key=os.environ.get("AFIN_OPENAI_API_KEY", ""),
            openai_base_url=os.environ.get("AFIN_OPENAI_BASE_URL", ""),
            openai_model=os.environ.get("AFIN_OPENAI_MODEL", "gpt-5.4-mini"),
        )
