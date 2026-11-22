#!/usr/bin/env python3
"""Failure taxonomy, classification and retry policy for the HACF workflow engine.

Normative design: HACF's v0.6.0 design notes (not shipped): the engine (FAILURE TAXONOMY, BUDGET AND
RATE LIMITS). Every engine step names a failure with one FailureType; classify() maps an executor
ExecResult (hyper_executors.py) plus engine-side conditions to one, and RetryPolicy tells the
scheduler what to do next.

Categories:
- fatal: preflight_failed, executor_unavailable, executor_auth, sandbox_unavailable
- retried: rate_limited (a global pause of every worker, never a per-slice retry, R20);
  timeout and executor_error (one retry each)
- retried once, then blocked: oracle_failed, audit_failed (autonomy.retries.oracle_or_audit_fail)
- blocked: turns_exceeded, budget_exceeded, gate_stopped, scope_violation, ref_violation,
  merge_conflict, commit_rejected
- rolled back, then blocked: ledger_invalid, lore_invalid
- upstream_blocked: the dependents of a blocked slice
- internal_error: the run aborts with its state saved
"""

from __future__ import annotations

from enum import Enum
from typing import Any, NamedTuple

from _runtime import require_runtime

require_runtime()


class FailureType(str, Enum):
    """One failure of the engine's taxonomy; the value is the name stored in run.json."""

    preflight_failed = "preflight_failed"
    executor_unavailable = "executor_unavailable"
    executor_auth = "executor_auth"
    sandbox_unavailable = "sandbox_unavailable"
    rate_limited = "rate_limited"
    timeout = "timeout"
    executor_error = "executor_error"
    oracle_failed = "oracle_failed"
    audit_failed = "audit_failed"
    turns_exceeded = "turns_exceeded"
    budget_exceeded = "budget_exceeded"
    gate_stopped = "gate_stopped"
    scope_violation = "scope_violation"
    ref_violation = "ref_violation"
    merge_conflict = "merge_conflict"
    commit_rejected = "commit_rejected"
    ledger_invalid = "ledger_invalid"
    lore_invalid = "lore_invalid"
    upstream_blocked = "upstream_blocked"
    internal_error = "internal_error"


FATAL = "fatal"
RETRIED = "retried"
RETRY_THEN_BLOCK = "retried_once_then_blocked"
BLOCKED = "blocked"
ROLLBACK = "rolled_back_then_blocked"
UPSTREAM = "upstream_blocked"
ABORT = "abort"

F = FailureType
CATEGORY: dict[FailureType, str] = {
    **dict.fromkeys(
        (F.preflight_failed, F.executor_unavailable, F.executor_auth, F.sandbox_unavailable), FATAL
    ),
    **dict.fromkeys((F.rate_limited, F.timeout, F.executor_error), RETRIED),
    **dict.fromkeys((F.oracle_failed, F.audit_failed), RETRY_THEN_BLOCK),
    **dict.fromkeys(
        (
            F.turns_exceeded,
            F.budget_exceeded,
            F.gate_stopped,
            F.scope_violation,
            F.ref_violation,
            F.merge_conflict,
            F.commit_rejected,
        ),
        BLOCKED,
    ),
    **dict.fromkeys((F.ledger_invalid, F.lore_invalid), ROLLBACK),
    F.upstream_blocked: UPSTREAM,
    F.internal_error: ABORT,
}


def classify(
    result: Any = None,
    *,
    timed_out: bool = False,
    oracle_exit: int | None = None,
    audit_verdict: str | None = None,
) -> FailureType | None:
    """Return the FailureType of one phase, or None when it succeeded.

    result is an ExecResult (or anything with ok and failure_type); an unknown executor failure
    string becomes executor_error. timed_out is the engine's own timeout, oracle_exit the oracle's
    exit code and audit_verdict the audit.json verdict (anything but PASS fails).
    """
    if timed_out:
        return F.timeout
    if result is not None and not result.ok:
        try:
            return FailureType(result.failure_type)
        except ValueError:
            return F.executor_error
    if oracle_exit is not None and oracle_exit != 0:
        return F.oracle_failed
    if audit_verdict is not None and str(audit_verdict).upper() != "PASS":
        return F.audit_failed
    return None


class Action(NamedTuple):
    """What the scheduler does next; delay_s is set only for pause_all."""

    kind: str  # retry, pause_all, block, rollback_then_block, abort or fatal
    delay_s: float | None = None


class RetryPolicy:
    """Retry rules built from autonomy.retries ({transient, oracle_or_audit_fail, backoff_s})."""

    def __init__(self, retries: dict | None = None) -> None:
        """Read the counts; transient counts the run's global rate-limit pauses."""
        retries = retries or {}
        self.transient = int(retries.get("transient", 2))
        self.oracle_or_audit = int(retries.get("oracle_or_audit_fail", 1))
        self.backoff_s = [float(s) for s in retries.get("backoff_s", [30, 120])]

    def next_action(self, failure: FailureType | str, attempts: int) -> Action:
        """Return the action for failure after attempts retries of it (rate_limited: pauses).

        rate_limited pauses every worker with the next backoff_s value and blocks the slice once
        the transient pauses are spent; it never retries one slice on its own (R20).
        """
        failure = FailureType(failure)
        category = CATEGORY[failure]
        if failure is F.rate_limited:
            if attempts < self.transient:
                return Action("pause_all", self.backoff_s[min(attempts, len(self.backoff_s) - 1)])
            return Action("block")
        if category in (RETRIED, RETRY_THEN_BLOCK):
            limit = 1 if category == RETRIED else self.oracle_or_audit
            return Action("retry" if attempts < limit else "block")
        if category == ROLLBACK:
            return Action("rollback_then_block")
        if category == FATAL:
            return Action("fatal")
        if category == ABORT:
            return Action("abort")
        return Action("block")  # blocked and upstream_blocked
