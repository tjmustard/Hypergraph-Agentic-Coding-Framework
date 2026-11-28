#!/usr/bin/env python3
"""One per-user heavy-job lock and memory budget for HACF's heavy entry points (OPR.99.0.6.2).

check.sh, every pytest of this repository (dev/tests/conftest.py), the release gate and the
OpenRig compat and probe runs take the lock through this tool, so at most one HACF heavy job runs on
a host at a time and none starts below the memory floor:

    python3 dev/scripts/host_gate.py run --job <name> [--wait-s N] -- <argv...>
    python3 dev/scripts/host_gate.py status [--json]

This is a client of openrig/hacf/bin/hacf-host.py, the scheduler installed projects run as
`hacf-host.py job run`, so both share one lock and one ticket queue (OPR.99.0.16.3). A job here
takes a ticket with no declared RAM or GPU need and the rig of OPENRIG_SESSION_NAME, and is ordered
by that rig's priority tier in host.yml.

Admission: wait for this ticket's turn and for MemAvailable >= HACF_HOST_MIN_MB (and the host
config's reserve_mb) without holding the lock; take `flock(LOCK_EX|LOCK_NB)`; re-read MemAvailable
and, below the floor, release and wait again. The whole wait is bounded (HACF_HEAVY_WAIT_S or
--wait-s); when it runs out the tool prints `host busy: <reason>; retry later (exit 75)` and exits
75. Exit 75 is never a test verdict: it means "the host was busy, retry later". A host state written
by a newer HACF also ends in 75.

Re-entrancy: the holder exports a random token as HACF_HEAVY_LOCK_HELD. A nested caller (gate ->
check.sh -> pytest) skips acquiring only when the token is valid: it equals the holder record's
token, the record's pid is alive and that pid is the caller or one of its ancestors (or, for a
launcher that breaks the ancestry, the pid HACF_HEAVY_LOCK_HOLDER_PID names). Any other token is
ignored and the caller acquires normally.

A heavy process that does not take the lock (another project's pytest) never blocks; it only lowers
the worker count check.sh uses (`workers_hint`).

Environment:
    HACF_HEAVY_LOCK_PATH      the lock file (default ${XDG_STATE_HOME:-$HOME/.local/state}/hacf/
                              heavy-job.lock); the holder record and log sit beside it. Tests set
                              it; a token issued for another lock file never validates under it.
    HACF_HOST_STATE_DIR       the host state dir holding the ticket queue (default
                              ${XDG_STATE_HOME:-$HOME/.local/state}/hacf/host).
    HACF_HOST_MIN_MB          the MemAvailable floor in MB (default 6144).
    HACF_HEAVY_WAIT_S         the bounded wait in seconds (default 3600).
    HACF_HEAVY_POLL_S         seconds between polls (default 10; tests set it lower).
    HACF_TEST_WORKERS_SHARED  the worker hint when another heavy run exists (default 4).
    HACF_HOST_MEMINFO         a file read instead of /proc/meminfo (a test seam).

Python 3.10+, standard library only (PyYAML, when present, reads host.yml). It writes nothing
outside the lock's directory and the host state dir.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

HOST_SCRIPT = Path(__file__).resolve().parents[2] / "openrig" / "hacf" / "bin" / "hacf-host.py"


def _load_host() -> ModuleType:
    """The shipped hacf-host.py as the module `hacf_host`, loaded once; no bytecode is written."""
    loaded = sys.modules.get("hacf_host")
    if loaded is not None and Path(str(loaded.__file__)) == HOST_SCRIPT:
        return loaded
    spec = importlib.util.spec_from_file_location("hacf_host", HOST_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {HOST_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["hacf_host"] = module
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("hacf_host", None)
        raise
    finally:
        sys.dont_write_bytecode = before
    return module


_host = _load_host()

SCHEMA = _host.JOB_SCHEMA
EXIT_BUSY = _host.EXIT_BUSY  # 75, EX_TEMPFAIL: retry later
TOKEN_VAR = _host.TOKEN_VAR
INTERPRETER_RE = _host.INTERPRETER_RE

HostBusy = _host.HostBusy
Lease = _host.Lease
lock_path = _host.heavy_lock_path
mem_available_mb = _host.mem_available_mb
pid_alive = _host.pid_alive
token_valid = _host.token_valid
ancestor_pids = _host.ancestor_pids
is_heavy_process = _host.is_heavy_process
_runs_heavy = _host._runs_heavy  # dev/release_export.py re-exports it
_pytest_module = _host._pytest_module  # likewise
non_cooperating = _host.non_cooperating
workers_hint = _host.workers_hint


def acquire(job: str, wait_s: float | None = None, *, out=None) -> Lease:
    """Take the heavy-job lock for `job` through the ticket queue; raise HostBusy on timeout.

    A valid inherited token returns a re-entrant Lease at once. Messages go to `out` (stderr).
    """
    try:
        return _host.acquire(
            job,
            wait_s,
            rig_name=_host.job_rig(argparse.Namespace(rig=None), os.environ),
            mem=lambda: mem_available_mb(),  # this module's name, so a test can replace it
            out=out,
            prog="host_gate",
            lock_word="",
        )
    except _host.NewerSchema as error:
        raise HostBusy(str(error)) from error


def run(job: str, argv: list[str], wait_s: float | None = None) -> int:
    """Run argv holding the lock; return its exit code, or 75 when the wait runs out."""
    return _host.run_job(argv, lambda: acquire(job, wait_s))


def status() -> dict:
    """The lock, its holder (or a stale record), memory, the budget, the queue and heavy runs."""
    lock = lock_path()
    record = _host.read_record(lock)
    alive = bool(record) and pid_alive(record.get("pid"))
    others = non_cooperating()
    return {
        "schema": "hacf.heavy-job-status/v1",
        "lock": str(lock),
        "locked": _host._probe_locked(lock),
        "holder_state": "held" if alive else ("stale" if record else "none"),
        "holder": record if alive else None,
        "stale_record": record if record and not alive else None,
        "mem_available_mb": mem_available_mb(),
        "min_mb": _host.min_mb(),
        "wait_s": _host._env_number("HACF_HEAVY_WAIT_S", _host.DEFAULT_WAIT_S),
        "non_cooperating": others,
        "workers_hint": workers_hint(others) if others else None,
        "queue": _host.queue_entries(),
    }


def _print_status(data: dict) -> None:
    print(f"lock: {data['lock']} (locked: {data['locked']})")
    holder = data["holder"]
    if holder:
        print(f"holder: {holder['job']} pid {holder['pid']} in {holder['cwd']} since "
              f"{holder['started_at']}")  # fmt: skip
    elif data["stale_record"]:
        print(f"holder: stale (pid {data['stale_record'].get('pid')} is not running)")
    else:
        print("holder: none")
    print(f"MemAvailable: {data['mem_available_mb']} MB (floor {data['min_mb']} MB)")
    print(f"non-cooperating heavy processes: {len(data['non_cooperating'])}")
    for line in data["non_cooperating"]:
        print(f"  {line}")
    if data["workers_hint"]:
        print(f"workers hint: {data['workers_hint']}")


def main(argv: list[str] | None = None) -> int:
    """The CLI: `run` and `status`."""
    parser = argparse.ArgumentParser(prog="host_gate.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="run a command holding the heavy-job lock")
    p_run.add_argument("--job", required=True)
    p_run.add_argument("--wait-s", type=float, default=None)
    p_run.add_argument("argv", nargs=argparse.REMAINDER)
    p_status = sub.add_parser("status", help="show the lock, its holder and the memory budget")
    p_status.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "run":
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not command:
            parser.error("run needs a command after --")
        return run(args.job, command, args.wait_s)
    data = status()
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        _print_status(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
