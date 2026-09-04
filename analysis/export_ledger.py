"""Export a run's ledger to disk. READ-ONLY.

Exists because the last analysis was crippled by losing exactly this data: the
append-only triggers block UPDATE and DELETE but not DROP TABLE, and a
development schema drop destroyed the per-payment evidence for three valid runs.
A file on disk survives that.

    PYTHONPATH=src python analysis/export_ledger.py <run_id> [<run_id> ...]
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")

from sqlalchemy import select  # noqa: E402

from afin.db.engine import get_engine  # noqa: E402
from afin.db.schema import audit_events, payment_attempts, runs  # noqa: E402

OUT = pathlib.Path("data/ledger")


def export(run_id: str) -> pathlib.Path:
    engine = get_engine()
    with engine.connect() as conn:
        run = conn.execute(select(runs).where(runs.c.run_id == run_id)).mappings().first()
        events = conn.execute(
            select(audit_events).where(audit_events.c.run_id == run_id).order_by(audit_events.c.seq)
        ).mappings().all()
        attempts = conn.execute(
            select(payment_attempts).where(payment_attempts.c.run_id == run_id)
        ).mappings().all()

    if not events:
        raise SystemExit(f"no audit events for {run_id!r}")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{run_id}.json"
    path.write_text(
        json.dumps(
            {
                "run": dict(run) if run else None,
                "audit_events": [dict(e) for e in events],
                "payment_attempts": [dict(a) for a in attempts],
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for rid in sys.argv[1:]:
        p = export(rid)
        print(f"exported {rid} -> {p} ({p.stat().st_size / 1024:.0f} KB)")
