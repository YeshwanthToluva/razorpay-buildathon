"""Execution traces for a run: what was called, with what, for how long.

The audit ledger answers "what did the system decide, and was it allowed". A
trace answers a different question: "what actually executed, in what order, how
long did each part take, and what went in and out". They are deliberately
separate. The ledger is evidence about decisions and must stay small and
durable; a trace is diagnostic and verbose.

Chain-of-thought is not captured here either. A model span records the prompt we
sent and the structured answer we parsed. Providers that return a separate
reasoning_content field have it read from nowhere.
"""

from __future__ import annotations

import json
import pathlib
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

STORE = pathlib.Path("data/traces")

#: Long values are truncated rather than dropped: a trace should stay readable
#: and a single oversized payload should not make the file unusable.
MAX_VALUE_CHARS = 4000


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + f"…[{len(value) - MAX_VALUE_CHARS} more chars]"
    return value


@dataclass
class Span:
    span_id: str
    name: str
    #: model | tool | policy | provider | orchestration
    kind: str
    started_at: str
    duration_ms: float | None = None
    parent_id: str | None = None
    payment_id: str | None = None
    cycle: int | None = None
    attributes: dict = field(default_factory=dict)
    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class Tracer:
    """Collects spans for one run and writes them next to the other evidence."""

    run_id: str
    spans: list[Span] = field(default_factory=list)
    #: Called as each span closes, so a live view can stream them.
    on_span: Callable[[Span], None] | None = None
    _stack: list[str] = field(default_factory=list)

    @contextmanager
    def span(
        self,
        name: str,
        kind: str,
        *,
        payment_id: str | None = None,
        cycle: int | None = None,
        **attributes: Any,
    ) -> Iterator[Span]:
        s = Span(
            span_id=uuid.uuid4().hex[:12],
            name=name,
            kind=kind,
            started_at=datetime.now(timezone.utc).isoformat(),
            parent_id=self._stack[-1] if self._stack else None,
            payment_id=payment_id,
            cycle=cycle,
            attributes={k: _clip(v) for k, v in attributes.items()},
        )
        self.spans.append(s)
        self._stack.append(s.span_id)
        t0 = time.perf_counter()
        try:
            yield s
        except Exception as exc:  # noqa: BLE001
            s.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            s.duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            self._stack.pop()
            s.input = {k: _clip(v) for k, v in s.input.items()}
            s.output = {k: _clip(v) for k, v in s.output.items()}
            if self.on_span:
                try:
                    self.on_span(s)
                except Exception:
                    # A watcher must never break the run it is watching.
                    pass

    def summary(self) -> dict:
        by_kind: dict[str, dict] = {}
        for s in self.spans:
            b = by_kind.setdefault(s.kind, {"calls": 0, "total_ms": 0.0, "errors": 0})
            b["calls"] += 1
            b["total_ms"] = round(b["total_ms"] + (s.duration_ms or 0), 2)
            b["errors"] += 0 if s.ok else 1
        return {
            "run_id": self.run_id,
            "spans": len(self.spans),
            "total_ms": round(sum(s.duration_ms or 0 for s in self.spans), 2),
            "by_kind": by_kind,
        }

    def save(self) -> pathlib.Path:
        STORE.mkdir(parents=True, exist_ok=True)
        path = STORE / f"{self.run_id}.json"
        path.write_text(
            json.dumps(
                {"summary": self.summary(), "spans": [asdict(s) for s in self.spans]},
                indent=2,
                default=str,
            )
        )
        return path


def load(run_id: str) -> dict | None:
    path = STORE / f"{run_id}.json"
    return json.loads(path.read_text()) if path.exists() else None
