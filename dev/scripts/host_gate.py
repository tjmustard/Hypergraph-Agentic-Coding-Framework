#!/usr/bin/env python3
"""One per-user heavy-job lock and memory budget for HACF's heavy entry points (OPR.99.0.6.2).

check.sh, every pytest of this repository (dev/tests/conftest.py), the release gate and the
OpenRig compat and probe runs take the lock through this tool, so at most one HACF heavy job runs on
a host at a time and none starts below the memory floor:

    python3 dev/scripts/host_gate.py run --job <name> [--wait-s N] -- <argv...>
    python3 dev/scripts/host_gate.py status [--json]

Acquire order: (a) wait until MemAvailable >= HACF_HOST_MIN_MB without holding the lock; (b) take
`flock(LOCK_EX|LOCK_NB)` in a poll loop; (c) re-read MemAvailable and, below the floor, release and
go back to (a). The whole wait is bounded (HACF_HEAVY_WAIT_S or --wait-s); when it runs out the
tool prints `host busy: <reason>; retry later (exit 75)` and exits 75. Exit 75 is never a test
verdict: it means "the host was busy, retry later".

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
    HACF_HOST_MIN_MB          the MemAvailable floor in MB (default 6144).
    HACF_HEAVY_WAIT_S         the bounded wait in seconds (default 3600).
    HACF_HEAVY_POLL_S         seconds between polls (default 10; tests set it lower).
    HACF_TEST_WORKERS_SHARED  the worker hint when another heavy run exists (default 4).
    HACF_HOST_MEMINFO         a file read instead of /proc/meminfo (a test seam).

Python 3.10+, standard library only. It writes nothing outside the lock's directory.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "hacf.heavy-job/v1"
EXIT_BUSY = 75  # EX_TEMPFAIL: retry later
TOKEN_VAR = "HACF_HEAVY_LOCK_HELD"
HOLDER_PID_VAR = "HACF_HEAVY_LOCK_HOLDER_PID"
PATH_VAR = "HACF_HEAVY_LOCK_PATH"
DEFAULT_MIN_MB = 6144
DEFAULT_WAIT_S = 3600
DEFAULT_POLL_S = 10.0
DEFAULT_SHARED_WORKERS = 4
KILL_GRACE_S = 10.0
REPORT_EVERY_S = 60.0


class HostBusy(Exception):
    """The bounded wait ran out; `reason` is the last thing that kept the job waiting."""

    def __init__(self, reason: str) -> None:
        """Keep the reason for the `host busy` line."""
        super().__init__(reason)
        self.reason = reason


# ------------------------------------------------------------------------------ paths, settings


def lock_path() -> Path:
    """The lock file: HACF_HEAVY_LOCK_PATH, else ${XDG_STATE_HOME:-~/.local/state}/hacf/."""
    override = os.environ.get(PATH_VAR)
    if override:
        return Path(override)
    state = os.environ.get("XDG_STATE_HOME", "")
    base = Path(state) if os.path.isabs(state) else Path.home() / ".local" / "state"
    return base / "hacf" / "heavy-job.lock"


def record_path(lock: Path) -> Path:
    """The holder record beside the lock (`heavy-job.json`)."""
    return lock.with_suffix(".json")


def log_path(lock: Path) -> Path:
    """The append-only acquire/release log beside the lock (`heavy-job.log`)."""
    return lock.with_suffix(".log")


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def min_mb() -> int:
    """The MemAvailable floor in MB."""
    return int(_env_number("HACF_HOST_MIN_MB", DEFAULT_MIN_MB))


def mem_available_mb() -> int | None:
    """MemAvailable in MB, or None when the host has no readable figure (the floor is then off)."""
    source = os.environ.get("HACF_HOST_MEMINFO") or "/proc/meminfo"
    try:
        text = Path(source).read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    match = re.search(r"^MemAvailable:\s+(\d+)", text, re.M)
    return int(match[1]) // 1024 if match else None


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------ processes


def _ppid(pid: int) -> int | None:
    """The parent of `pid` (from /proc, else ps), or None when it cannot be read."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        return int(stat.rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        pass
    if Path("/proc/self").exists():
        return None
    try:  # no /proc (macOS): ps
        out = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        return int(out.stdout.strip())
    except (OSError, ValueError):
        return None


