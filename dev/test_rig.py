#!/usr/bin/env python3
"""Run a test against a throwaway rig launched from a copy of a project. A dev tool, not shipped.

    python3 dev/test_rig.py run --project <root> [--seats <pod>.<member>,...]
        [--full --approval <qitem id>] [--keep-copy] -- <test argv>

The run is one job of the host's heavy-job scheduler (`hacf-host.py job run`), declaring
seats x `seat_ram_mb` of RAM. The project is copied to $TMPDIR/hacf-test-rig-<stamp>/, its
openrig/hacf/rig.yaml is cut down there to the chosen seats (default: orch.lead), and the copy is
launched as hacf-test-<project>-<stamp>. Before that, the copy's openrig/hacf/overrides.yml pins
routing question.destination and update.destination to a stub, the first chosen seat of the run's
own rig, and the copy's seat-config.py must resolve both to it, so the rig's questions and updates
reach no seat or person outside it (a `rig send` of a seat is not routed and not guarded here).
The test command runs with HACF_TEST_RIG=<that name>, HACF_TEST_RIG_COPY=<the copy> and
HACF_TEST_RIG_STUB=<the stub>. Then, whatever happened, a `rig up` that did not succeed is stopped,
every open queue row to the stub or from a seat of the run is closed as canceled, the rig is
deleted, its absence verified and the copy removed. The source project is never edited,
and a copy that reaches outside itself (an absolute or escaping symlink, a `.git` file) is refused.
A full-size rig needs --full and --approval, a queue row.

Deleting a rig cannot be undone, so one place calls `rig down --delete`, and only for the name
this run generated and launched (`delete_rig`).

The log of a run is $TMPDIR/hacf-test-rig-<stamp>.log. The heavy lock is held until the cleanup
has finished: `run` keeps the scheduler's `job run` in a session of its own and relays a stop signal
to the job's `_inner` process only (its pid is in hacf-test-rig-<stamp>.inner.pid). A stop while the
job still waits its turn takes effect when it starts. A signal sent to `job run` itself kills its
job's process group 10 s later and frees the lock; the work runs in a group of its own and finishes
its cleanup even then. The log's last line says how that ended.

Exit: the test command's code (128 + signal after a stop signal); 1 when `rig up` failed; 2 on
usage or a refusal; 3 when the rig's deletion could not be verified, the delete was refused, a row
of the run could not be closed or is still open, or the copy could not be removed; 75 when the
scheduler stayed busy.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import yaml

TOOL = Path(__file__).resolve()
HOST_TOOL = TOOL.parents[1] / "openrig" / "hacf" / "bin" / "hacf-host.py"
SPEC_PATH = Path("openrig", "hacf", "rig.yaml")
KIT_PATH = SPEC_PATH.parent
OVERRIDES_PATH = KIT_PATH / "overrides.yml"
RESOLVER_PATH = KIT_PATH / "bin" / "seat-config.py"
ROUTED = ("question", "update")  # the routing destinations pinned to the stub
OPEN_STATES = ("pending", "in-progress", "blocked")
QUEUE_LIMIT = 10000  # a list this long may be short
DEFAULT_SEATS = ("orch.lead",)
JOB = "test-rig"
COPY_PREFIX = "hacf-test-rig-"
# P87: the only names `rig down --delete` may take.
NAME_RE = re.compile(r"hacf-test-[a-z0-9][a-z0-9._-]*-[0-9]{8}T[0-9]{6}")
ROW_RE = re.compile(r"qitem-[A-Za-z0-9][A-Za-z0-9-]*")
STAMP_RE = re.compile(r"[0-9]{8}T[0-9]{6}")
NEEDS_APPROVAL = "a full-size test rig needs TJ's say-so: --approval <qitem id>"
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_CLEANUP = 3
NOT_RUN = 127
TIMED_OUT = 124
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
TEST_GRACE_S = 5.0  # a stopped test command's time between SIGTERM and SIGKILL
READ_TIMEOUT_S = 60.0
UP_TIMEOUT_S = 600.0
UP_GRACE_S = 10.0  # P91: an unfinished or failed `rig up` group's time between SIGTERM and SIGKILL
REAP_S = 5.0  # the wait for a SIGKILLed group to disappear
DOWN_TIMEOUT_S = 300.0

# This run's rig name: set once by claim_run_name, read by `rig up` and by the delete guard.
_RUN_NAME: str | None = None
_LOG: Path | None = None


class Refused(Exception):
    """A usage error or a refusal: nothing is launched, exit 2."""


class Plan(NamedTuple):
    """A run's checked inputs."""

    project: Path
    tmp: Path
    slug: str
    spec: dict
    seats: tuple[str, ...]
    approval: str | None
    keep: bool
    test: tuple[str, ...]
    stamp: str | None = None  # `_inner` only: the run's stamp, from `run`


