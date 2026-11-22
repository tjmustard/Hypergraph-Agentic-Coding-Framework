#!/usr/bin/env python3
"""Stub executor for engine tests: acts out one scenario and prints a claude-like JSON result.

Usage:
    stub_executor.py --scenario <name> --prompt <text>

It runs in the slice worktree (cwd) the way `claude -p --output-format json` would, prints one
result object (type, subtype, is_error, num_turns, result, session_id, total_cost_usd,
permission_denials, api_error_status, plus `stub_writes`: every path it wrote) and exits 0, or 1
for an error result. The phase is audit when the prompt names hyper-audit, else execute. It spends
nothing and never calls a network.

Scenarios (execute writes stub_change.txt unless noted; audit writes a PASS
$HACF_OUTBOX/audit.json unless noted):
- success
- low_confidence: execute appends a low_confidence gate to $HACF_OUTBOX/gates.jsonl, no change
- oracle_fail: execute also writes test_stub_oracle_fail.py, a failing pytest test
- audit_fail_then_pass: the first audit writes FAIL with a punch list, later ones PASS
- timeout: sleeps with a child process that ignores SIGTERM; the leader exits 143 on SIGTERM
- budget_exceeded: an error_max_budget_usd result
- scope_violation: execute writes spec/compiled/architecture.yml (an always-protected path)
- rate_limited: an API 429 error result (shape UNVERIFIED)
- ref_move: execute creates refs/hacf/stub-moved with git update-ref
- hook_reject: execute writes STUB_HOOK_REJECT, which a test pre-commit hook refuses
- git_meta_write: execute rewrites the worktree's .git pointer to the common dir and writes
  <common>/worktrees/<other>/HEAD of every sibling worktree
- credential_leak: execute writes stub_config.txt holding credential-shaped strings
- dirty_after_audit: audit also writes stub_audit_scratch.txt in the worktree
- control_path_edit: execute writes .claude/settings.json

State that must survive between phases (the audit_fail_then_pass count, the timeout pids and
SIGTERM marker) lives in $HACF_STUB_STATE, else the temp dir.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCENARIOS = (
    "success",
    "low_confidence",
    "oracle_fail",
    "audit_fail_then_pass",
    "timeout",
    "budget_exceeded",
    "scope_violation",
    "rate_limited",
    "ref_move",
    "hook_reject",
    "git_meta_write",
    "credential_leak",
    "dirty_after_audit",
    "control_path_edit",
)


def _state_dir() -> Path:
    path = Path(os.environ.get("HACF_STUB_STATE") or tempfile.gettempdir())
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(path: Path, text: str, writes: list[str], append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a" if append else "w", encoding="utf-8") as handle:
        handle.write(text)
    writes.append(str(path.resolve()))


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, timeout=60
    ).stdout.strip()


def _outbox() -> Path | None:
    value = os.environ.get("HACF_OUTBOX")
    return Path(value) if value else None


def _audit(verdict: str, writes: list[str], punch_list: list[str] | None = None) -> None:
    outbox = _outbox()
    if outbox is None:
        return
    record = {
        "slice": os.environ.get("HACF_SLICE", ""),
        "verdict": verdict,
        "punch_list": punch_list or [],
        "notes": ["stub audit"],
        "money_evidence": "stub executor",
    }
    _write(outbox / "audit.json", json.dumps(record) + "\n", writes)


def _timeout() -> None:
    """Sleep with a child that ignores SIGTERM, so only the group SIGKILL ends it."""
    state = _state_dir()
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(600)",
        ]
    )

    def on_term(_signum: int, _frame: object) -> None:
        (state / "stub-timeout.term").write_text("SIGTERM\n", encoding="utf-8")
        sys.exit(143)

    signal.signal(signal.SIGTERM, on_term)
    (state / "stub-timeout.pids").write_text(f"{os.getpid()} {child.pid}\n", encoding="utf-8")
    time.sleep(600)


def _git_meta_write(writes: list[str]) -> None:
    common = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    own = Path(_git("rev-parse", "--path-format=absolute", "--git-dir"))
    head = _git("rev-parse", "HEAD")
    pointer = Path(_git("rev-parse", "--show-toplevel")) / ".git"
    if pointer.is_file():
        _write(pointer, f"gitdir: {common}\n", writes)
    siblings = common / "worktrees"
    for other in sorted(siblings.iterdir()) if siblings.is_dir() else []:
        if other.resolve() != own.resolve():
            _write(other / "HEAD", head + "\n", writes)


def _credentials() -> str:
    # Built from parts so no credential shape exists in this source file.
    aws = "AK" + "IA" + "STUB" + "EXAMPLE" + "7Q2ZX"
    pem = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
    return f"aws_access_key_id = {aws}\n{pem}\nstub\n"


def act(scenario: str, phase: str, writes: list[str]) -> dict:
    """Perform the scenario's side effects; return the result fields that differ from success."""
    cwd = Path.cwd()
    if scenario == "timeout":
        _timeout()
    if scenario == "budget_exceeded":
        return {"subtype": "error_max_budget_usd", "is_error": True, "result": None}
    if scenario == "rate_limited":
        return {
            "is_error": True,
            "api_error_status": 429,
            "result": "API Error: 429 rate_limit_error: stub rate limit",
        }
    if phase == "audit":
        if scenario == "audit_fail_then_pass":
            counter = _state_dir() / (
                f"stub-audit-{os.environ.get('HACF_RUN_ID', 'run')}-"
                f"{os.environ.get('HACF_SLICE', 'slice')}.count"
            )
            calls = int(counter.read_text()) if counter.is_file() else 0
            counter.write_text(str(calls + 1), encoding="utf-8")
            if calls == 0:
                _audit("FAIL", writes, ["stub punch-list item"])
                return {"result": "Audit FAIL (stub)"}
        _audit("PASS", writes)
        if scenario == "dirty_after_audit":
            _write(cwd / "stub_audit_scratch.txt", "left behind by the audit\n", writes)
        return {"result": "Audit PASS (stub)"}
    if scenario == "low_confidence":
        outbox = _outbox()
        if outbox is not None:
            record = {
                "gate": "low_confidence",
                "slice": os.environ.get("HACF_SLICE", ""),
                "reason": "stub: confidence 6/10",
                "at": "2026-10-21T12:00:00+00:00",
            }
            _write(outbox / "gates.jsonl", json.dumps(record) + "\n", writes, append=True)
        return {"result": "Confidence 6/10; stopped at the gate (stub)"}
    _write(cwd / "stub_change.txt", f"stub change for {scenario}\n", writes)
    if scenario == "oracle_fail":
        _write(
            cwd / "test_stub_oracle_fail.py",
            'def test_stub_oracle_fail():\n    assert False, "stub oracle_fail"\n',
            writes,
        )
    elif scenario == "scope_violation":
        path = cwd / "spec/compiled/architecture.yml"
        _write(path, "# stub scope violation\n", writes, append=True)
    elif scenario == "ref_move":
        _git("update-ref", "refs/hacf/stub-moved", "HEAD")
        writes.append("refs/hacf/stub-moved")
    elif scenario == "hook_reject":
        _write(cwd / "STUB_HOOK_REJECT", "a test pre-commit hook refuses this file\n", writes)
    elif scenario == "git_meta_write":
        _git_meta_write(writes)
    elif scenario == "credential_leak":
        _write(cwd / "stub_config.txt", _credentials(), writes)
    elif scenario == "control_path_edit":
        settings = '{"permissions": {"allow": ["Bash"]}}\n'
        _write(cwd / ".claude/settings.json", settings, writes)
    return {"result": f"Execute done (stub {scenario})"}


def main(argv: list[str] | None = None) -> int:
    """Act out the scenario, print the JSON result and return the exit code."""
    parser = argparse.ArgumentParser(prog="stub_executor.py")
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    parser.add_argument("--prompt", required=True)
    ns = parser.parse_args(argv)
    phase = "audit" if "hyper-audit" in ns.prompt else "execute"
    writes: list[str] = []
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1,
        "num_turns": 1,
        "result": "",
        "session_id": f"stub-{ns.scenario}",
        "total_cost_usd": 0.0,
        "permission_denials": [],
        "api_error_status": None,
    }
    result.update(act(ns.scenario, phase, writes))
    result["stub_writes"] = writes
    print(json.dumps(result))
    return 1 if result["is_error"] else 0


if __name__ == "__main__":
    sys.exit(main())