def _ancestors(pid: int) -> list[int]:
    """`pid` and its ancestors, nearest first (stops at pid 1 or an unreadable parent)."""
    chain: list[int] = []
    while pid > 1 and pid not in chain:
        chain.append(pid)
        parent = _ppid(pid)
        if parent is None:
            break
        pid = parent
    return chain


def ancestor_pids() -> set[str]:
    """This process and its ancestors: the shell that started the gate is never "another run"."""
    return {str(pid) for pid in _ancestors(os.getpid())}


def pid_alive(pid: object) -> bool:
    """Whether `pid` names a running (not zombie) process."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def is_heavy_process(line: str) -> bool:
    """Whether a `pgrep -a` line is a real check.sh or pytest run.

    The pgrep pattern also matches any process whose arguments merely mention pytest, such as an
    agent whose permission settings allow it, `grep pytest` or an editor open on a pytest file.
    Only a process that runs one counts: a pytest or check.sh executable, an interpreter running
    that script or `-m pytest`/`-mpytest`, or `uv run` of any of these.
    """
    argv = line.split()[1:]
    if not argv:
        return False
    first = os.path.basename(argv[0])
    if _runs_heavy(argv[0]):
        return True
    if first in ("uv", "uvx") and "run" in argv:
        rest = argv[argv.index("run") + 1 :]
        return any(_runs_heavy(a) or _pytest_module(rest, i) for i, a in enumerate(rest))
    if INTERPRETER_RE.match(first):
        rest = argv[1:]
        for i, arg in enumerate(rest):
            if _pytest_module(rest, i):
                return True
            if not arg.startswith("-"):
                return _runs_heavy(arg)  # the script the interpreter runs
    return False


INTERPRETER_RE = re.compile(r"(python[0-9.]*|bash|sh|dash|zsh)\Z")


def _runs_heavy(arg: str) -> bool:
    """Whether one argument is a pytest executable or the check.sh script."""
    return os.path.basename(arg) in ("pytest", "py.test") or arg.endswith("dev/scripts/check.sh")


def _pytest_module(argv: list[str], i: int) -> bool:
    """Whether `argv[i]` starts `-m pytest` or `-mpytest`."""
    if argv[i] in ("-mpytest", "-mpy.test"):
        return True
    return argv[i] == "-m" and i + 1 < len(argv) and argv[i + 1] in ("pytest", "py.test")


def _process_lines() -> list[str]:
    """`<pid> <argv joined by spaces>` for every process of this user, like `pgrep -a`."""
    lines = []
    proc = Path("/proc")
    if not proc.is_dir():
        out = subprocess.run(
            ["ps", "-U", str(os.getuid()), "-o", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    uid = os.getuid()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            argv = (entry / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        except OSError:
            continue
        text = " ".join(a.decode("utf-8", "replace") for a in argv if a)
        if text:
            lines.append(f"{entry.name} {text}")
    return lines


def heavy_processes() -> list[str]:
    """This user's processes that really run pytest or check.sh (`is_heavy_process`)."""
    return [ln for ln in _process_lines() if is_heavy_process(ln)]


def non_cooperating(lines: list[str] | None = None) -> list[str]:
    """Heavy processes outside this job.

    Not this process or an ancestor, not the live holder and not a descendant of the holder.
    """
    lines = heavy_processes() if lines is None else lines
    own = {int(p) for p in ancestor_pids()}
    record = read_record(lock_path())
    holder = record.get("pid") if record and pid_alive(record.get("pid")) else None
    others = []
    for line in lines:
        try:
            pid = int(line.split(" ", 1)[0])
        except ValueError:
            continue
        if pid in own or (holder is not None and holder in _ancestors(pid)):
            continue
        others.append(line)
    return others


def workers_hint(lines: list[str] | None = None) -> int | None:
    """HACF_TEST_WORKERS_SHARED (default 4) when a non-cooperating heavy process exists, else None.

    `lines` replaces the live process list (tests pass synthetic `pgrep -a` lines).
    """
    if not non_cooperating(lines):
        return None
    return int(_env_number("HACF_TEST_WORKERS_SHARED", DEFAULT_SHARED_WORKERS))


# ---------------------------------------------------------------------------- record, token, log