def say(text: str) -> None:
    """Print a line to stderr and the run's log. Never raises: cleanup must go on."""
    try:
        print(text, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass
    if _LOG is not None:
        try:
            with _LOG.open("a", encoding="utf-8") as log:
                log.write(text + "\n")
        except OSError:
            pass


def note(text: str) -> None:
    """A line of the tool's own."""
    say(f"test_rig: {text}")


def rig(*argv: str, timeout: float = READ_TIMEOUT_S) -> tuple[int, str, str]:
    """Run `rig <argv>` from PATH: (exit code, stdout, stderr); 127 not run, 124 timed out."""
    try:
        done = subprocess.run(
            ["rig", *argv],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            start_new_session=True,  # a signal meant for the run must not cut `up` or `down` short
        )
    except OSError as error:
        return NOT_RUN, "", f"cannot run rig: {error.strerror}"
    except subprocess.TimeoutExpired:
        return TIMED_OUT, "", f"timed out after {timeout:g} s"
    return done.returncode, done.stdout, done.stderr


def _parsed(text: str) -> object | None:
    try:
        return json.loads(text)
    except ValueError:
        return None


# ------------------------------------------------------------------------------------ the inputs


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise Refused(message)


def parse_options(
    argv: Sequence[str], inner: bool = False
) -> tuple[argparse.Namespace, tuple[str, ...]]:
    """Split at the first `--`: the options before it, the test command after it."""
    argv = list(argv)
    cut = argv.index("--") if "--" in argv else len(argv)
    parser = _Parser(prog="test_rig.py run", allow_abbrev=False, description=__doc__)
    parser.add_argument("--project", required=True, help="the project to copy")
    parser.add_argument("--seats", nargs="+", metavar="POD.MEMBER", help="default: orch.lead")
    parser.add_argument("--full", action="store_true", help="every member; needs --approval")
    parser.add_argument("--approval", metavar="QITEM", help="the row that allows --full")
    parser.add_argument("--keep-copy", action="store_true", help="leave the copy under TMPDIR")
    if inner:
        parser.add_argument("--stamp", required=True, help=argparse.SUPPRESS)
    options = parser.parse_args(argv[:cut])
    test = tuple(argv[cut + 1 :])
    if not test or not test[0]:
        raise Refused("needs a test command after --")
    return options, test


def project_slug(basename: str) -> str:
    """The project's part of the rig name (P87)."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", basename.lower())
    return re.sub(r"^[^a-z0-9]+", "", slug)


def _tmpdir(project: Path) -> Path:
    value = os.environ.get("TMPDIR")
    if not value:
        raise Refused("TMPDIR is not set; point it at an on-disk directory (not /tmp)")
    tmp = Path(value).resolve()
    if tmp == Path("/tmp").resolve():
        raise Refused("TMPDIR is /tmp; use an on-disk directory")
    if tmp == project or project in tmp.parents:
        raise Refused(f"TMPDIR {tmp} is inside the project; the copy would hold itself")
    return tmp


def read_spec(path: Path) -> tuple[dict, list[str]]:
    """A rig spec and its member ids, `<pod>.<member>`."""
    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        ids = [f"{pod['id']}.{m['id']}" for pod in spec["pods"] for m in pod["members"]]
    except (OSError, ValueError, yaml.YAMLError, KeyError, TypeError) as error:
        raise Refused(f"cannot read the rig spec {path}: {error!r}") from None
    if not ids:
        raise Refused(f"the rig spec {path} has no member")
    return spec, ids


def check_approval(row: str | None) -> None:
    """A full-size rig needs a queue row that `rig queue show` returns."""
    if not row or not ROW_RE.fullmatch(row):
        raise Refused(NEEDS_APPROVAL)
    rc, out, err = rig("queue", "show", row, "--json")
    shown = _parsed(out)
    if rc != 0 or not isinstance(shown, dict) or shown.get("qitemId") != row:
        note(f"rig queue show {row} --json did not return the row (exit {rc}) {err.strip()}")
        raise Refused(NEEDS_APPROVAL)


def make_plan(argv: Sequence[str], inner: bool = False) -> Plan:
    """Check everything that can be checked before anything is created.

    Raises:
        Refused: A usage error or a refusal.
    """
    options, test = parse_options(argv, inner)
    stamp = options.stamp if inner else None
    if stamp is not None and not STAMP_RE.fullmatch(stamp):
        raise Refused(f"not a run stamp: {stamp!r}")
    project = Path(options.project).resolve()
    tmp = _tmpdir(project)
    spec, ids = read_spec(project / SPEC_PATH)
    slug = project_slug(project.name)
    if not slug:
        raise Refused(f"the project name {project.name!r} gives an empty rig name slug")
    asked = None
    if options.seats is not None:
        asked = [s for word in options.seats for s in word.split(",") if s]
    if options.full:
        if asked is not None:
            raise Refused("--full takes every member; drop --seats")
        seats = tuple(ids)
    else:
        chosen = DEFAULT_SEATS if asked is None else asked
        unknown = [s for s in chosen if s not in ids]
        if unknown or not chosen:
            named = ", ".join(unknown) or "(none)"
            raise Refused(f"no such member: {named}; the spec has {', '.join(ids)}")
        seats = tuple(s for s in ids if s in chosen)
        if len(seats) == len(ids):  # every member by name is full size too
            raise Refused(NEEDS_APPROVAL)
        if options.approval is not None:
            raise Refused("--approval goes with --full")
    if options.full:
        check_approval(options.approval)
    keep = options.keep_copy
    return Plan(project, tmp, slug, spec, seats, options.approval, keep, test, stamp)


# ------------------------------------------------------------------------------------ the job


def _host_tool() -> types.ModuleType:
    """hacf-host.py, loaded by path with no bytecode written (it has no importable name)."""
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("hacf_host", HOST_TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def submit(argv: Sequence[str]) -> int:
    """`run`: check the inputs, then run the scheduler job around `_inner` and wait for it."""
    plan = make_plan(argv)
    config, problems = _host_tool().load_config()
    for problem in problems:
        note(problem)
    ram = len(plan.seats) * config["seat_ram_mb"]
    options = ["--project", str(plan.project)]
    if plan.approval is None:
        options += ["--seats", ",".join(plan.seats)]
    else:
        options += ["--full", "--approval", plan.approval]
    if plan.keep:
        options.append("--keep-copy")
    stamp = utc_stamp()
    pid_file = inner_pid_file(plan.tmp, stamp)
    try:
        plan.tmp.mkdir(parents=True, exist_ok=True)
        pid_file.touch(exist_ok=False)  # two runs never share a stamp
    except OSError as error:
        raise Refused(f"cannot reserve {pid_file}: {error.strerror}; try again") from None
    options += ["--stamp", stamp]
    command = [sys.executable, str(HOST_TOOL), "job", "run", "--job", JOB, "--ram-mb", str(ram)]
    command += ["--", sys.executable, str(TOOL), "_inner", *options, "--", *plan.test]
    try:
        return shielded(command, pid_file, stamp)
    finally:
        pid_file.unlink(missing_ok=True)


def inner_pid_file(tmp: Path, stamp: str) -> Path:
    """Where this run's `_inner` writes its pid, beside the copy."""
    return tmp / f"{COPY_PREFIX}{stamp}.inner.pid"


def inner_pid(pid_file: Path, stamp: str) -> int | None:
    """This run's live `_inner`: the pid in the file, if its command line says so."""
    try:
        pid = int(pid_file.read_text(encoding="ascii"))
        words = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except (OSError, ValueError):
        return None
    return pid if pid > 0 and b"_inner" in words and stamp.encode() in words else None


def shielded(command: list[str], pid_file: Path, stamp: str) -> int:
    """Run `job run` where no stop signal reaches it, and relay one to `_inner` alone (P89).

    `job run` frees the heavy lock 10 s after a stop signal of its own, whatever is still cleaning
    up. So it gets its own session and is never signalled: it releases when `_inner` has exited.
    """
    received: list[int] = []
    for sig in STOP_SIGNALS:
        signal.signal(sig, lambda number, _frame: received.append(number))
    sys.stdout.flush()
    sys.stderr.flush()
    # close_fds=False: an inherited heavy-lock descriptor stays with the job, as under exec.
    job = subprocess.Popen(command, start_new_session=True, close_fds=False)
    relayed = told = False
    while job.poll() is None:
        if received and not relayed:
            pid = inner_pid(pid_file, stamp)
            if pid is not None:
                relayed = True
                try:
                    os.kill(pid, received[0])
                except ProcessLookupError:
                    pass
            elif not told:
                told = True
                note("stop noted; it takes effect when the job starts (the scheduler is waiting)")
        time.sleep(0.05)
    code = job.returncode
    if received and not pid_file.stat().st_size:  # stopped, and `_inner` never got going
        return 128 + received[0]
    return 128 - code if code < 0 else code


# ------------------------------------------------------------------------------------ the rig


def utc_stamp() -> str:
    """The run's stamp."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def claim_run_name(name: str) -> None:
    """Set this run's rig name, once, and only to a name the delete guard accepts.

    Raises:
        Refused: The name fails the pattern, or the run already has one.
    """
    global _RUN_NAME
    if not NAME_RE.fullmatch(name):
        raise Refused(f"the rig name {name!r} is not a test rig name; nothing launched")
    if _RUN_NAME is not None:
        raise Refused(f"this run's rig name is already {_RUN_NAME}")
    _RUN_NAME = name


def trimmed(spec: dict, seats: Sequence[str]) -> dict:
    """The spec with only these members, and only the edges between them."""
    kept = set(seats)
    pods = []
    for pod in spec["pods"]:
        members = [m for m in pod["members"] if f"{pod['id']}.{m['id']}" in kept]
        if not members:
            continue
        own = {m["id"] for m in members}
        edges = [e for e in pod.get("edges") or [] if e["from"] in own and e["to"] in own]
        pods.append({**pod, "members": members, "edges": edges})
    edges = [e for e in spec.get("edges") or [] if e["from"] in kept and e["to"] in kept]
    return {**spec, "pods": pods, "edges": edges}


def outward_path(copy: Path) -> str | None:
    """The first path, in sorted walk order, by which the copy reaches outside itself (P90).

    An absolute symlink, a symlink resolving outside the copy, or a `.git` regular file (a linked
    worktree or submodule, pointing into the source repository). No link is followed by the walk.

    Raises:
        OSError: A directory of the copy cannot be read.
    """

    def unreadable(error: OSError):
        raise error

    root = os.path.realpath(copy)
    found = []
    for top, dirs, files in os.walk(root, onerror=unreadable, followlinks=False):
        for entry in dirs + files:
            path = os.path.join(top, entry)
            if os.path.islink(path):
                target = os.path.realpath(path)
                inside = target == root or target.startswith(root + os.sep)
                outward = os.path.isabs(os.readlink(path)) or not inside
            else:
                outward = entry == ".git" and os.path.isfile(path)
            if outward:
                found.append(Path(path).relative_to(root))
    return str(min(found, key=lambda p: p.parts)) if found else None


def stub_address(seat: str, name: str) -> str:
    """The session address of the run's seat `<pod>.<member>`."""
    return f"{seat.replace('.', '-')}@{name}"


def pin_routing(copy: Path, stub: str) -> None:
    """Pin the copy kit's question and update destinations to the stub; other keys are kept.

    Raises:
        Refused: The copy's overrides.yml, or its routing, cannot be read as a mapping, or the
            file cannot be written.
    """
    path = copy / OVERRIDES_PATH
    try:
        data = {}
        if path.exists() or path.is_symlink():
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("it is not a mapping")
        routing = data.get("routing", {})
        if not isinstance(routing, dict):
            raise ValueError("routing is not a mapping")
        pinned = dict(routing)
        for kind in ROUTED:
            cell = routing.get(kind, {})
            if not isinstance(cell, dict):
                raise ValueError(f"routing.{kind} is not a mapping")
            pinned[kind] = {**cell, "destination": {"session": stub}}
        text = yaml.safe_dump({**data, "routing": pinned}, sort_keys=False, allow_unicode=True)
        path.write_text(text, encoding="utf-8")
    except (OSError, ValueError, RecursionError, yaml.YAMLError) as error:
        raise Refused(f"cannot pin the routing in {path}: {error}; nothing launched") from None


def check_routing(copy: Path, stub: str) -> None:
    """The preflight: the copy's own seat-config.py resolves both destinations to the stub.

    Raises:
        Refused: It did not run, or it resolves either destination to anything else.
    """
    command = [sys.executable, "-B", str(copy / RESOLVER_PATH), "resolve"]
    command += ["--dir", str(copy / KIT_PATH), "--section", "routing", "--json"]
    try:
        done = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=READ_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Refused(f"seat-config.py did not run: {error}; nothing launched") from None
    data = _parsed(done.stdout) if done.returncode == 0 else None
    routing = data.get("routing") if isinstance(data, dict) else None
    for kind in ROUTED:
        cell = routing.get(f"{kind}.destination") if isinstance(routing, dict) else None
        if not isinstance(cell, dict) or cell.get("value") != {"session": stub}:
            said = (done.stderr or done.stdout).strip()[-500:]
            raise Refused(
                f"the copy's routing does not resolve {kind}.destination to {stub} "
                f"(seat-config.py exit {done.returncode}: {said}); nothing launched"
            )


def open_rows(name: str, stub: str) -> tuple[list[dict] | None, str]:
    """The run's open queue rows (P109): to the stub, or from a seat of the run.

    Returns:
        (the rows, "") or (None, why they are unknown).
    """
    rc, out, err = rig(
        "queue", "list", "-A", "--state", ",".join(OPEN_STATES), "--json",
        "--limit", str(QUEUE_LIMIT),
    )  # fmt: skip
    rows = _parsed(out) if rc == 0 else None
    if not isinstance(rows, list) or not all(
        isinstance(r, dict)
        and isinstance(r.get("destinationSession"), str)
        and isinstance(r.get("state"), str)
        for r in rows
    ):
        return None, f"rig queue list gave no row list (exit {rc}) {err.strip()[-500:]}"
    if len(rows) >= QUEUE_LIMIT:
        return None, f"rig queue list returned {len(rows)} rows and may be short"
    mine = [
        r
        for r in rows
        if r["state"] in OPEN_STATES
        and (
            r["destinationSession"] == stub
            or str(r.get("sourceSession") or "").endswith(f"@{name}")
        )
    ]
    if not all(ROW_RE.fullmatch(str(r.get("qitemId"))) for r in mine):
        return None, "a row of the run has no usable id"
    return mine, ""


def close_rows(name: str, stub: str) -> bool:
    """Close the run's open rows before the delete (P109); True when each close succeeded."""
    # P87's guard, as for the delete: only this run's rows. delete_rig reports a refused name.
    if not (NAME_RE.fullmatch(name) and name == _RUN_NAME):
        return False
    rows, why = open_rows(name, stub)
    if rows is None:
        note(f"cannot list the rows of {name}: {why}")
        return False
    closed = True
    for row in rows:
        qitem = row["qitemId"]
        ends = f"{row.get('sourceSession')} -> {row['destinationSession']}"
        rc, out, err = rig(
            "queue", "update", qitem, "--state", "done", "--closure-reason", "canceled",
            "--note", f"closed by dev/test_rig.py: the test rig {name} is being deleted",
        )  # fmt: skip
        if rc == 0:
            note(f"closed row {qitem} ({ends})")
        else:
            closed = False
            note(f"could not close row {qitem} ({ends}): exit {rc} {(err or out).strip()[-500:]}")
    return closed


def no_row_left(name: str, stub: str) -> bool:
    """After the delete: no row of the run is open, so none outlives the rig (P109)."""
    if not (NAME_RE.fullmatch(name) and name == _RUN_NAME):
        return False
    rows, why = open_rows(name, stub)
    if rows is None:
        note(f"could not verify that no row of {name} is open: {why}")
        return False
    if rows:
        note(f"rows still open: {', '.join(r['qitemId'] for r in rows)}")
        return False
    note(f"no row of {name} is open")
    return True


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_group(child: subprocess.Popen, seconds: float) -> bool:
    """Wait for the child's whole process group to be gone, reaping the child; True when it is."""
    deadline = time.monotonic() + seconds
    while child.poll() is None or _group_alive(child.pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def stop_up(up: subprocess.Popen) -> bool:
    """Stop an unfinished or failed `rig up` and wait for it, so no rig registers later (P91)."""
    if up.poll() is not None and not _group_alive(up.pid):
        return True
    note("stopping the rig up process group")
    _kill(up.pid, signal.SIGTERM)
    if _wait_group(up, UP_GRACE_S):
        return True
    _kill(up.pid, signal.SIGKILL)
    up.wait()
    if _wait_group(up, REAP_S):
        return True
    note(f"the rig up process group {up.pid} is still there after SIGKILL")
    return False


def delete_rig(name: str) -> bool:
    """The only `rig down --delete` of this tool: delete this run's rig and verify it is gone."""
    # P87, immediately before the delete: the pattern, and the one name this run launched.
    if not (NAME_RE.fullmatch(name) and name == _RUN_NAME):
        say(f"refusing to delete {name}: not this run's test rig")
        return False
    rc, out, err = rig("down", name, "--delete", "--yes", "--json", timeout=DOWN_TIMEOUT_S)
    if rc != 0:
        note(f"rig down {name} exited {rc}: {(err or out).strip()[-500:]}")
    rc, out, err = rig("ps", "--json")
    rows = _parsed(out) if rc == 0 else None
    if isinstance(rows, list) and all(
        isinstance(r, dict) and isinstance(r.get("name"), str) for r in rows
    ):
        if not any(name in (r["name"], r.get("rigName")) for r in rows):
            note(f"{name} is deleted: rig ps does not list it")
            return True
        why = "rig ps still lists it"
    else:
        why = f"rig ps --json gave no rig list (exit {rc})"
    note(f"could not verify that {name} is deleted: {why}")
    return False


def remove_copy(copy: Path, tmp: Path) -> bool:
    """Remove a copy dir, only when it is a real hacf-test-rig-* dir directly under `tmp`."""
    resolved = copy.resolve()
    if (
        copy.is_symlink()
        or resolved.parent != tmp.resolve()
        or not resolved.name.startswith(COPY_PREFIX)
        or not resolved.is_dir()
    ):
        note(f"not removing {copy}: it is not a {COPY_PREFIX}* directory under {tmp}")
        return False
    try:
        shutil.rmtree(resolved)
    except OSError as error:
        note(f"could not remove the copy {resolved}: {error}")
        return False
    return True


def cleanup(
    name: str | None,
    copy: Path,
    tmp: Path,
    keep: bool,
    up: subprocess.Popen | None = None,
    stub: str | None = None,
) -> int | None:
    """Delete the rig (`name` is None when none was launched) and the copy; 3 on any failure.

    `up` is this run's `rig up`; unless it succeeded, it is stopped before the delete (P91).
    With a `stub`, the run's open rows are closed before the delete and checked after it (P109);
    a row that cannot be closed never holds the delete back.
    """
    stopped = closed = deleted = unrowed = removed = True
    try:
        try:
            if up is not None and up.returncode != 0:
                stopped = stop_up(up)
        finally:
            if name is not None:
                try:
                    if stub is not None:
                        closed = close_rows(name, stub)
                finally:
                    deleted = delete_rig(name)
                if stub is not None:
                    unrowed = no_row_left(name, stub)
    finally:
        if keep:
            note(f"keeping the copy {copy}")
        elif copy.exists() or copy.is_symlink():
            removed = remove_copy(copy, tmp)
    done = stopped and closed and deleted and unrowed and removed
    return None if done else EXIT_CLEANUP


# ------------------------------------------------------------------------------------ the run


def run_test(argv: Sequence[str], name: str, copy: Path, stub: str, received: list[int]) -> int:
    """The test command in its own process group; a stop signal ends the whole group."""
    try:
        child = subprocess.Popen(
            argv,
            env={
                **os.environ,
                "HACF_TEST_RIG": name,
                "HACF_TEST_RIG_COPY": str(copy.resolve()),
                "HACF_TEST_RIG_STUB": stub,
            },
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        note(f"cannot run {argv[0]}: {error.strerror}")
        return NOT_RUN
    deadline = None
    try:
        while child.poll() is None:
            if received and deadline is None:
                _kill(child.pid, signal.SIGTERM)
                deadline = time.monotonic() + TEST_GRACE_S
            if deadline is not None and time.monotonic() >= deadline:
                _kill(child.pid, signal.SIGKILL)
            time.sleep(0.05)
    finally:
        if received or child.poll() is None:  # leave nothing of the command running
            _kill(child.pid, signal.SIGKILL)
            child.wait()
    code = child.returncode
    return 128 - code if code < 0 else code


def _kill(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def wait_up(up: subprocess.Popen, received: list[int]) -> None:
    """Wait for `rig up` until it ends, its time runs out or a stop signal arrives."""
    deadline = time.monotonic() + UP_TIMEOUT_S
    while up.poll() is None and not received and time.monotonic() < deadline:
        time.sleep(0.05)


def execute(plan: Plan, received: list[int]) -> int:
    """Copy, trim, validate, launch, test; then always clean up."""
    global _LOG
    stamp = plan.stamp
    name = f"hacf-test-{plan.slug}-{stamp}"
    claim_run_name(name)  # refused (exit 2) before anything exists
    plan.tmp.mkdir(parents=True, exist_ok=True)
    copy = plan.tmp / f"{COPY_PREFIX}{stamp}"
    try:
        copy.mkdir(mode=0o700)  # fails on an existing dir, which is never ours to remove
    except OSError as error:
        raise Refused(f"cannot create the copy dir {copy}: {error.strerror}") from None
    launched = up = None
    stub = stub_address(plan.seats[0], name)  # Plan.seats is in the spec's order
    keep = plan.keep
    code = EXIT_FAIL
    try:
        _LOG = plan.tmp / f"{copy.name}.log"
        note(f"rig {_RUN_NAME}, copy {copy}, seats {', '.join(plan.seats)}")
        if plan.approval is not None:
            note(f"full-size test rig approved by queue row {plan.approval}")
        try:
            shutil.copytree(plan.project, copy, symlinks=True, dirs_exist_ok=True)
        except (OSError, shutil.Error) as error:
            raise Refused(f"cannot copy {plan.project}: {str(error)[:500]}") from None
        try:
            outward = outward_path(copy)
        except OSError as error:
            keep = False
            raise Refused(f"cannot check the copy: {error}; nothing launched") from None
        if outward is not None:  # P90: refused, and removed even with --keep-copy
            keep = False
            raise Refused(f"refusing a copy that reaches outside itself: {outward}")
        spec_file = copy / SPEC_PATH
        spec_file.write_text(
            yaml.safe_dump(trimmed(plan.spec, plan.seats), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        pin_routing(copy, stub)
        check_routing(copy, stub)
        note(f"stub destination {stub}: the copy's question and update routing resolve to it")
        rc, out, err = rig("spec", "validate", str(spec_file))
        if rc != 0:
            say((out + err).strip()[-2000:])
            raise Refused(f"rig spec validate exited {rc}; nothing launched")
        if not received:
            launched = _RUN_NAME  # from here a rig may exist, whatever `rig up` answers
            command = ["rig", "up", str(spec_file), "--name", _RUN_NAME, "--cwd", str(copy)]
            # A file, not a pipe: a process `up` leaves behind cannot hold the run up.
            with tempfile.TemporaryFile() as output:
                try:
                    up = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,  # its group is what cleanup stops (P91)
                    )
                except OSError as error:
                    note(f"cannot run rig: {error.strerror}")
                else:
                    wait_up(up, received)
                    if up.returncode != 0 and not received:
                        output.seek(0)
                        said = output.read().decode(errors="replace").strip()[-2000:]
                        ended = up.returncode
                        how = f"timed out after {UP_TIMEOUT_S:g} s" if ended is None else (
                            f"exited {ended}"
                        )  # fmt: skip
                        note(f"rig up {how}: {said}")
            if up is not None and up.returncode == 0 and not received:
                note("rig is up; running the test command")
                code = run_test(plan.test, _RUN_NAME, copy, stub, received)
                note(f"the test command exited {code}")
    except Refused as refusal:
        note(str(refusal))
        code = EXIT_USAGE
    finally:
        failed = cleanup(launched, copy, plan.tmp, keep, up, stub)
        if received:
            note(f"stopped by signal {received[0]}")
            code = 128 + received[0]
        code = failed or code
        note(f"cleanup {'FAILED' if failed else 'done'}; exit {code}")
    return code


def _apart(plan: Plan) -> int:
    """Run `execute` in a forked process with a process group of its own, relaying stop signals.

    This process is the `_inner` that `run` signals, and it exits only after the cleanup, so the
    scheduler holds the heavy lock until then. The own group covers a signal sent to `job run`
    itself, which SIGKILLs the job's group soon afterwards.
    """
    received: list[int] = []
    for sig in STOP_SIGNALS:
        signal.signal(sig, lambda number, _frame: received.append(number))
    # Advertised only now that a relayed signal is handled; replaced whole, never read half.
    pid_file = inner_pid_file(plan.tmp, plan.stamp)
    part = pid_file.with_name(f"{pid_file.name}.part")
    part.write_text(str(os.getpid()), encoding="ascii")
    os.replace(part, pid_file)
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        os.setpgid(0, 0)
        return execute(plan, received)
    relayed = False
    while True:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            code = os.waitstatus_to_exitcode(status)
            return 128 - code if code < 0 else code
        if received and not relayed:
            relayed = True
            os.kill(pid, received[0])
        time.sleep(0.05)


def main(argv: Sequence[str] | None = None, *, fork: bool = True) -> int:
    """`run` submits the job; `_inner` is the job."""
    argv = sys.argv[1:] if argv is None else list(argv)
    verb, rest = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if verb == "run":
            return submit(rest)
        if verb == "_inner":
            plan = make_plan(rest, inner=True)
            return _apart(plan) if fork else execute(plan, [])
        if verb in ("-h", "--help"):
            print(__doc__)
            return 0
        raise Refused("usage: test_rig.py run --project <root> [options] -- <test argv>")
    except Refused as refusal:
        note(str(refusal))
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
