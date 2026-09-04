"""Experiment 002g — behavioural transitions after a failed action. READ-ONLY.

The question is not whether recovery moved. It is what the agent *does* on the
cycle after one of its own actions is executed and fails, and whether telling it
the outcome changes that.

    PYTHONPATH=src python analysis/feedback.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")
sys.path.insert(0, ".")

RETRY_ACTIONS = {"RETRY_PAYMENT", "SCHEDULE_RETRY"}
LEDGER = pathlib.Path("data/ledger")


def timeline(ledger: dict) -> dict[str, list[dict]]:
    """Per payment, the ordered sequence of proposals and execution results."""
    out: dict[str, list[dict]] = {}
    for e in ledger["audit_events"]:
        t = e["event_type"]
        if t not in ("PROPOSAL_MADE", "ACTION_EXECUTED"):
            continue
        out.setdefault(e["payment_id"], []).append(
            {
                "type": t,
                "cycle": e["cycle"],
                "action": e["proposed_action"] if t == "PROPOSAL_MADE" else e["executed_action"],
                "result": e["execution_result"],
                "recovered": e["revenue_recovered_minor"] or 0,
                "claimed_last_outcome": e.get("claimed_last_attempt_outcome"),
            }
        )
    return out


def transitions(ledger: dict) -> dict:
    """What the agent proposes on the cycle after its own action failed.

    A failure is an ACTION_EXECUTED whose result is FAILURE and which recovered
    nothing. The "next decision" is the next PROPOSAL_MADE on the same payment.
    """
    tl = timeline(ledger)
    stats = {
        "failures_observed": 0,
        "next_action": {},
        "repeated_same_action_after_failure": 0,
        "retry_after_failed_retry": 0,
        "cycles_burned_after_failed_action": 0,
        "recovery_after_failure": 0,
        "recovered_revenue_after_failure": 0,
        "absorbed_outcome": {"SUPPORTED": 0, "CONTRADICTED": 0, "MISSING": 0},
    }
    for events in tl.values():
        for i, ev in enumerate(events):
            if ev["type"] != "ACTION_EXECUTED" or ev["result"] != "FAILURE":
                continue
            stats["failures_observed"] += 1
            nxt = next((e for e in events[i + 1:] if e["type"] == "PROPOSAL_MADE"), None)
            if nxt is None:
                continue
            action = nxt["action"]
            stats["next_action"][action] = stats["next_action"].get(action, 0) + 1
            if action == ev["action"]:
                stats["repeated_same_action_after_failure"] += 1
            if ev["action"] in RETRY_ACTIONS and action in RETRY_ACTIONS:
                stats["retry_after_failed_retry"] += 1
            stats["cycles_burned_after_failed_action"] += 1

            # Did the agent correctly restate that the previous attempt failed?
            claimed = (nxt.get("claimed_last_outcome") or "").upper()
            if not claimed:
                stats["absorbed_outcome"]["MISSING"] += 1
            elif claimed.startswith("FAIL"):
                stats["absorbed_outcome"]["SUPPORTED"] += 1
            else:
                stats["absorbed_outcome"]["CONTRADICTED"] += 1

            later = [e for e in events[i + 1:] if e["type"] == "ACTION_EXECUTED"]
            gained = sum(e["recovered"] for e in later)
            if gained:
                stats["recovery_after_failure"] += 1
                stats["recovered_revenue_after_failure"] += gained
    return stats


def compare(arms: dict[str, str]) -> str:
    res = {n: transitions(json.loads(pathlib.Path(p).read_text())) for n, p in arms.items()}
    names = list(arms)
    lines = [f"{'metric':<44}" + "".join(f"{n[:16]:>18}" for n in names)]
    for k in (
        "failures_observed",
        "repeated_same_action_after_failure",
        "retry_after_failed_retry",
        "cycles_burned_after_failed_action",
        "recovery_after_failure",
    ):
        lines.append(f"{k:<44}" + "".join(f"{res[n][k]:>18}" for n in names))
    lines.append(
        f"{'revenue recovered after a failure':<44}"
        + "".join(f"{res[n]['recovered_revenue_after_failure']/100:>18,.0f}" for n in names)
    )
    lines.append("")
    lines.append("P(next action | previous action FAILED)")
    actions = sorted({a for n in names for a in res[n]["next_action"]})
    for a in actions:
        row = f"  {a:<42}"
        for n in names:
            tot = sum(res[n]["next_action"].values()) or 1
            c = res[n]["next_action"].get(a, 0)
            row += f"{c:>7} {c/tot:>9.0%}"
        lines.append(row)
    lines.append("")
    lines.append("did the agent restate that the previous attempt FAILED?")
    for v in ("SUPPORTED", "CONTRADICTED", "MISSING"):
        lines.append(
            f"  {v:<42}" + "".join(f"{res[n]['absorbed_outcome'][v]:>18}" for n in names)
        )
    return "\n".join(lines)


if __name__ == "__main__":
    import glob

    arms = {}
    for pat, label in (("*treatment-nofb*", "002g control"), ("*treatment-fb*", "002g feedback")):
        hits = sorted(glob.glob(str(LEDGER / pat)))
        if hits:
            arms[label] = hits[-1]
    if not arms:
        raise SystemExit("no 002g ledgers exported yet")
    print(compare(arms))