def read_record(lock: Path) -> dict | None:
    """The holder record beside `lock`, or None when it is absent or unreadable."""
    try:
        data = json.loads(record_path(lock).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def _write_record(lock: Path, record: dict) -> None:
    """Write the holder record atomically (temp file + rename), only while the lock is held."""
    target = record_path(lock)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _log(lock: Path, event: str, job: str, seconds: float, pid: int | None = None) -> None:
    """Append one line: UTC time, event, pid, job and the seconds waited (or held)."""
    what = "waited" if event == "acquire" else "held"
    line = f"{_utc()} {event} pid={pid or os.getpid()} job={job} {what}={seconds:.1f}s\n"
    fd = os.open(log_path(lock), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def token_valid(env: dict | os._Environ | None = None) -> bool:
    """Whether `env` (default os.environ) holds a valid re-entrancy token for the current lock.

    Valid: HACF_HEAVY_LOCK_HELD equals the record's token, the record's pid is alive, and that pid
    is this process or an ancestor (or equals HACF_HEAVY_LOCK_HOLDER_PID when that is set).
    """
    env = os.environ if env is None else env
    token = env.get(TOKEN_VAR)
    if not token:
        return False
    record = read_record(lock_path())
    if not record or not secrets.compare_digest(str(record.get("token", "")), token):
        return False
    pid = record.get("pid")
    if not pid_alive(pid):
        return False
    named = env.get(HOLDER_PID_VAR)
    if named:
        return named == str(pid)
    return pid in _ancestors(os.getpid())


# -------------------------------------------------------------------------------------- acquire


class Lease:
    """A held lock (or a re-entrant pass-through when `fd` is None); a context manager."""

    def __init__(self, lock: Path, fd: int | None, token: str, job: str, waited: float) -> None:
        """A lease on `lock`; `fd` is the locked descriptor (None: re-entrant)."""
        self.lock, self.fd, self.token, self.job, self.waited = lock, fd, token, job, waited
        self.started = time.monotonic()

    @property
    def reentrant(self) -> bool:
        """True when an ancestor holds the lock and this lease only passes its token on."""
        return self.fd is None

    def env(self) -> dict[str, str]:
        """What a child needs to run inside this lease."""
        return {TOKEN_VAR: self.token}

    def release(self) -> None:
        """Remove our holder record, log the release and unlock (no-op when re-entrant or done)."""
        if self.fd is None:
            return
        try:
            record = read_record(self.lock)
            if record and record.get("token") == self.token:
                record_path(self.lock).unlink(missing_ok=True)
            _log(self.lock, "release", self.job, time.monotonic() - self.started)
        finally:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> Lease:
        """The lease itself."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Release on leaving the block."""
        self.release()


def holder_line(lock: Path) -> str:
    """Who holds the lock, for the wait message; a dead holder is reported stale, never named."""
    record = read_record(lock)
    if record is None:
        return "the lock is held (no holder record yet)"
    if not pid_alive(record.get("pid")):
        return f"the lock is held; the holder record (pid {record.get('pid')}) is stale"
    try:
        started = datetime.strptime(str(record.get("started_at")), "%Y-%m-%dT%H:%M:%SZ")
        age = int(
            datetime.now(timezone.utc).timestamp()
            - started.replace(tzinfo=timezone.utc).timestamp()
        )
        held = f"{age // 60}m{age % 60:02d}s"
    except ValueError:
        held = "unknown time"
    return f"held by {record.get('job')} (pid {record.get('pid')}, {record.get('cwd')}, for {held})"


def _memory_short() -> str | None:
    mem, floor = mem_available_mb(), min_mb()
    if mem is not None and mem < floor:
        return f"MemAvailable {mem} MB < {floor} MB"
    return None


def acquire(job: str, wait_s: float | None = None, *, out=None) -> Lease:
    """Take the heavy-job lock for `job` (memory, lock, memory again); raise HostBusy on timeout.

    A valid inherited token returns a re-entrant Lease at once. Messages go to `out` (stderr).
    """
    out = sys.stderr if out is None else out
    lock = lock_path()
    if os.environ.get(TOKEN_VAR):
        if token_valid():
            return Lease(lock, None, os.environ[TOKEN_VAR], job, 0.0)
        if PATH_VAR not in os.environ:  # under the override an inherited token is simply cleared
            print(f"host_gate: ignoring an invalid {TOKEN_VAR}; acquiring", file=out, flush=True)
    wait_s = _env_number("HACF_HEAVY_WAIT_S", DEFAULT_WAIT_S) if wait_s is None else wait_s
    poll = max(0.01, _env_number("HACF_HEAVY_POLL_S", DEFAULT_POLL_S))
    lock.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    last_report: float | None = None
    while True:
        reason = _memory_short()  # (a) memory first, without the lock
        if reason is None:
            fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # (b)
            except BlockingIOError:
                os.close(fd)
                reason = holder_line(lock)
            else:
                reason = _memory_short()  # (c) memory again, holding the lock
                if reason is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                    reason += " right after acquiring; released"
                else:
                    token = secrets.token_hex(16)
                    waited = time.monotonic() - start
                    record = {
                        "schema": SCHEMA,
                        "pid": os.getpid(),
                        "token": token,
                        "job": job,
                        "cwd": os.getcwd(),
                        "started_at": _utc(),
                    }
                    _write_record(lock, record)
                    _log(lock, "acquire", job, waited)
                    return Lease(lock, fd, token, job, waited)
        now = time.monotonic()
        if now - start >= wait_s:
            raise HostBusy(reason)
        if last_report is None or now - last_report >= REPORT_EVERY_S:
            print(f"host_gate: {job} waiting: {reason}", file=out, flush=True)
            last_report = now
        time.sleep(min(poll, max(0.0, wait_s - (now - start))))


# ------------------------------------------------------------------------------------------- run


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run(job: str, argv: list[str], wait_s: float | None = None) -> int:
    """Run argv holding the lock; return its exit code, or 75 when the wait runs out."""
    if token_valid():  # an ancestor holds it: run in place
        os.execvp(argv[0], argv)
    try:
        lease = acquire(job, wait_s)
    except HostBusy as exc:
        print(f"host busy: {exc.reason}; retry later (exit {EXIT_BUSY})", file=sys.stderr)
        return EXIT_BUSY
    try:
        env = {**os.environ, **lease.env()}
        env.pop(HOLDER_PID_VAR, None)
        # The child holds the locked fd too (pass_fds), so a killed host_gate never frees the lock
        # while the job runs; it gets its own process group so a signal can stop all of it.
        child = subprocess.Popen(argv, env=env, pass_fds=(lease.fd,), start_new_session=True)
        received: list[int] = []

        def forward(signum: int, _frame: object) -> None:
            if not received:
                received.append(signum)
                _killpg(child.pid, signal.SIGTERM)

        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, forward)
        deadline: float | None = None
        while True:
            try:
                code = child.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if received:
                    deadline = deadline or time.monotonic() + KILL_GRACE_S
                    if time.monotonic() >= deadline:
                        _killpg(child.pid, signal.SIGKILL)
        if received:  # the rest of the group: the grace period, then SIGKILL
            deadline = deadline or time.monotonic() + KILL_GRACE_S
            while _group_alive(child.pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            _killpg(child.pid, signal.SIGKILL)
        return 128 - code if code < 0 else code
    finally:
        lease.release()


# ---------------------------------------------------------------------------------------- status


def _probe_locked(lock: Path) -> bool | None:
    """Whether some process holds the lock now (a non-blocking probe), None when no file exists."""
    try:
        fd = os.open(lock, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def status() -> dict:
    """The lock, its holder (or a stale record), memory, the budget and other heavy processes."""
    lock = lock_path()
    record = read_record(lock)
    alive = bool(record) and pid_alive(record.get("pid"))
    others = non_cooperating()
    return {
        "schema": "hacf.heavy-job-status/v1",
        "lock": str(lock),
        "locked": _probe_locked(lock),
        "holder_state": "held" if alive else ("stale" if record else "none"),
        "holder": record if alive else None,
        "stale_record": record if record and not alive else None,
        "mem_available_mb": mem_available_mb(),
        "min_mb": min_mb(),
        "wait_s": _env_number("HACF_HEAVY_WAIT_S", DEFAULT_WAIT_S),
        "non_cooperating": others,
        "workers_hint": workers_hint(others) if others else None,
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
