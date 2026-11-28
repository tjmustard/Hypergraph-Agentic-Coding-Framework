#!/usr/bin/env python3
"""hacf-host.py: run this machine's HACF teams by one host config and one shared host state.

    hacf-host.py config check [--json]
    hacf-host.py status [--json | --md]
    hacf-host.py boundary <rig> [--json]
    hacf-host.py job run --job <name> [--ram-mb N] [--gpu] [--rig <r>] [--wait-s N] -- argv...
    hacf-host.py job status [--json]
    hacf-host.py pause <rig> --reason <text> [--stop]
    hacf-host.py resume <rig>
    hacf-host.py park <rig>
    hacf-host.py restore <rig>
    hacf-host.py supervise --once [--dry-run]
    hacf-host.py supervisor install [--dry-run-mode] | remove | status
    hacf-host.py idle-point request --purpose <text> [--ttl <hours>]
    hacf-host.py idle-point wait [--timeout <s>] | release | status [--json]

Built so far: the host state, the host config, the priority tiers, the rig → project binding, a
ticket's rig, the one `rig` adapter, `config check`, the cross-rig status view (`status`), the slice
boundary, the writer of a rig's resume point (`rigs/<rig>.resume.md`), the heavy-job scheduler
(`job`), pause, resume, park and restore, auto-park (`supervise`, `supervisor`) and the idle
point (`idle-point`).

`status --json` shows the host from `rig ps --json`, the config and the state files: every rig
that is not stopped, mapped, ignored, in `priority`, recorded or in a paused file, with its HACF
state (running, paused-by-person, parked-waiting, restore-failed, unmapped or ignored; null when
its record cannot be read), what `rig ps` says (`ps_status`), parks in the last 24 h and the last
restore's seconds; the heavy job's holder and queue, the idle point, foreign heavy processes, the
supervisor, the last 20 park and restore lines of `supervisor.log` and MemAvailable. It writes
nothing. `status --md`, or `status` with no form, writes the same as `status.md` and
`status.json` in the state dir and prints the path of status.md. No form calls a model, the
network or a `rig` verb but `ps`.

`boundary` asks whether a rig mapped in `projects` is at a slice boundary, from fresh reads:
B1 every seat is idle, running, not stale and needs nothing; B2 no in-progress queue row is
addressed to the rig; B3 every slice under the project's missions/ has an at-rest hacf_status
(compiled, audited or superseded); B4 the rig holds no heavy-job lock and has no queued job.
Anything that cannot be read or judged is "no", with the reason. It writes nothing.

`job run` runs one heavy job at a time on this user's host. It queues a ticket
(`queue/<ticket>.json`), waits its turn, runs argv holding the lock at HACF_HEAVY_LOCK_PATH, else
${XDG_STATE_HOME:-~/.local/state}/hacf/heavy-job.lock, and exits with argv's code. Tickets go by
priority tier, then enqueue time. While the head does not fit, a later job that fits may run, until
the head has waited reserve_after_minutes; the host is then reserved for it. A ticket needs
MemAvailable >= max(the floor, --ram-mb + reserve_mb); --gpu also waits for foreign_heavy_patterns
and nvidia-smi's compute apps. During an idle point (`idle-point.json`) only a mapped rig that is
not at a boundary is admitted. Inside a job (a valid HACF_HEAVY_LOCK_HELD) argv runs in place. A
wait that runs out is `host busy`, exit 75. dev/scripts/host_gate.py of the HACF repository is a
client of this code.

Environment of `job`: HACF_HOST_MIN_MB (the floor, 6144), HACF_HEAVY_WAIT_S (3600; --wait-s),
HACF_HEAVY_POLL_S (10), HACF_TEST_WORKERS_SHARED (4) and HACF_HOST_MEMINFO (a test seam).

`pause`, `resume`, `park` and `restore` are the person's, or the operator's on the person's word.
They refuse a seat of a mapped rig (by OPENRIG_SESSION_NAME), an unmapped or ignored rig, and run
one at a time per rig. `pause` marks a rig at a slice boundary `paused-by-person`; --stop also
parks it. `park` writes the resume point, takes a snapshot and finds it in `rig snapshot list`,
reads the boundary again, runs `rig down` and checks that every seat stopped. `restore` waits for
MemAvailable >= seats x seat_ram_mb + reserve_mb, then accepts only a `fully_restored` result of
the recorded snapshot; a refusal with no seat running is retried after 2, 4 and 8 minutes. Anything
else marks the rig `restore-failed` and sends one question row to the project's question
destination, with no retry. `resume` restores a stopped rig and clears the mark. A rig named in a
`paused_files` file is paused by the person: this script reads those files and never writes them.

`supervise --once` is one tick of auto-park, off unless `autopark.enabled` is true. It parks a
mapped rig that has been, for `grace_minutes`, at a boundary with a question row it sent the
person and no row for it to act on, under `max_parks_per_day`; the grace counts no earlier than
the running seats' newest `lastActivityAt` in `rig ps`. At most one park per tick, the
lowest tier first. It restores a rig it parked (never a person's park) when a row for it can be
acted on, or every row it waited on is over: at most one per tick, the highest tier first, not
during an idle point, and without waiting for memory. Paused, unmapped and ignored rigs are left
alone, and anything unknown parks nothing. A stop signal marks a cut park or restore
restore-failed and sends the question row. Each decision is a line of `supervisor.log`.
--dry-run evaluates the same, enabled or not, runs no `rig` command that changes anything and
writes only the log and each rig's grace times.

`supervisor install` copies this script to ${XDG_DATA_HOME:-~/.local/share}/hacf/host/bin/ and
writes a systemd user service and timer (every 2 minutes) that run the copy, with this shell's
PATH (seat-bin entries left out) and the host variables that are set; it refuses without a systemd
user session, when `rig` is not on that PATH, and over a newer recorded version. `remove` takes
them away; `status` shows the record, the timer, the unit's PATH and rig, and what the last tick
queued. A seat of a mapped rig never runs supervise, install or
remove.

`idle-point` drains the host before maintenance. `request` writes `idle-point.json`, in force for
--ttl hours (default 6); a second request while one is in force exits 1. `release` removes it, and
`status` shows it; an expired one counts as absent everywhere, and an unreadable one as in force.
`wait` reads every 30 s for at most --timeout seconds (default 3600) until each rig `rig ps` does
not show stopped, `ignore_rigs` aside, is mapped and at a boundary, parked with no seat running, or
paused, and no job holds the heavy lock; an unmapped one is in flight. It exits 0 with `idle
reached`, 75 naming what is still in flight, or 1 when no idle point is in force. No idle-point
verb stops, parks, installs or upgrades anything. A seat of a mapped rig never runs request or
release.

State dir: HACF_HOST_STATE_DIR, else ${XDG_STATE_HOME:-~/.local/state}/hacf/host/
(XDG_STATE_HOME only when absolute). Every project's copy of this script shares it. Each JSON
file carries `"schema": "hacf.host/v1"` and is replaced atomically under a flock on state.lock;
an update keeps the keys it does not know. When any file there declares a newer major schema, this
script writes nothing: a verb that would write exits 75, and `status` prints what it can read.

Config: ${XDG_CONFIG_HOME:-~/.config}/hacf/host.yml (hacf.host-config/v1, every key optional; a
missing file means all defaults). host.example.yml beside the kit lists every key. The file is the
person's: this script never writes it.

A rig's routing is read by `seat-config.py` of its kit, the first of these that holds
bin/seat-config.py: the dir of the one rig spec of its name in `rig specs ls --json` (its live
spec); only when that listing succeeds with no rig spec of the name, $OPENRIG_HOME/specs/<rig>/
(default ~/.openrig) whose rig.yaml names the rig, with no symlink and all owned by this user;
else <project root>/openrig/hacf. With none it is unknown, and so is the routing of a kit older
than v0.6.7. One tick lists the specs once.

`rig` calls use HACF_HOST_RIG, else `rig` from PATH. `config check`, `status`, the boundary, the
resume point, `idle-point wait` and a drain's admission only read (`rig ps`, `rig queue list`).
Park and restore also run `rig snapshot`, `rig down`, `rig up --existing`, `rig restore`,
`rig send`, `rig queue create` and `rig gateway human list`; they and `supervise` also read
`rig specs ls`. `systemctl --user` is run only by
`supervisor` and, for `is-active`, by `status`.

Exit codes: 0 ok; 1 a check that failed (`config check` found a problem, or `boundary` found the
rig not at a boundary, or a pause, resume, park or restore that did not do what it says, or a
supervisor tick that could not read the host or left a rig restore-failed, or a `systemctl` step
of install or remove that failed, or an `idle-point request` over one in force, or a `wait` with
none in force); 2 a usage error, a refusal (such as an unmapped or ignored rig)
or a command error; 75 busy or timeout (newer state, another operation on the rig or another tick, a
person's restore still short of memory, or an `idle-point wait` that timed out), never a verdict.

Python 3.10+, standard library and PyYAML.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import json
import math
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

try:
    import yaml
except ImportError:  # pragma: no cover - the CLI refuses; an importer gets the default config
    yaml = None  # type: ignore[assignment]

STATE_SCHEMA = "hacf.host/v1"
STATE_MAJOR = 1
CONFIG_SCHEMA = "hacf.host-config/v1"
STATUS_SCHEMA = "hacf.host-status/v1"
CHECK_SCHEMA = "hacf.host-config-check/v1"
IDLE_SCHEMA = "hacf.host-idle-point/v1"  # `idle-point status --json`
IDLE_TTL_H = 6.0
IDLE_WAIT_S = 3600
IDLE_POLL_S = 30.0
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_BUSY = 75  # EX_TEMPFAIL: retry later
RIG_NOT_RUN = 127
RIG_TIMED_OUT = 124
RIG_PS_TIMEOUT_S = 60.0
RIG_QUEUE_TIMEOUT_S = 60.0
GIT_TIMEOUT_S = 30.0
QUEUE_LIMIT = 10000
RIG_SNAPSHOT_TIMEOUT_S = 180.0
RIG_DOWN_TIMEOUT_S = 180.0
RIG_SEND_TIMEOUT_S = 60.0
SEAT_CONFIG_TIMEOUT_S = 60.0
SPEC_YAML_LIMIT = 1024 * 1024  # bytes of a specs dir's rig.yaml that are read
# argparse's words when a seat-config.py from before v0.6.7 is asked for the routing section.
OLD_KIT_ERROR = "argument --section: invalid choice: 'routing'"
OLD_KIT_REASON = "kit older than v0.6.7 (no routing section); upgrade the rig's HACF kit"
BODY_LIMIT = 60000  # characters: one argument of `rig queue create` stays under the kernel's limit
RESTORE_MARGIN_S = 60.0  # the adapter waits this much longer than `rig up --timeout`
RESTORE_POLL_S = 10.0
RETRY_DELAYS_S = (120.0, 240.0, 480.0)  # before a refused restore is tried again (R188)
RUNNING = "running"
PAUSED = "paused-by-person"
PARKED = "parked-waiting"
FAILED = "restore-failed"
RIG_STATES = (RUNNING, PAUSED, PARKED, FAILED)
LEAD_LOGICAL_ID = "orch.lead"  # the kit's lead member (R206)
ADVISOR_LOGICAL_ID = "advisor.lead"  # on the kernel rig (startup/protocol.md, discover)
# The source of a row this script sends from outside a seat. Inside one, OpenRig takes the seat.
ROW_SOURCE = "hacf-host@host"
# A person's address, as OpenRig 0.7.23 classes it: only it may carry --human-intent (P74).
HUMAN_ADDRESS_RE = re.compile(r"(human(-[A-Za-z0-9._-]+)?@(kernel|host)|[A-Za-z0-9._:-]+@external)")
# A rig name `rig up` reads as a name: no flag, no path, no spec file.
RIG_NAME_RE = re.compile(r"(?!.*\.(ya?ml|rigbundle|rigtopology)$)[A-Za-z0-9_][A-Za-z0-9._-]*", re.I)
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
# A slice's hacf_status at rest (R197). Every slice status is listed in
# .agents/scripts/hyper_spec.py:103-114 and its transitions in hyper_spec.py:181-189; every other
# value (executing, executed, audit_failed, blocked, an unknown or missing one) is in flight.
AT_REST = frozenset({"compiled", "audited", "superseded"})
# The front matter block, as hyper_openrig.FRONTMATTER_RE reads it.
FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?=\r?\n|$)")
SESSION_RE = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+")
STATE_SCHEMA_RE = re.compile(r"hacf\.host/v([0-9]+)")
HACF_HOST_VERSION = "0.6.8"  # the release bumps it with HACF_VERSION; guards the installed copy
TICK_INTERVAL_S = 120  # the timer's period
TIMEOUT_PARK_S = 300  # TimeoutStartSec's share for a park: its snapshot and `rig down` (P75 a)
TIMEOUT_MARGIN_S = 60
UNIT_NAME = "hacf-host-supervise"
SYSTEMCTL_TIMEOUT_S = 60.0
NEEDS_SYSTEMD = "supervisor needs a systemd user session (systemctl --user)"
# Host variables the unit carries when the installing shell sets them (P82).
UNIT_VARIABLES = (
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "HACF_HOST_STATE_DIR",
    "HACF_HEAVY_LOCK_PATH",
    "HACF_HOST_RIG",
)
SEAT_BIN = "/.openrig/run/seat-bin/"  # a seat's shim dir: its `rig` works only inside that seat
ACTIVE_STATES = "pending,in-progress,blocked"
ACTIONABLE = ("pending", "in-progress")
GRACE_KEYS = ("eligible_since", "eligible_checked")  # P5's times in a rig record
LOG_TAIL_BYTES = 262_144
PARK_LOG_LINES = 20
# A park or a restore that ran, with its seconds: not a queued, deferred or would- line.
PARK_LOG_RE = re.compile(
    r"\S+ (park|restore) (\S+) (done|failed|aborted)(?: seconds=([0-9]+(?:\.[0-9]+)?))?(?: .*)?"
)
UNIT_STATES = ("active", "activating", "deactivating", "failed", "inactive", "reloading")
JOB_SCHEMA = "hacf.heavy-job/v1"  # the holder record beside the lock (R190)
JOB_STATUS_SCHEMA = "hacf.host-job-status/v1"
TOKEN_VAR = "HACF_HEAVY_LOCK_HELD"
HOLDER_PID_VAR = "HACF_HEAVY_LOCK_HOLDER_PID"
LOCK_PATH_VAR = "HACF_HEAVY_LOCK_PATH"
DEFAULT_MIN_MB = 6144
DEFAULT_WAIT_S = 3600
DEFAULT_POLL_S = 10.0
DEFAULT_SHARED_WORKERS = 4
KILL_GRACE_S = 10.0
REPORT_EVERY_S = 60.0
PS_TIMEOUT_S = 30.0
INTERPRETER_RE = re.compile(r"(python[0-9.]*|bash|sh|dash|zsh)\Z")
# The state dir's JSON files, as globs relative to it.
STATE_GLOBS = ("*.json", "queue/*.json", "rigs/*.json")

DEFAULT_CONFIG: dict = {
    "priority": [],
    "projects": {},
    "ignore_rigs": [],
    "reserve_mb": 2048,
    "reserve_after_minutes": 10,
    "seat_ram_mb": 330,
    "foreign_heavy_patterns": [],
    "paused_files": [],
    "human": {"destinations": [], "tags": []},
    "autopark": {
        "enabled": False,
        "grace_minutes": 15,
        "max_parks_per_day": 3,
        "fyi_tags": ["hacf:update"],
        "restore_timeout_s": 900,
    },
}


class StateError(Exception):
    """A state file that must not be overwritten, or a name outside the state dir."""


class NewerSchema(Exception):
    """The host state belongs to a newer HACF; this client writes nothing."""

    def __init__(self, major: int) -> None:
        """Build the line a mutating verb prints."""
        super().__init__(
            f"host state is schema v{major}; this hacf-host is v{STATE_MAJOR}: "
            "newer HACF owns it; waiting"
        )
        self.major = major


# ------------------------------------------------------------------------------------ paths


def _home(env: Mapping[str, str]) -> Path:
    return Path(env.get("HOME") or Path.home())


def _xdg(env: Mapping[str, str], name: str, *fallback: str) -> Path:
    """An XDG base dir: the variable only when absolute, else the fallback under HOME."""
    value = env.get(name, "")
    return Path(value) if os.path.isabs(value) else _home(env).joinpath(*fallback)


def state_dir(env: Mapping[str, str] | None = None) -> Path:
    """The host state dir: HACF_HOST_STATE_DIR, else ${XDG_STATE_HOME}/hacf/host."""
    env = os.environ if env is None else env
    override = env.get("HACF_HOST_STATE_DIR")
    if override:
        return Path(override)
    return _xdg(env, "XDG_STATE_HOME", ".local", "state") / "hacf" / "host"


def config_path(env: Mapping[str, str] | None = None) -> Path:
    """The host config file: ${XDG_CONFIG_HOME:-~/.config}/hacf/host.yml."""
    env = os.environ if env is None else env
    return _xdg(env, "XDG_CONFIG_HOME", ".config") / "hacf" / "host.yml"


# ------------------------------------------------------------------------------------ state


def read_state(path: Path) -> dict | None:
    """The JSON object in `path`, or None when it is absent.

    Raises:
        StateError: The file exists and holds no readable JSON object.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as error:
        raise StateError(f"{path}: unreadable: {_one_line(error)}") from error
    if not isinstance(data, dict):
        raise StateError(f"{path}: not a JSON object")
    return data


def schema_major(data: Mapping) -> int | None:
    """The major version of a state file's `schema`, or None when it is not hacf.host."""
    schema = data.get("schema")
    match = STATE_SCHEMA_RE.fullmatch(schema) if isinstance(schema, str) else None
    return int(match[1]) if match else None


def scan_state(root: Path) -> tuple[dict[str, int], list[str]]:
    """Look at every state file under `root`; nothing is written.

    Returns:
        {relative name: major} of the files with a newer schema, and the relative names of the
        files that cannot be read.
    """
    newer: dict[str, int] = {}
    unreadable: list[str] = []
    for path in sorted({p for pattern in STATE_GLOBS for p in root.glob(pattern)}):
        name = path.relative_to(root).as_posix()
        try:
            data = read_state(path)
        except StateError:
            unreadable.append(name)
            continue
        major = None if data is None else schema_major(data)
        if major is not None and major > STATE_MAJOR:
            newer[name] = major
    return newer, unreadable


def _refuse_newer(root: Path) -> None:
    newer, _ = scan_state(root)
    if newer:
        raise NewerSchema(max(newer.values()))


@contextlib.contextmanager
def state_lock(root: Path) -> Iterator[None]:
    """Hold the exclusive flock on `root`/state.lock, creating the dir when needed.

    Raises:
        NewerSchema: A state file is newer. It is checked before anything is created, and again
            under the lock.
    """
    _refuse_newer(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(root / "state.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _refuse_newer(root)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def _write_atomic(target: Path, text: str) -> None:
    """Replace `target` with `text` through a temp file in the same dir."""
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)  # already gone after a successful replace


def update_state(name: str, mutate: Callable[[dict], object]) -> dict:
    """Read, change and atomically rewrite one state file under the state lock.

    The only way this script writes a JSON state file. `mutate` changes the dict in place, so
    keys it does not touch, unknown ones included, are kept.

    Args:
        name: The file's path relative to the state dir, such as `rigs/<rig>.json`.
        mutate: Changes the current content ({} for a new file).

    Returns:
        The content written.

    Raises:
        NewerSchema: The host state is newer; nothing is written.
        StateError: The name leaves the state dir, or the existing file is not a
            hacf.host/v1 object.
    """
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or not relative.name:
        raise StateError(f"{name}: not a file name inside the state dir")
    root = state_dir()
    target = root / relative
    with state_lock(root):
        current = read_state(target)
        if current is not None and schema_major(current) != STATE_MAJOR:
            raise StateError(f"{target}: not a {STATE_SCHEMA} file; left as it is")
        data = current or {}
        mutate(data)
        data["schema"] = STATE_SCHEMA
        _write_atomic(target, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


# ------------------------------------------------------------------------------------ config


def _one_line(value: object) -> str:
    """`value` as text that stays on one line."""
    text = str(value)
    return text if text.isprintable() else " ".join(text.split()).encode("unicode_escape").decode()


class _Check:
    """The problems found in one config file, one line each."""

    def __init__(self) -> None:
        self.problems: list[str] = []

    def add(self, key: str, reason: str) -> None:
        self.problems.append(f"host.yml: {_one_line(key)}: {_one_line(reason)}")

    def mapping(self, key: str, value: object) -> bool:
        if isinstance(value, dict):
            return True
        self.add(key, "expected a mapping")
        return False

    def strings(self, key: str, value: object) -> list[str] | None:
        """A list of strings, or None after reporting each problem."""
        if not isinstance(value, list):
            self.add(key, "expected a list")
            return None
        bad = [i for i, item in enumerate(value) if not isinstance(item, str)]
        for i in bad:
            self.add(f"{key}[{i}]", "expected a string")
        return None if bad else list(value)

    def integer(self, key: str, value: object, *, positive: bool) -> int | None:
        ok = isinstance(value, int) and not isinstance(value, bool)
        if ok and (value > 0 or not positive):
            return value
        self.add(key, "expected a positive integer" if positive else "expected an integer")
        return None

    def priority(self, value: object) -> list | None:
        """Tiers: each a rig name or a list of rig names, no rig named twice."""
        if not isinstance(value, list):
            self.add("priority", "expected a list")
            return None
        seen: set[str] = set()
        before = len(self.problems)
        for i, tier in enumerate(value):
            nested = isinstance(tier, list)
            for j, name in enumerate(tier if nested else [tier]):
                key = f"priority[{i}][{j}]" if nested else f"priority[{i}]"
                if not isinstance(name, str) or not name:
                    wanted = "a rig name" if nested else "a rig name or a list of rig names"
                    self.add(key, f"expected {wanted}")
                elif name in seen:
                    self.add(key, f"rig {name} is named twice in priority")
                else:
                    seen.add(name)
        return value if len(self.problems) == before else None

    def projects(self, value: object) -> dict[str, str] | None:
        """The rig → project root binding.

        A root that fails a check on disk stays bound: the rig is still mapped, and a later
        reader treats an unreadable root as unknown. An entry of the wrong type is dropped.
        """
        if not self.mapping("projects", value):
            return None
        bound: dict[str, str] = {}
        for rig_name, root in value.items():
            key = f"projects.{rig_name}"
            if not isinstance(rig_name, str) or not rig_name:
                self.add(key, "expected a rig name")
                continue
            if not isinstance(root, str):
                self.add(key, "expected a project root path")
                continue
            bound[rig_name] = root
            if not os.path.isabs(root):
                self.add(key, "the project root is not absolute")
            elif not os.path.isdir(root):
                self.add(key, "the project root does not exist")
            elif not os.path.isdir(os.path.join(root, "missions")):
                self.add(key, "the project root has no missions/ dir")
        return bound

    def patterns(self, value: object) -> list[str] | None:
        items = self.strings("foreign_heavy_patterns", value)
        if items is None:
            return None
        before = len(self.problems)
        for i, pattern in enumerate(items):
            try:
                re.compile(pattern)
            except (re.error, RecursionError, OverflowError) as error:
                self.add(f"foreign_heavy_patterns[{i}]", f"not a regular expression: {error}")
        return items if len(self.problems) == before else None

    def paused_files(self, value: object) -> list[str] | None:
        items = self.strings("paused_files", value)
        if items is None:
            return None
        before = len(self.problems)
        for i, item in enumerate(items):
            if not os.path.isabs(os.path.expanduser(item)):
                self.add(f"paused_files[{i}]", "not an absolute path (after ~ expansion)")
        return items if len(self.problems) == before else None

    def section(self, name: str, value: object, resolved: dict) -> None:
        """Merge the `human` or `autopark` mapping into `resolved`, key by key."""
        if not self.mapping(name, value):
            return
        for key, item in value.items():
            path = f"{name}.{key}"
            if key not in resolved:
                self.add(path, "unknown key")
                continue
            default = DEFAULT_CONFIG[name][key]
            if isinstance(default, bool):
                checked = item if isinstance(item, bool) else None
                if checked is None:
                    self.add(path, "expected true or false")
            elif isinstance(default, int):
                checked = self.integer(path, item, positive=True)
            else:
                checked = self.strings(path, item)
            if checked is not None:
                resolved[key] = checked


def check_config(data: Mapping) -> tuple[dict, list[str]]:
    """Check a parsed host config.

    Returns:
        The config with every key (a default where the file's value is absent or invalid) and one
        `host.yml: <key path>: <reason>` line per problem.
    """
    check = _Check()
    config = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in data.items():
        if key == "schema":
            if value != CONFIG_SCHEMA:
                check.add(key, f"expected {CONFIG_SCHEMA}")
            continue
        if key not in config:
            check.add(str(key), "unknown key")
            continue
        if key in ("human", "autopark"):
            check.section(key, value, config[key])
            continue
        if key == "priority":
            checked = check.priority(value)
        elif key == "projects":
            checked = check.projects(value)
        elif key == "foreign_heavy_patterns":
            checked = check.patterns(value)
        elif key == "paused_files":
            checked = check.paused_files(value)
        elif key == "ignore_rigs":
            checked = check.strings(key, value)
        else:
            checked = check.integer(key, value, positive=key == "reserve_after_minutes")
        if checked is not None:
            config[key] = checked
    for i, name in enumerate(config["ignore_rigs"]):
        if name in config["projects"]:
            check.add(f"ignore_rigs[{i}]", f"rig {name} is in both projects and ignore_rigs")
    return config, check.problems


def load_config(path: Path | None = None) -> tuple[dict, list[str]]:
    """Read and check the host config; a missing or empty file is all defaults."""
    path = config_path() if path is None else path
    if yaml is None:
        return check_config({})[0], ["host.yml: (top level): PyYAML is not installed"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = None
    except OSError as error:
        reason = f"cannot be read: {error.strerror}"
        return check_config({})[0], [f"host.yml: (top level): {_one_line(reason)}"]
    except (yaml.YAMLError, ValueError, RecursionError) as error:
        reason = f"not valid YAML: {error}"
        return check_config({})[0], [f"host.yml: (top level): {_one_line(reason)}"]
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return check_config({})[0], ["host.yml: (top level): expected a mapping"]
    return check_config(data)


# ------------------------------------------------------------------------------- tiers, tickets


def tier_of(rig_name: str | None, priority: Sequence) -> int:
    """The index of the tier holding the rig; an unlisted rig or None ranks below them all."""
    for index, tier in enumerate(priority):
        if rig_name in (tier if isinstance(tier, list) else [tier]):
            return index
    return len(priority)


def job_rig(args: argparse.Namespace, env: Mapping[str, str]) -> str | None:
    """A ticket's rig: --rig, else the part after `@` of OPENRIG_SESSION_NAME, else None."""
    if args.rig:
        return args.rig
    session = env.get("OPENRIG_SESSION_NAME", "")
    return session.split("@")[1] if SESSION_RE.fullmatch(session) else None


# ------------------------------------------------------------------------------- the adapter


def rig(*argv: str, timeout: float) -> tuple[int, str, str]:
    """Run `rig <argv>`: the one place this script calls OpenRig.

    HACF_HOST_RIG names the binary, else `rig` from PATH. The command runs in its own process
    group, and the whole group is killed at the timeout.

    Returns:
        (exit code, stdout, stderr); 127 when it cannot be run, 124 when it timed out.
    """
    command = [os.environ.get("HACF_HOST_RIG") or "rig", *argv]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError as error:
        return RIG_NOT_RUN, "", f"cannot run {command[0]}: {error.strerror}"
    try:
        out, err = process.communicate(timeout=timeout)
        return process.returncode, out, err
    except subprocess.TimeoutExpired:
        _kill_group(process)
        out, err = process.communicate()
        return RIG_TIMED_OUT, out, f"{err}timed out after {timeout:g} s"
    finally:
        if process.poll() is None:  # interrupted: leave nothing running
            _kill_group(process)
            process.wait()


def _kill_group(process: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


def parse_json(text: str) -> object | None:
    """The JSON value in `text`, or None on a parse error."""
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def json_rig(*argv: str, timeout: float) -> object | None:
    """`rig <argv>`'s stdout parsed as JSON, or None on a parse error.

    The exit code is not looked at; a caller that needs it uses `rig` and `parse_json`.
    """
    return parse_json(rig(*argv, timeout=timeout)[1])


def _exit_reason(rc: int, err: str) -> str:
    """A failed `rig` call: its exit code and the last line of its stderr."""
    detail = (err.strip().splitlines() or [""])[-1]
    return f"exit {rc}" + (f": {detail}" if detail else "")


def _rig_objects(*argv: str, timeout: float) -> tuple[list[dict] | None, str]:
    """`rig <argv>`'s stdout as a JSON list of objects: (list, "") or (None, why not)."""
    rc, out, err = rig(*argv, timeout=timeout)
    if rc != 0:
        return None, _exit_reason(rc, err)
    items = parse_json(out)
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        return None, "its output is not a JSON list of objects"
    return items, ""


def rig_statuses() -> tuple[dict[str, list[str]] | None, str]:
    """Each rig `rig ps --json` shows, with the status of every row of that name.

    Returns:
        ({name: statuses}, "") or (None, why the answer is unknown).
    """
    rc, out, err = rig("ps", "--json", timeout=RIG_PS_TIMEOUT_S)
    if rc != 0:
        return None, _exit_reason(rc, err)
    rows = parse_json(out)
    if not isinstance(rows, list) or not all(
        isinstance(r, dict) and isinstance(r.get("name"), str) and isinstance(r.get("status"), str)
        for r in rows
    ):
        return None, "its output is not a list of rigs with a name and a status"
    found: dict[str, list[str]] = {}
    for row in rows:
        found.setdefault(row["name"], []).append(row["status"])
    return found, ""


def _not_stopped(statuses: Mapping[str, list[str]]) -> list[str]:
    return sorted(name for name, seen in statuses.items() if any(s != "stopped" for s in seen))


def running_rigs() -> tuple[list[str] | None, str]:
    """The names of the rigs `rig ps --json` shows running or partly running.

    Returns:
        (names, "") or (None, why the answer is unknown).
    """
    statuses, why = rig_statuses()
    return (None, why) if statuses is None else (_not_stopped(statuses), "")


# ------------------------------------------------------------------------------- boundary


def _bound_root(rig_name: str, config: Mapping) -> str | None:
    """The project root the config binds the rig to; None when unmapped or ignored (R185)."""
    if rig_name in config["ignore_rigs"]:
        return None
    return config["projects"].get(rig_name)


def _unmapped_line(rig_name: str) -> str:
    return f"{_one_line(rig_name)} is not mapped in host.yml projects"


def _result(problems: Sequence[str], at_rest: str) -> dict:
    """One check's answer: ok with `at_rest` as its reason, else every problem."""
    reason = "; ".join(problems) if problems else at_rest
    return {"ok": not problems, "reason": _one_line(reason)}


def _shown(value: object) -> str:
    """A field's value as a reason prints it."""
    return value if isinstance(value, str) else json.dumps(value)


def _seat_problems(node: Mapping) -> list[str]:
    """Why one node of `rig ps --nodes` is not at rest (R205); empty when it is."""
    problems = []
    activity = node.get("agentActivity")
    if not isinstance(activity, dict):
        problems.append("agentActivity missing")
    else:
        if "state" not in activity:
            problems.append("agentActivity.state missing")
        elif activity["state"] != "idle":
            problems.append(f"agentActivity.state {_shown(activity['state'])}")
        # `stale` is optional: OpenRig sets it on a reading that no longer holds.
        if activity.get("stale", False) is not False:
            problems.append(f"agentActivity.stale {_shown(activity['stale'])}")
    for key, wanted in (("needsAttention", False), ("arbitratedNeedsInput", False)):
        if key not in node:
            problems.append(f"{key} missing")
        elif node[key] is not wanted:  # null is "no answer", never false
            problems.append(f"{key} {_shown(node[key])}")
    if "lifecycleState" not in node:
        problems.append("lifecycleState missing")
    elif node["lifecycleState"] != "running":
        problems.append(f"lifecycleState {_shown(node['lifecycleState'])}")
    return problems


def check_seats(rig_name: str) -> dict:
    """B1: every seat of the rig is at rest, from the live `rig ps --nodes` fields."""
    nodes, why = _rig_objects(
        "ps", "--nodes", "--rig", rig_name, "--full", "--json", timeout=RIG_PS_TIMEOUT_S
    )
    if nodes is None:
        return _result([f"unknown: rig ps failed: {why}"], "")
    if not nodes:
        return _result(["unknown: rig ps lists no node"], "")
    problems = []
    for index, node in enumerate(nodes):
        name = node.get("canonicalSessionName") or node.get("logicalId") or f"node {index}"
        problems += [f"{name}: {problem}" for problem in _seat_problems(node)]
    return _result(problems, f"{len(nodes)} seats idle")


def _queue_rows(states: str) -> tuple[list[dict] | None, str]:
    """The host's queue rows in `states`: (rows, "") or (None, why they are unknown).

    A full page is returned with why it may be incomplete.
    """
    rows, why = _rig_objects(
        "queue",
        "list",
        "-A",
        "--state",
        states,
        "--json",
        "--limit",
        str(QUEUE_LIMIT),
        timeout=RIG_QUEUE_TIMEOUT_S,
    )
    if rows is None:
        return None, f"rig queue list failed: {why}"
    if not all(isinstance(row.get("destinationSession"), str) for row in rows):
        return None, "rig queue list failed: a row has no destinationSession"
    if len(rows) >= QUEUE_LIMIT:
        return rows, f"the list reached --limit {QUEUE_LIMIT} and may be incomplete"
    return rows, ""


def _of_rig(session: object, rig_name: str) -> bool:
    return isinstance(session, str) and session.endswith(f"@{rig_name}")


def check_queue(rig_name: str) -> dict:
    """B2: no in-progress row is addressed to a seat of the rig."""
    rows, why = _queue_rows("in-progress")
    problems = [f"unknown: {why}"] if why else []
    busy = [
        str(row.get("qitemId") or "a row without an id")
        for row in rows or []
        if _of_rig(row["destinationSession"], rig_name)
    ]
    if busy:
        problems.insert(0, f"in-progress: {', '.join(busy)}")
    return _result(problems, "no in-progress row is addressed to the rig")


def _cannot_read(error: OSError) -> str:
    if isinstance(error, FileNotFoundError):
        return "cannot be read: no such file"
    return f"cannot be read: {error.strerror}"


def _subdirs(parent: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """The directories in `parent` (hidden names passed over) and the entries it cannot judge.

    Raises:
        OSError: `parent` cannot be listed.
    """
    found: list[Path] = []
    unknown: list[tuple[Path, str]] = []
    for name in sorted(os.listdir(parent)):
        if name.startswith("."):
            continue
        entry = parent / name
        try:
            mode = entry.stat().st_mode
        except OSError as error:  # a dangling link included
            unknown.append((entry, _cannot_read(error)))
            continue
        if stat.S_ISDIR(mode):
            found.append(entry)
    return found, unknown


def _spec_status(spec: Path) -> tuple[str | None, str]:
    """A SPEC.md's front matter `hacf_status`: (status, "") or (None, why it is unknown)."""
    try:
        if not stat.S_ISREG(spec.stat().st_mode):  # a fifo would block the read
            return None, "SPEC.md is not a file"
        text = spec.read_text(encoding="utf-8")
    except OSError as error:
        return None, f"SPEC.md {_cannot_read(error)}"
    except UnicodeDecodeError:
        return None, "SPEC.md cannot be read: not UTF-8"
    match = FRONTMATTER_RE.match(text)
    if match is None:
        return None, "no front matter"
    try:
        data = yaml.safe_load(match[1])
    except Exception:  # PyYAML raises more than YAMLError; every failure means unknown
        return None, "front matter is not valid YAML"
    if not isinstance(data, dict):
        return None, "front matter is not a mapping"
    if "hacf_status" not in data:
        return None, "no hacf_status"
    status = data["hacf_status"]
    if not isinstance(status, str):
        return None, "hacf_status is not a string"
    return status, ""


def slice_statuses(root: str) -> tuple[list[tuple[str, str | None, str]] | None, str]:
    """Every `missions/*/slices/*/SPEC.md` under the bound root, read directly (R185, R197).

    Returns:
        ([(slice dir relative to the root, status or None, why it is unknown)], ""), or
        (None, why the whole project is unknown). A path that cannot be read is listed with a
        status of None, never left out.
    """
    if not os.path.isabs(root):
        return None, f"the project root {root} is not absolute"
    base = Path(root)
    try:
        missions, unknown = _subdirs(base / "missions")
    except OSError as error:
        return None, f"{base / 'missions'} {_cannot_read(error)}"
    found: list[tuple[str, str | None, str]] = []

    def note(path: Path, status: str | None, why: str) -> None:
        found.append((path.relative_to(base).as_posix(), status, why))

    for path, why in unknown:
        note(path, None, why)
    for mission in missions:
        slices = mission / "slices"
        try:
            directories, unknown = _subdirs(slices)
        except FileNotFoundError as error:
            if slices.is_symlink():
                note(slices, None, _cannot_read(error))
            continue  # a mission without slices
        except OSError as error:
            note(slices, None, _cannot_read(error))
            continue
        for path, why in unknown:
            note(path, None, why)
        for directory in directories:
            spec = directory / "SPEC.md"
            try:
                spec.lstat()
            except FileNotFoundError:
                continue  # nothing to read: not a slice yet
            except OSError as error:
                note(directory, None, f"SPEC.md {_cannot_read(error)}")
                continue
            note(directory, *_spec_status(spec))
    return sorted(found, key=lambda item: item[0]), ""


def check_slices(root: str) -> dict:
    """B3: no slice of the rig's bound project is in flight."""
    slices, why = slice_statuses(root)
    if slices is None:
        return _result([f"unknown: {why}"], "")
    problems = [
        f"{name}: {why}" if status is None else f"{name}: hacf_status {status}"
        for name, status, why in slices
        if status not in AT_REST
    ]
    return _result(problems, f"{len(slices)} slices at rest")


def heavy_lock_path(env: Mapping[str, str] | None = None) -> Path:
    """The heavy-job lock, at host_gate.py's path (R190)."""
    env = os.environ if env is None else env
    override = env.get(LOCK_PATH_VAR)
    if override:
        return Path(override)
    return _xdg(env, "XDG_STATE_HOME", ".local", "state") / "hacf" / "heavy-job.lock"


def _usable_pid(pid: object) -> bool:
    return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0


def pid_alive(pid: object) -> bool:
    """Whether `pid` names a running (not zombie) process."""
    if not _usable_pid(pid):
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        return text.rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def _holder_problems(rig_name: str) -> list[str]:
    """The heavy lock's holder record, when it is the rig's or cannot be judged.

    A record without `rig` (an older client's) is attributed to no rig, and one whose process is
    gone holds nothing.
    """
    path = heavy_lock_path().with_suffix(".json")
    try:
        record = read_state(path)
    except StateError as error:
        return [f"unknown: the heavy lock's holder record: {error}"]
    if record is None:
        return []
    holder = record.get("rig")
    if holder is None:
        return []
    if not isinstance(holder, str):
        return [f"unknown: {path}: rig is not a name"]
    if holder != rig_name:
        return []
    pid = record.get("pid")
    if not _usable_pid(pid):
        return [f"unknown: {path}: names the rig with no usable pid"]
    if not pid_alive(pid):
        return []
    return [f"holds the heavy lock: job {record.get('job')} (pid {pid})"]


def _ticket_problems(rig_name: str, root: Path) -> list[str]:
    """The live tickets of the rig in `queue/*.json` (SuperPRD §5.4), and those it cannot judge."""
    queue = root / "queue"
    try:
        names = sorted(os.listdir(queue))
    except FileNotFoundError as error:
        if not queue.is_symlink():
            return []  # nothing has ever been queued
        return [f"unknown: the ticket queue {_cannot_read(error)}"]
    except OSError as error:
        return [f"unknown: the ticket queue {_cannot_read(error)}"]
    problems = []
    for name in names:
        if name.startswith(".") or not name.endswith(".json"):
            continue
        label = f"queue/{name}"
        try:
            ticket = read_state(queue / name)
        except StateError as error:
            problems.append(f"unknown: {error}")
            continue
        if ticket is None:
            if (queue / name).is_symlink():
                problems.append(f"unknown: {label}: unreadable")
            continue  # removed since the listing
        if schema_major(ticket) != STATE_MAJOR:
            problems.append(f"unknown: {label}: not a {STATE_SCHEMA} ticket")
            continue
        owner = ticket.get("rig")
        if owner is None:
            continue
        if not isinstance(owner, str):
            problems.append(f"unknown: {label}: rig is not a name")
        elif owner == rig_name:
            pid = ticket.get("pid")
            if not _usable_pid(pid):
                problems.append(f"unknown: {label}: names the rig with no usable pid")
            elif pid_alive(pid):
                problems.append(f"queued job {ticket.get('job')} (pid {pid}, {label})")
    return problems


def check_heavy(rig_name: str) -> dict:
    """B4: the rig neither holds the heavy-job lock nor has a queued job."""
    root = state_dir()
    newer, _ = scan_state(root)
    problems = []
    if newer:  # a newer HACF may keep its holder and tickets elsewhere
        problems.append(f"unknown: the host state holds a newer schema v{max(newer.values())}")
    problems += _holder_problems(rig_name) + _ticket_problems(rig_name, root)
    return _result(problems, "no heavy lock held and no job queued")


def boundary(rig_name: str, root: str) -> dict:
    """Whether the rig bound to `root` is at a slice boundary (SuperPRD §5.9), read afresh.

    Anything that cannot be read or judged makes its check not ok, with the reason.
    """
    checks = {
        "B1": check_seats(rig_name),
        "B2": check_queue(rig_name),
        "B3": check_slices(root),
        "B4": check_heavy(rig_name),
    }
    at_boundary = all(check["ok"] for check in checks.values())
    return {"rig": rig_name, "at_boundary": at_boundary, "checks": checks}


def _boundary_lines(report: Mapping) -> list[str]:
    """One line per check, then the verdict."""
    lines = [
        f"{name}: {'ok' if check['ok'] else 'no'} - {check['reason']}"
        for name, check in report["checks"].items()
    ]
    return [*lines, f"at boundary: {'yes' if report['at_boundary'] else 'no'}"]


# ------------------------------------------------------------------------------- resume point


def _git_head(root: str) -> str:
    """`git -C <root> rev-parse HEAD`, or `unknown`."""
    if not os.path.isabs(root):
        return "unknown"
    # GIT_DIR and its kin would override -C.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        done = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=GIT_TIMEOUT_S,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    head = done.stdout.strip()
    return head if done.returncode == 0 and head else "unknown"


def _slice_lines(root: str) -> list[str]:
    slices, why = slice_statuses(root)
    if slices is None:
        return [f"- unknown: {why}"]
    lines = []
    for name, status, why in slices:
        value = f"unknown, {why}" if status is None else status
        rest = "at rest" if status in AT_REST else "in flight"
        lines.append(f"- {name}: {value} ({rest})")
    return lines or ["- none"]


def _row_lines(rig_name: str) -> list[str]:
    """The active rows a seat of the rig sent or was sent, from one `rig queue list` call."""
    rows, why = _queue_rows("pending,in-progress,blocked")
    lines = []
    for row in rows or []:
        source, destination = row.get("sourceSession"), row["destinationSession"]
        if not (_of_rig(source, rig_name) or _of_rig(destination, rig_name)):
            continue
        tags = row.get("tags")
        tags = ", ".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
        # Each field is made one line by itself, so an odd one does not escape the others.
        qitem, state, source, destination, summary, tags = map(
            _one_line,
            (
                row.get("qitemId"),
                row.get("state"),
                source,
                destination,
                row.get("summary") or "(no summary)",
                tags or "none",
            ),
        )
        lines.append(f"- {qitem} ({state}) {source} → {destination}: {summary} [tags: {tags}]")
    if why:
        lines.append(f"- unknown: {why}")
    return lines or ["- none"]


def write_resume_point(rig_name: str) -> Path:
    """Write `rigs/<rig>.resume.md`: where the rig's work stood (SuperPRD §5.6, park step 1).

    It holds the UTC time, the project root and its HEAD, each slice with its hacf_status, each
    active queue row sent by or to a seat of the rig, and the boundary checks. What cannot be
    read is written as unknown.

    Returns:
        The file's path.

    Raises:
        NewerSchema: The host state is newer; nothing is written.
        StateError: The rig is not mapped, or its name cannot name a file.
    """
    config, _ = load_config()
    root = _bound_root(rig_name, config)
    if root is None:
        raise StateError(_unmapped_line(rig_name))
    if not rig_name or "/" in rig_name or "\0" in rig_name or rig_name.startswith("."):
        raise StateError(f"{rig_name}: not a rig name a state file can carry")
    state = state_dir()
    _refuse_newer(state)  # before any read; state_lock checks again before the write
    *checks, verdict = _boundary_lines(boundary(rig_name, root))
    written = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# Resume point: {rig_name}",
        "",
        f"- schema: {STATE_SCHEMA}",
        f"- written: {written}",
        f"- project: {root}",
        f"- HEAD: {_git_head(root)}",
        "",
        "## Boundary",
        "",
        *(f"- {line}" for line in (verdict, *checks)),
        "",
        "## Slices",
        "",
        *_slice_lines(root),
        "",
        "## Active queue rows",
        "",
        *_row_lines(rig_name),
    ]
    target = state / "rigs" / f"{rig_name}.resume.md"
    text = "".join(f"{_one_line(line) if line else ''}\n" for line in lines)
    with state_lock(state):
        _write_atomic(target, text)
    return target


# ------------------------------------------------------------------- pause, park, restore: state


class Refused(Exception):
    """A verb that may not act on this rig; the message says why (exit 2)."""


class RigBusy(Exception):
    """Another operation holds the rig (exit 75)."""


class Outcome(NamedTuple):
    """What a park or a restore did.

    `state` is the state it left the rig's record in, or None when it did not change it. `busy`
    marks a restore that never started because it could not get its memory in time.
    """

    verb: str
    rig: str
    ok: bool
    state: str | None
    reason: str
    steps: list[dict]
    busy: bool = False


def pause_for(seconds: float) -> None:
    """The engine's waits go through here."""
    time.sleep(seconds)


def clock() -> float:
    """The monotonic time the engine's bounded waits use."""
    return time.monotonic()


def _step(steps: list[dict], name: str, ok: bool, detail: str, result: object = None) -> bool:
    """Record one step of a park or a restore; returns `ok`.

    `result` is the JSON `rig` answered with, kept for the escalation row.
    """
    steps.append({"step": name, "ok": ok, "detail": _one_line(detail)})
    if result is not None:
        steps[-1]["result"] = result
    return ok


def host_log(verb: str, rig_name: str, detail: str) -> None:
    """Append `<utc> <verb> <rig> <detail>` to supervisor.log; newer state is left alone."""
    root = state_dir()
    line = f"{_utc()} {verb} {rig_name} {_one_line(detail)}\n"
    with contextlib.suppress(NewerSchema, OSError), state_lock(root):
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC
        fd = os.open(root / "supervisor.log", flags, 0o600)
        try:
            os.write(fd, line.encode(errors="replace"))
        finally:
            os.close(fd)


def rig_record(rig_name: str) -> dict:
    """`rigs/<rig>.json`, or {} when the rig has none.

    Raises:
        StateError: The record cannot be read or judged, so nothing may act on the rig.
    """
    path = state_dir() / "rigs" / f"{rig_name}.json"
    data = read_state(path)
    if data is None:
        return {}
    if schema_major(data) != STATE_MAJOR or data.get("state") not in RIG_STATES:
        raise StateError(
            f"{path}: not a {STATE_SCHEMA} rig record (state {_shown(data.get('state'))})"
        )
    return data


def unreadable_records() -> list[str]:
    """The rig records nothing can act on. They are only read: one may be a person's pause."""
    found = []
    for path in sorted((state_dir() / "rigs").glob("*.json")):
        try:
            rig_record(path.stem)
        except StateError:
            found.append(str(path))
    return found


@contextlib.contextmanager
def rig_operation(rig_name: str) -> Iterator[None]:
    """Hold `rigs/<rig>.lock`, so two operations never stop or start one rig at once.

    Raises:
        NewerSchema: The host state is newer; nothing is created.
        RigBusy: Another operation holds the rig.
    """
    root = state_dir()
    _refuse_newer(root)
    (root / "rigs").mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "rigs" / f"{rig_name}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RigBusy(f"{rig_name}: another operation on this rig is running") from None
        yield
    finally:
        os.close(fd)


def paused_in_files(config: Mapping) -> tuple[dict[str, str], list[str]]:
    """The rigs the person's `paused_files` name; the files are only read (R183).

    A line's first word is a rig name; `#` starts a comment.

    Returns:
        ({rig: the file naming it}, one warning per file that cannot be read).
    """
    found: dict[str, str] = {}
    warnings: list[str] = []
    for item in config["paused_files"]:
        path = Path(os.path.expanduser(item))
        try:
            if not stat.S_ISREG(path.stat().st_mode):  # a fifo would block the read
                warnings.append(f"paused file {_one_line(path)} cannot be read: not a file")
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            why = _cannot_read(error) if isinstance(error, OSError) else "cannot be read: not UTF-8"
            warnings.append(f"paused file {_one_line(path)} {why}")
            continue
        for line in text.splitlines():
            words = line.partition("#")[0].split()
            if words:
                found.setdefault(words[0], str(path))
    return found, warnings


def paused_rigs(config: Mapping | None = None) -> tuple[set[str], list[str]]:
    """Every rig the person paused: records marked paused-by-person, and the paused files (R183).

    Returns:
        (rig names, warnings). A warning means the answer may be short: an unreadable file or
        record is never "nothing paused".
    """
    config = load_config()[0] if config is None else config
    listed, warnings = paused_in_files(config)
    names = set(listed)
    for path in sorted((state_dir() / "rigs").glob("*.json")):
        try:
            data = read_state(path)
        except StateError as error:
            warnings.append(str(error))
            continue
        if data is not None and data.get("state") == PAUSED:
            names.add(path.stem)
    return names, warnings


# ------------------------------------------------------------------- pause, park, restore: the rig


def rig_nodes(rig_name: str) -> tuple[list[dict] | None, str]:
    """The rig's nodes: (nodes, "") or (None, why they are unknown). No node is unknown."""
    nodes, why = _rig_objects(
        "ps", "--nodes", "--rig", rig_name, "--full", "--json", timeout=RIG_PS_TIMEOUT_S
    )
    if nodes is None:
        return None, f"rig ps failed: {why}"
    if not nodes:
        return None, "rig ps lists no node"
    if not all(isinstance(node.get("sessionStatus"), (str, type(None))) for node in nodes):
        return None, "rig ps shows a node with an unreadable sessionStatus"
    return nodes, ""


def _running_nodes(nodes: Sequence[Mapping]) -> list[str]:
    return [
        str(node.get("canonicalSessionName") or node.get("logicalId"))
        for node in nodes
        if node.get("sessionStatus") == "running"
    ]


def observe(rig_name: str) -> tuple[str, str]:
    """Whether the rig is up, from fresh reads of its nodes and its rig row (R205).

    Returns:
        ("running" | "stopped" | "partial" | "unknown", detail). Running: every node runs and the
        rig is not `stopped`. Stopped: no node runs and `rig ps` shows the rig `stopped`.
    """
    nodes, why = rig_nodes(rig_name)
    if nodes is None:
        return "unknown", why
    rows, why = _rig_objects("ps", "--json", timeout=RIG_PS_TIMEOUT_S)
    if rows is None:
        return "unknown", f"rig ps failed: {why}"
    statuses = [row.get("status") for row in rows if row.get("name") == rig_name]
    if not statuses:
        return "unknown", "rig ps does not list the rig"
    up = _running_nodes(nodes)
    detail = (
        f"{len(up)} of {len(nodes)} seats running, "
        f"rig status {', '.join(_shown(s) for s in statuses)}"
    )
    down = [status == "stopped" for status in statuses]
    if not up and all(down):
        return "stopped", detail
    if len(up) == len(nodes) and not any(down):
        return "running", detail
    return "partial", detail


def send_lead(rig_name: str, resume_point: object) -> tuple[bool, str]:
    """R206: tell the lead seat its rig is back. One `rig send`, never retried."""
    nodes, why = rig_nodes(rig_name)
    if nodes is None:
        return False, why
    leads = [
        node["canonicalSessionName"]
        for node in nodes
        if node.get("logicalId") == LEAD_LOGICAL_ID
        and isinstance(node.get("canonicalSessionName"), str)
    ]
    if len(leads) != 1:
        return False, f"no one {LEAD_LOGICAL_ID} seat in rig ps"
    message = (
        f"restored from park at {_utc()}; resume point: {resume_point or 'none recorded'}; "
        "re-read your queue"
    )
    rc, _, err = rig("send", leads[0], message, timeout=RIG_SEND_TIMEOUT_S)
    if rc != 0:
        return False, f"rig send {leads[0]}: {_exit_reason(rc, err)}"
    return True, f"sent to {leads[0]}"


# ------------------------------------------------------------------------------- the rig's kit

# `rig specs ls`'s answer while `one_spec_listing` is open, else None: every call reads it.
_spec_memo: list[tuple[list[dict] | None, str]] | None = None


@contextlib.contextmanager
def one_spec_listing() -> Iterator[None]:
    """Read `rig specs ls` at most once inside the block, and forget it afterwards."""
    global _spec_memo
    previous, _spec_memo = _spec_memo, []
    try:
        yield
    finally:
        _spec_memo = previous


def _spec_listing() -> tuple[list[dict] | None, str]:
    """`rig specs ls --json`: (its entries, "") or (None, why not)."""
    if _spec_memo:
        return _spec_memo[0]
    answer = _rig_objects("specs", "ls", "--json", timeout=RIG_PS_TIMEOUT_S)
    if _spec_memo is not None:
        _spec_memo.append(answer)
    return answer


def _live_spec_dir(rig_name: str) -> tuple[Path | None, str, bool]:
    """The dir of the one rig spec of the rig's name.

    Returns:
        (dir, "", False) or (None, what was found, whether the listing succeeded with no rig
        spec of the name).
    """
    listed, why = _spec_listing()
    if listed is None:
        return None, f"rig specs ls failed: {why}", False
    specs = [e for e in listed if e.get("kind") == "rig" and e.get("name") == rig_name]
    if len(specs) != 1:
        count = f"{len(specs)} rig specs" if specs else "no rig spec"
        return None, f"{count} named {rig_name} in rig specs ls", not specs
    path = specs[0].get("sourcePath")
    if not isinstance(path, str):
        path = specs[0].get("resolvedSourcePath")
    # A relative path would be read from this process's directory: a guess.
    if not (isinstance(path, str) and os.path.isabs(path) and os.path.basename(path) == "rig.yaml"):
        return None, f"the rig spec {rig_name} in rig specs ls names no rig.yaml path", False
    return Path(path).parent, "", False


def _specs_dir_fault(spec: Path, rig_name: str) -> str:
    """Why `spec` may not serve as the rig's kit, or "" when every check holds."""
    # Parents before their files: a linked bin/ would run a script from outside the dir.
    for part, is_kind, kind in (
        ("", stat.S_ISDIR, "a real dir"),
        ("rig.yaml", stat.S_ISREG, "a regular file"),
        ("bin", stat.S_ISDIR, "a real dir"),
        ("bin/seat-config.py", stat.S_ISREG, "a regular file"),
    ):
        subject = f"{part} is " if part else ""
        try:
            info = os.lstat(spec / part)
        except FileNotFoundError:
            return f"{subject}not there"
        except OSError as error:
            return f"{part} {_cannot_read(error)}".lstrip()
        if not is_kind(info.st_mode):
            return f"{subject}not {kind}"
        if info.st_uid != os.getuid():
            return f"{subject}not owned by this user"
    try:
        with open(spec / "rig.yaml", "rb") as stream:
            raw = stream.read(SPEC_YAML_LIMIT + 1)
    except OSError as error:
        return f"rig.yaml {_cannot_read(error)}"
    if len(raw) > SPEC_YAML_LIMIT:
        return f"rig.yaml is over {SPEC_YAML_LIMIT} bytes"
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except Exception:  # PyYAML raises more than YAMLError; every failure means unknown
        return "rig.yaml is not valid YAML"
    if not isinstance(data, dict):
        return "rig.yaml is not a mapping"
    name = data.get("name")
    if name != rig_name:
        shown = json.dumps(name) if name is None or isinstance(name, str) else "not a string"
        return f"rig.yaml name is {shown}"
    return ""


def _specs_dir(rig_name: str) -> tuple[Path | None, str]:
    """The rig's dir in OpenRig's specs dir: (dir, "") or (None, which check failed).

    The OpenRig home is $OPENRIG_HOME when set and not empty, else ~/.openrig.
    """
    if not RIG_NAME_RE.fullmatch(rig_name):  # so the name cannot leave the specs dir
        return None, f"specs dir not read: {rig_name!r} is not a rig name"
    home = os.environ.get("OPENRIG_HOME")
    spec = (Path(home) if home else _home(os.environ) / ".openrig") / "specs" / rig_name
    fault = _specs_dir_fault(spec, rig_name)
    if fault:
        return None, f"specs dir {_one_line(spec)}: {fault}"
    return spec, ""


def kit_dir(rig_name: str, root: str) -> tuple[Path | None, str]:
    """The HACF kit a rig's routing is read from.

    An installed rig's kit is its OpenRig live spec, found in the specs dir when the library
    does not list the rig yet; the HACF repository's is `<root>/openrig/hacf`. A kit is a dir
    holding the file bin/seat-config.py.

    Args:
        rig_name: The rig.
        root: Its bound project root.

    Returns:
        (the kit, "") or (None, "no kit for <rig>: <what was found>").
    """
    live, found, unlisted = _live_spec_dir(rig_name)
    if live is not None:
        if os.path.isfile(live / "bin" / "seat-config.py"):
            return live, ""
        found = f"live spec {live} has no bin/seat-config.py"
    elif unlisted:
        spec, why = _specs_dir(rig_name)
        if spec is not None:
            return spec, ""
        found = f"{found}; {why}"
    project = Path(root) / "openrig" / "hacf"
    if os.path.isfile(project / "bin" / "seat-config.py"):
        return project, ""
    return None, f"no kit for {rig_name}: {found}; {project} has none"


# ------------------------------------------------------------------------------- the escalation


def question_routing(
    rig_name: str, root: str, key: str = "question.destination"
) -> tuple[dict, dict]:
    """The rig kit's destination and tags (R199, v0.6.7 routing).

    Args:
        rig_name: The rig.
        root: The bound project root.
        key: `question.destination` or `update.destination`.

    Returns:
        ({"session": address} or {"rule": "discover"}, the tags mapping).

    Raises:
        Refused: The routing cannot be resolved.
    """
    kit, why = kit_dir(rig_name, root)
    if kit is None:
        raise Refused(why)
    command = [
        sys.executable, "-B", str(kit / "bin" / "seat-config.py"),
        "resolve", "--dir", str(kit), "--section", "routing", "--json",
    ]  # fmt: skip
    try:
        done = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=SEAT_CONFIG_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Refused(f"seat-config.py did not run: {_one_line(error)}") from error
    if done.returncode == EXIT_USAGE and OLD_KIT_ERROR in done.stderr:
        raise Refused(OLD_KIT_REASON)  # no shim: an old kit's routing is not read any other way
    if done.returncode != 0:
        raise Refused(f"seat-config.py: {_exit_reason(done.returncode, done.stderr)}")
    data = parse_json(done.stdout)
    routing = data.get("routing") if isinstance(data, dict) else None
    cell = routing.get(key) if isinstance(routing, dict) else None
    value = cell.get("value") if isinstance(cell, dict) else None
    if not isinstance(value, dict):
        raise Refused(f"seat-config.py gave no {key.replace('.', ' ')}")
    tags = routing.get("tags")
    tags = tags.get("value") if isinstance(tags, dict) else None
    return value, tags if isinstance(tags, dict) else {}


def discover_destination() -> str:
    """protocol.md's discovery: the kernel's running advisor, else the one registered person.

    Raises:
        Refused: Either read failed, or there is not exactly one answer; nothing is guessed.
    """
    nodes, why = _rig_objects(
        "ps", "--nodes", "--rig", "kernel", "--full", "--json", timeout=RIG_PS_TIMEOUT_S
    )
    if nodes is None:
        raise Refused(f"discover: rig ps of the kernel failed: {why}")
    advisors = [
        node.get("canonicalSessionName")
        for node in nodes
        if node.get("logicalId") == ADVISOR_LOGICAL_ID and node.get("sessionStatus") == "running"
    ]
    if len(advisors) == 1 and isinstance(advisors[0], str):
        return advisors[0]
    if advisors:
        raise Refused("discover: the advisor's address is not one session")
    rc, out, err = rig("gateway", "human", "list", "--json", timeout=RIG_QUEUE_TIMEOUT_S)
    data = parse_json(out) if rc == 0 else None
    people = data.get("humans") if isinstance(data, dict) else None
    if not isinstance(people, list):
        raise Refused(f"discover: rig gateway human list failed: {_exit_reason(rc, err)}")
    addresses = [p.get("address") for p in people if isinstance(p, dict)]
    if len(addresses) != 1 or not isinstance(addresses[0], str):
        raise Refused(
            f"discover: {len(addresses)} people are registered; set routing question.destination"
        )
    return addresses[0]


def routed_address(value: Mapping, what: str) -> str:
    """The address a routing destination names: its session, or the discovered one (R199).

    Raises:
        Refused: Neither, or discovery failed; nothing is guessed.
    """
    session = value.get("session")
    if isinstance(session, str) and (
        SESSION_RE.fullmatch(session) or HUMAN_ADDRESS_RE.fullmatch(session)
    ):
        return session
    if value.get("rule") == "discover":
        return discover_destination()
    raise Refused(f"unknown {what} destination {_shown(value)}")


def _routing_tags(tags: Mapping, key: str, rig_name: str) -> list[str]:
    """The tags of a row about the rig: the routing tag `key`, rig:<rig>, then routing's extra."""
    extra = tags.get("extra")
    wanted = [str(tags.get(key) or f"hacf:{key}"), f"rig:{rig_name}"]
    return wanted + ([str(tag) for tag in extra] if isinstance(extra, list) else [])


def _create_row(
    destination: str, summary: str, tags: list[str], body: str, point: str, intent: str
) -> str:
    """One `rig queue create`: "" or why it failed.

    Only a person's address may carry --human-intent (P74).
    """
    command = [
        "queue", "create", "--destination", destination,
        "--summary", summary,
        "--tags", ",".join(tags),
        "--body", body[:BODY_LIMIT],
        "--evidence-ref", point,
    ]  # fmt: skip
    if HUMAN_ADDRESS_RE.fullmatch(destination):
        command += ["--human-intent", intent]
    if not os.environ.get("OPENRIG_SESSION_NAME"):
        command += ["--source", ROW_SOURCE]
    rc, _, err = rig(*command, timeout=RIG_QUEUE_TIMEOUT_S)
    return "" if rc == 0 else f"rig queue create to {destination}: {_exit_reason(rc, err)}"


def _resume_point_for(rig_name: str, recorded: object) -> str:
    """The rig's resume-point path, written now when the file is not there."""
    if isinstance(recorded, str) and os.path.isfile(recorded):
        return recorded
    path = state_dir() / "rigs" / f"{rig_name}.resume.md"
    if not path.is_file():
        with contextlib.suppress(NewerSchema, StateError, OSError):
            write_resume_point(rig_name)
    return str(path)


def escalate(
    outcome: Outcome, root: str, resume_point: object, evidence: str | None = None
) -> tuple[bool, str]:
    """Send the one question row about a restore-failed rig (MR 4, R199, P74).

    A person's address gets `--human-intent decision`; an agent seat gets no intent, which OpenRig
    refuses there. The options are in the body. The row's evidence is `evidence` when given (the
    record of a restore that could not start), else the resume point.

    Returns:
        (sent, the destination or why not). Either is also written to supervisor.log.
    """
    rig_name = outcome.rig
    try:
        value, tags = question_routing(rig_name, root)
        destination = routed_address(value, "question")
    except Refused as error:
        host_log("escalate", rig_name, f"not sent: {error}")
        return False, str(error)
    point = _resume_point_for(rig_name, resume_point)
    options = [f"A (recommended): restore it again (restore {rig_name})", "B: leave it stopped"]
    if evidence is not None:
        options = [
            f"A (recommended): read the record ({evidence}), start the rig yourself "
            f"(`rig up {rig_name} --existing`), then clear the mark (resume {rig_name})",
            "B: leave it stopped",
        ]
    result = {
        "verb": outcome.verb,
        "rig": rig_name,
        "state": outcome.state,
        "reason": outcome.reason,
        "steps": outcome.steps,
    }
    body = "\n".join(
        [
            f"The HACF host could not {outcome.verb} the rig {rig_name}: {outcome.reason}",
            f"It is marked {FAILED}. Nothing retries it.",
            "",
            *options,
            "",
            f"Resume point: {point}",
            "",
            "Result:",
            json.dumps(result, indent=2),
        ]
    )
    summary = f"HACF host: restore of {rig_name} needs the person (resume point {point})"
    tags = _routing_tags(tags, "question", rig_name)
    why = _create_row(destination, summary, tags, body, evidence or point, "decision")
    if why:
        host_log("escalate", rig_name, f"not sent: {why}")
        return False, why
    host_log("escalate", rig_name, f"sent to {destination}")
    return True, destination


def _fail(
    verb: str,
    rig_name: str,
    steps: list[dict],
    reason: str,
    snapshot: dict,
    evidence: str | None = None,
) -> Outcome:
    """Mark the rig restore-failed, keep what a later restore needs, and escalate once."""

    def mutate(data: dict) -> None:
        data.update(state=FAILED, since=_utc(), reason=_one_line(reason), snapshot=snapshot)

    record = update_state(f"rigs/{rig_name}.json", mutate)
    outcome = Outcome(verb, rig_name, False, FAILED, _one_line(reason), steps)
    root = _bound_root(rig_name, load_config()[0]) or ""
    sent, detail = escalate(outcome, root, record.get("resume_point"), evidence=evidence)
    _step(steps, "escalation", sent, f"sent to {detail}" if sent else f"not sent: {detail}")
    return outcome


def _mark_interrupted(rig_name: str, verb: str, snapshot: dict) -> None:
    """An operation stopped after it may have changed the rig: it is neither up nor parked."""

    def mutate(data: dict) -> None:
        reason = f"{verb} was interrupted"
        data.update(state=FAILED, since=_utc(), reason=reason, snapshot=snapshot)

    with contextlib.suppress(NewerSchema, StateError, OSError):
        update_state(f"rigs/{rig_name}.json", mutate)


# ------------------------------------------------------------------------------- the park engine


def _snapshot_verified(rig_name: str, steps: list[dict]) -> str | None:
    """Take a manual snapshot and find it, usable, in `rig snapshot list`; its id or None."""
    rc, out, err = rig("snapshot", rig_name, timeout=RIG_SNAPSHOT_TIMEOUT_S)
    created = re.search(r"^Snapshot created: (\S+)", out, re.M)
    if rc != 0 or created is None:
        why = _exit_reason(rc, err) if rc != 0 else "no `Snapshot created:` line"
        _step(steps, "snapshot", False, f"snapshot not verified: rig snapshot: {why}")
        return None
    snapshot = created[1]
    rc, out, err = rig("snapshot", "list", rig_name, timeout=RIG_SNAPSHOT_TIMEOUT_S)
    rows = [line.split() for line in out.splitlines()]
    if rc != 0:
        why = f"rig snapshot list: {_exit_reason(rc, err)}"
    elif ["ID", "Kind", "Status", "Created"] not in rows:
        why = "rig snapshot list printed no table"
    else:
        found = [row[2] for row in rows if len(row) >= 4 and row[0] == snapshot]
        if len(found) == 1 and found[0] not in ("failed", "unusable"):
            _step(steps, "snapshot", True, f"{snapshot} is {found[0]}")
            return snapshot
        why = f"{snapshot} is {found[0] if len(found) == 1 else 'not listed'}"
    _step(steps, "snapshot", False, f"snapshot not verified: {why}")
    return None


def _recheck(rig_name: str, root: str, by: str, p3: Callable[[], str] | None) -> str:
    """Why `rig down` must not run after all (R191), or "" when everything still holds."""
    report = boundary(rig_name, root)
    if not report["at_boundary"]:
        failed = [f"{k}: {v['reason']}" for k, v in report["checks"].items() if not v["ok"]]
        return "no longer at a boundary: " + "; ".join(failed)
    if by == "supervisor":
        if p3 is None:
            return "no P3 check was given"
        changed = p3()
        if changed:
            return changed
    listed, warnings = paused_in_files(load_config()[0])
    if warnings:
        return "; ".join(warnings)
    if rig_name in listed:
        return f"{rig_name} is listed in {listed[rig_name]}"
    return ""


def park_rig(
    rig_name: str,
    by: str,
    reason: str,
    *,
    p3: Callable[[], str] | None = None,
    waits_on: Sequence[str] = (),
) -> Outcome:
    """Stop a rig that is at a boundary, so it can be brought back (SuperPRD §5.6, steps 1-6).

    The caller sends any FYI row. A failure before `rig down` leaves the rig running; a stop that
    cannot be verified marks it restore-failed and escalates.

    Args:
        rig_name: A mapped rig. The caller has applied the refusals and holds rig_operation().
        by: Who parks it; "supervisor" must give `p3`.
        reason: Why, for the record.
        p3: The supervisor's re-check that the rig still waits on the same rows: "" or why not.
        waits_on: The rows the supervisor's park waits on.

    Raises:
        NewerSchema: The host state is newer.
        StateError: The rig is not mapped, or its record cannot be used.
    """
    steps: list[dict] = []
    rig_record(rig_name)
    point = str(write_resume_point(rig_name))
    _step(steps, "resume point", True, point)

    def aborted(why: str) -> Outcome:
        return Outcome("park", rig_name, False, None, why, steps)

    manual = _snapshot_verified(rig_name, steps)
    if manual is None:
        return aborted(steps[-1]["detail"])
    root = _bound_root(rig_name, load_config()[0])
    why = "the rig is no longer mapped" if root is None else _recheck(rig_name, root, by, p3)
    if not _step(steps, "re-check", not why, why or "still at a boundary"):
        return aborted(why)
    snapshot: dict = {"manual": manual, "down": None}
    try:
        rc, out, err = rig("down", rig_name, "--json", timeout=RIG_DOWN_TIMEOUT_S)
        data = parse_json(out)
        data = data if isinstance(data, dict) else {}
        if data.get("errors") == [] and isinstance(data.get("snapshotId"), str):
            snapshot["down"] = data["snapshotId"]
        errors = data.get("errors") or data.get("error")
        told = f"errors: {_shown(errors)}" if errors else f"down snapshot {snapshot['down']}"
        _step(steps, "rig down", rc == 0, f"{_exit_reason(rc, err)}; {told}", data or None)
        seen, detail = observe(rig_name)
        if not _step(steps, "verify", seen == "stopped", detail):
            return _fail(
                "park",
                rig_name,
                steps,
                f"the stop is not verified ({detail})",
                snapshot,
            )

        def mutate(record: dict) -> None:
            record.update(resume_point=point, snapshot=snapshot, waits_on=list(waits_on))
            if record.get("state") != PAUSED:  # `pause --stop`: it stays the person's pause
                record.update(state=PARKED, by=by, reason=reason, since=_utc())

        state = update_state(f"rigs/{rig_name}.json", mutate)["state"]
    except BaseException:
        _mark_interrupted(rig_name, "park", snapshot)
        raise
    _step(steps, "record", True, state)
    return Outcome("park", rig_name, True, state, reason, steps)


# ------------------------------------------------------------------------------- the restore engine


def _wait_for_ram(rig_name: str, wait_s: float, config: Mapping, steps: list[dict]) -> bool:
    """R203, R204: wait until the host has the rig's memory; no other rig is parked for it."""
    deadline = clock() + wait_s
    reported = ""
    while True:
        nodes, why = rig_nodes(rig_name)
        if nodes is not None:
            need = len(nodes) * config["seat_ram_mb"] + config["reserve_mb"]
            available = mem_available_mb()
            if available is None or available >= need:
                have = "unknown" if available is None else f"{available} MB"
                return _step(steps, "memory", True, f"{have}, needs {need} MB")
            why = f"MemAvailable {available} MB < {need} MB"
        if why != reported:
            print(f"restore {rig_name} waiting: {_one_line(why)}", flush=True)
            reported = why
        left = deadline - clock()
        if left <= 0:
            return _step(steps, "memory", False, f"gave up waiting: {why}")
        pause_for(min(RESTORE_POLL_S, left))


def _up_existing(rig_name: str, down: str, timeout: int) -> tuple[bool, bool, str, object]:
    """`rig up --existing` once: (verified, refused before a launch, detail, its JSON)."""
    rc, out, err = rig(
        "up", rig_name, "--existing", "--json", "--timeout", str(timeout),
        timeout=timeout + RESTORE_MARGIN_S,
    )  # fmt: skip
    data = parse_json(out)
    if not isinstance(data, dict):
        return False, rc == 2, f"rig up: {_exit_reason(rc, err or out)}; no JSON result", None
    result = data.get("rigResult")
    # OpenRig 0.7.23 and 0.7.24 name the snapshot only here, at the top level (P108).
    selected = data.get("snapshotId")
    detail = (
        f"rig up: exit {rc}, rigResult {_shown(result)}, snapshot {_shown(selected)} "
        f"kind {_shown(data.get('snapshotKind'))} (expected {down})"
    )
    if data.get("code") or data.get("error"):
        detail += f", {_shown(data.get('code'))}: {_shown(data.get('error'))}"
    if rc == 0 and result == "fully_restored" and selected == down:
        return True, False, detail, data
    # A 409 is exit 1: its body carries a code and no result of a launch (R188).
    conflict = rc == 1 and bool(data.get("code")) and result in (None, "not_attempted")
    return False, rc == 2 or conflict, detail, data


def _restore_manual(rig_name: str, manual: str, timeout: int) -> tuple[bool, bool, str, object]:
    """`rig restore <id>` once, polled to its end.

    Returns:
        (verified, refused before a launch, detail, the finished attempt's JSON or None).
    """
    rc, out, err = rig("restore", manual, "--rig", rig_name, timeout=timeout)
    attempt = re.search(r"^Restore attempt id: (\S+)", out, re.M)
    if rc != 0 or attempt is None:
        why = _exit_reason(rc, err) if rc != 0 else "no `Restore attempt id:` line"
        return False, rc == 2, f"rig restore {manual}: {why}", None
    deadline = clock() + timeout
    while True:
        rc, out, err = rig(
            "restore", "status", attempt[1], "--rig", rig_name, "--json",
            timeout=RIG_PS_TIMEOUT_S,
        )  # fmt: skip
        if rc == 0:  # a finished attempt; until then the command fails
            data = parse_json(out)
            original = data.get("originalResult") if isinstance(data, dict) else None
            result = original.get("rigResult") if isinstance(original, dict) else None
            detail = f"restore attempt {attempt[1]}: rigResult {_shown(result)}"
            return result == "fully_restored", False, detail, data
        left = deadline - clock()
        if left <= 0:
            why = _exit_reason(rc, err)
            return False, False, f"restore attempt {attempt[1]}: timed out; last: {why}", None
        pause_for(min(RESTORE_POLL_S, left))


def restore_rig(rig_name: str, ram_wait_s: float | None = None) -> Outcome:
    """Bring a stopped rig back from its recorded snapshot, accepting only a whole restore (R188).

    With the `rig down` snapshot: `rig up --existing`, which must select it. Without: `rig restore`
    of the manual one. A refusal with no seat running is retried after 2, 4 and 8 minutes.
    Everything else short of `fully_restored` marks restore-failed and escalates, with no retry.
    On success the lead seat is told, then the record says running.

    Args:
        rig_name: A mapped rig. The caller has applied the refusals and holds rig_operation().
        ram_wait_s: The longest wait for memory; None means `autopark.restore_timeout_s`. The
            supervisor gives 0, so that a tick stays within its unit's time limit (P81).

    Raises:
        NewerSchema: The host state is newer.
        StateError: The rig's record cannot be used.
    """
    steps: list[dict] = []
    record = rig_record(rig_name)
    held = record.get("snapshot")
    held = held if isinstance(held, dict) else {}
    down = held.get("down") if isinstance(held.get("down"), str) else None
    manual = held.get("manual") if isinstance(held.get("manual"), str) else None
    if down is None and manual is None:
        why = "no recorded snapshot to restore"
        _step(steps, "snapshot", False, why)
        return Outcome("restore", rig_name, False, None, why, steps)
    config = load_config()[0]
    wait_s = config["autopark"]["restore_timeout_s"] if ram_wait_s is None else ram_wait_s
    if not _wait_for_ram(rig_name, wait_s, config, steps):
        return Outcome("restore", rig_name, False, None, steps[-1]["detail"], steps, busy=True)
    _refuse_newer(state_dir())  # the wait may have been long
    timeout = config["autopark"]["restore_timeout_s"]
    delays = list(RETRY_DELAYS_S)
    try:
        while True:
            if down is not None:
                ok, refused, detail, data = _up_existing(rig_name, down, timeout)
            else:
                ok, refused, detail, data = _restore_manual(rig_name, str(manual), timeout)
            _step(steps, "restore", ok, detail, data)
            if ok or not refused or not delays:
                break
            delay = delays.pop(0)
            if not _nothing_runs(rig_name, steps):
                break
            print(f"restore {rig_name} refused; retrying in {delay:g} s", flush=True)
            pause_for(delay)
            if not _nothing_runs(rig_name, steps):
                break
        if not ok:
            return _fail("restore", rig_name, steps, detail, held)
        sent, told = send_lead(rig_name, record.get("resume_point"))
        _step(steps, "lead message", sent, told if sent else f"failed: {told}")
        mark_running(rig_name)
    except BaseException:
        _mark_interrupted(rig_name, "restore", held)
        raise
    return Outcome("restore", rig_name, True, RUNNING, detail, steps)


def _nothing_runs(rig_name: str, steps: list[dict]) -> bool:
    """Whether `rig ps` shows that no seat of the rig runs; unknown is not that."""
    nodes, why = rig_nodes(rig_name)
    if nodes is not None:
        up = _running_nodes(nodes)
        why = f"running: {', '.join(up)}" if up else ""
    if why:
        _step(steps, "retry", False, f"not retried: {why}")
    return not why


def unusable(record: Mapping) -> str:
    """Why a supervisor's park cannot be restored from its record (P83, P85), or ""."""
    held = record.get("snapshot")
    held = held if isinstance(held, dict) else {}
    if not any(isinstance(held.get(k), str) and held[k] for k in ("down", "manual")):
        return "no snapshot id"
    parks = record.get("parks")
    if not isinstance(parks, list) or not parks:
        return "no parks entry"
    return ""


def fail_unusable(rig_name: str, why: str) -> Outcome:
    """Mark a rig whose restore cannot start restore-failed and ask once. Nothing is launched.

    Args:
        rig_name: A mapped rig with a readable record. The caller holds rig_operation().
        why: What `unusable` found.
    """
    held = rig_record(rig_name).get("snapshot")
    held = held if isinstance(held, dict) else {}
    record = str(state_dir() / "rigs" / f"{rig_name}.json")
    reason = f"it cannot start: the record is unusable: {why}"
    try:
        return _fail("restore", rig_name, [], reason, held, evidence=record)
    except BaseException:
        _mark_interrupted(rig_name, "restore", held)  # the caller then still asks (P81)
        raise


def mark_running(rig_name: str) -> None:
    """Clear a pause or a park: the record says running and holds no snapshot to restore."""

    def mutate(data: dict) -> None:
        data.update(state=RUNNING, since=_utc(), waits_on=[])
        for key in ("by", "reason", "snapshot", *GRACE_KEYS):
            data.pop(key, None)

    update_state(f"rigs/{rig_name}.json", mutate)


# ------------------------------------------------------------------- the heavy job: processes


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def min_mb() -> int:
    """The MemAvailable floor in MB (HACF_HOST_MIN_MB, default 6144)."""
    return int(_env_number("HACF_HOST_MIN_MB", DEFAULT_MIN_MB))


def mem_available_mb() -> int | None:
    """MemAvailable in MB, or None when the host has no readable figure (RAM isn't checked)."""
    source = os.environ.get("HACF_HOST_MEMINFO") or "/proc/meminfo"
    try:
        text = Path(source).read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    match = re.search(r"^MemAvailable:\s+(\d+)", text, re.M)
    return int(match[1]) // 1024 if match else None


def utcnow() -> datetime:
    """The UTC time the supervisor's grace and park cap use."""
    return datetime.now(timezone.utc)


def _utc() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _ppid(pid: int) -> int | None:
    """The parent of `pid` (from /proc, else ps), or None when it cannot be read."""
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
        return int(stat_text.rsplit(")", 1)[1].split()[1])
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
    """This process and its ancestors: the shell that started the job is never "another run"."""
    return {str(pid) for pid in _ancestors(os.getpid())}


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
    record = read_record(heavy_lock_path())
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


# ---------------------------------------------------------------- the heavy job: record, token


def record_path(lock: Path) -> Path:
    """The holder record beside the lock (`heavy-job.json`)."""
    return lock.with_suffix(".json")


def log_path(lock: Path) -> Path:
    """The append-only acquire/release log beside the lock (`heavy-job.log`)."""
    return lock.with_suffix(".log")


def read_record(lock: Path) -> dict | None:
    """The holder record beside `lock`, or None when it is absent or unreadable."""
    try:
        data = json.loads(record_path(lock).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == JOB_SCHEMA else None


def _write_record(lock: Path, record: dict) -> None:
    """Write the holder record atomically (temp file + rename), only while the lock is held."""
    target = record_path(lock)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _log(lease: Lease, event: str, seconds: float) -> None:
    """Append one line: UTC time, event, pid, job, seconds waited (or held), rig and needs."""
    what = "waited" if event == "acquire" else "held"
    line = (
        f"{_utc()} {event} pid={os.getpid()} job={lease.job} {what}={seconds:.1f}s "
        f"rig={lease.rig or '-'} needs={lease.ram_mb}/{int(lease.gpu)}\n"
    )
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC
    fd = os.open(log_path(lease.lock), flags, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def token_valid(env: Mapping[str, str] | None = None) -> bool:
    """Whether `env` (default os.environ) holds a valid re-entrancy token for the current lock.

    Valid: HACF_HEAVY_LOCK_HELD equals the record's token, the record's pid is alive, and that pid
    is this process or an ancestor (or equals HACF_HEAVY_LOCK_HOLDER_PID when that is set).
    """
    env = os.environ if env is None else env
    token = env.get(TOKEN_VAR)
    if not token:
        return False
    record = read_record(heavy_lock_path())
    if not record or not secrets.compare_digest(str(record.get("token", "")), token):
        return False
    pid = record.get("pid")
    if not pid_alive(pid):
        return False
    named = env.get(HOLDER_PID_VAR)
    if named:
        return named == str(pid)
    return pid in _ancestors(os.getpid())


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


# ---------------------------------------------------------------------- the heavy job: tickets


def _parse_utc(value: object) -> datetime | None:
    """An ISO time ending in Z (or with an offset) as an aware datetime, else None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class Ticket(NamedTuple):
    """One live `queue/<name>`: a job waiting for the lock, or holding it."""

    name: str
    job: str
    rig: str | None
    pid: int
    tier: int
    ram_mb: int
    gpu: bool
    enqueued: datetime
    blocked: str | None  # why its owner says it cannot be admitted: "ram", "gpu", "drain"

    @property
    def order(self) -> tuple[int, datetime, str]:
        """The admission order: tier, then enqueue time (the name breaks a tie)."""
        return (self.tier, self.enqueued, self.name)

    @property
    def label(self) -> str:
        """`<job> (<rig>, tier N)`."""
        return f"{_one_line(self.job)} ({_one_line(self.rig or 'no rig')}, tier {self.tier})"


def _ticket(name: str, data: Mapping) -> Ticket | None:
    """The ticket in `data`, or None when a field it is ordered by is missing or of a wrong type."""
    needs = data.get("needs")
    enqueued = _parse_utc(data.get("enqueued_at"))
    owner, blocked = data.get("rig"), data.get("blocked")
    if (
        schema_major(data) != STATE_MAJOR
        or not isinstance(data.get("job"), str)
        or not (owner is None or isinstance(owner, str))
        or not _is_count(data.get("tier"))
        or not isinstance(needs, dict)
        or not _is_count(needs.get("ram_mb"))
        or not isinstance(needs.get("gpu"), bool)
        or enqueued is None
    ):
        return None
    kind = blocked.get("kind") if isinstance(blocked, dict) else None
    return Ticket(
        name, data["job"], owner, data["pid"], data["tier"], needs["ram_mb"], needs["gpu"],
        enqueued, kind if isinstance(kind, str) else None,
    )  # fmt: skip


def live_tickets(root: Path, prune: bool = True) -> list[Ticket]:
    """The tickets whose process is alive, in admission order.

    A ticket whose process is gone is deleted here, by whoever sees it, unless the host state is
    newer or `prune` is False (`status` writes nothing). A file that cannot be read or judged is
    passed over.
    """
    found, dead = [], []
    for path in sorted((root / "queue").glob("*.json")):
        try:
            data = read_state(path)
        except StateError:
            continue
        if data is None or schema_major(data) != STATE_MAJOR or not _usable_pid(data.get("pid")):
            continue
        if not pid_alive(data["pid"]):
            dead.append(path)
            continue
        ticket = _ticket(path.name, data)
        if ticket is not None:
            found.append(ticket)
    if dead and prune:
        with contextlib.suppress(NewerSchema, OSError), state_lock(root):
            for path in dead:
                path.unlink(missing_ok=True)
    return sorted(found, key=lambda t: t.order)


def _holding(lock: Path) -> dict | None:
    """The record of a live holder, else None."""
    record = read_record(lock)
    return record if record and pid_alive(record.get("pid")) else None


def waiting_tickets(root: Path, lock: Path, prune: bool = True) -> list[Ticket]:
    """The live tickets that are not the holder's, in admission order."""
    holder = _holding(lock)
    held = holder.get("ticket") if holder else None
    return [t for t in live_tickets(root, prune) if t.name != held]


# -------------------------------------------------------------------- the heavy job: admission


def _expired(point: Mapping) -> bool:
    """Whether an idle point's `expires_at` has passed; without a usable one it has not."""
    expires = _parse_utc(point.get("expires_at"))
    return expires is not None and expires <= utcnow()


def idle_point(root: Path) -> dict | None:
    """The idle point in force (`idle-point.json` before its `expires_at`), else None.

    A file that cannot be read or judged is in force: unknown never admits a ticket a drain holds.
    """
    path = root / "idle-point.json"
    try:
        data = read_state(path)
    except StateError as error:
        return {"purpose": f"unknown: {error}"}
    if data is None or _expired(data):
        return None
    purpose = data.get("purpose")
    return {**data, "purpose": purpose if isinstance(purpose, str) else "unknown"}


def _in_flight(rig_name: str | None, config: Mapping) -> bool:
    """Whether a drain admits the rig: mapped, and not at a boundary (R187).

    B1-B3 only. B4 asks whether the rig holds or queues a heavy job, which the asking ticket
    itself does, so counting it would put every rig in flight.
    """
    root = None if rig_name is None else _bound_root(rig_name, config)
    if root is None:
        return False
    checks = (lambda: check_seats(rig_name), lambda: check_queue(rig_name),
              lambda: check_slices(root))  # fmt: skip
    return not all(check()["ok"] for check in checks)


def _needed_mb(ram_mb: int, config: Mapping) -> int:
    """R189: MemAvailable must reach max(the floor, ram_mb + reserve_mb)."""
    return max(min_mb(), ram_mb + config["reserve_mb"])


def _ps_args() -> dict[int, str] | None:
    """Each pid → args of every process on the host (`ps -eo pid=,args=`), None when ps fails."""
    try:
        done = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=PS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    found = {}
    for line in done.stdout.splitlines():
        pid, _, args = line.strip().partition(" ")
        if pid.isdigit():
            found[int(pid)] = args.strip()
    return found


def foreign_heavy(
    patterns: Sequence[str], root: Path, lock: Path, prune: bool = True
) -> list[str] | None:
    """`<pid> <args>` of each process matching a foreign_heavy_patterns regex (D3).

    HACF's own are never foreign: this process, every ticket's process and their ancestors (a
    waiting job's command line names what it will run), and the holder with its descendants.

    Returns:
        The lines, or None when the processes cannot be listed.
    """
    if not patterns:
        return []
    processes = _ps_args()
    if processes is None:
        return None
    ours: set[int] = set(_ancestors(os.getpid()))
    for ticket in live_tickets(root, prune):
        ours.update(_ancestors(ticket.pid))
    record = _holding(lock)
    holder = record.get("pid") if record else None
    matchers = [re.compile(p) for p in patterns]
    found = []
    for pid, args in sorted(processes.items()):
        if pid in ours or not any(m.search(args) for m in matchers):
            continue
        if holder is not None and holder in _ancestors(pid):
            continue
        found.append(f"{pid} {_one_line(args)}")
    return found


def _gpu_busy(config: Mapping, root: Path, lock: Path) -> str | None:
    """Why a --gpu job must wait (D3), or None.

    The pattern check always applies. Without nvidia-smi only the compute-app check is dropped,
    and the lock serialises the rest (P70).
    """
    foreign = foreign_heavy(config["foreign_heavy_patterns"], root, lock)
    if foreign is None:
        return "gpu busy: ps failed"
    if foreign:
        return f"gpu busy: {foreign[0]}"
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return None
    try:
        done = subprocess.run(
            [smi, "--query-compute-apps=pid", "--format=csv,noheader"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=PS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"gpu busy: nvidia-smi failed ({_one_line(error)})"
    if done.returncode != 0:
        return f"gpu busy: nvidia-smi failed (exit {done.returncode})"
    apps = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if not apps:
        return None
    processes = _ps_args() or {}
    args = processes.get(int(apps[0]), "(unknown)") if apps[0].isdigit() else "(unknown)"
    return f"gpu busy: {_one_line(apps[0])} {_one_line(args)}"


def _head_and_reservation(
    order: Sequence[Ticket], config: Mapping
) -> tuple[Ticket | None, datetime | None]:
    """The head of the admission order, and since when the host is reserved for it (P48)."""
    if not order:
        return None, None
    head = order[0]
    since = head.enqueued + timedelta(minutes=config["reserve_after_minutes"])
    return head, since if datetime.now(timezone.utc) >= since else None


def _fits(ticket: Ticket, mem: int | None, config: Mapping) -> bool:
    """Whether a ticket ahead could run now: its owner says it is not short, nor does `mem`."""
    if ticket.blocked in ("ram", "gpu"):
        return False
    return mem is None or mem >= _needed_mb(ticket.ram_mb, config)


def _behind(
    mine: str, tickets: Sequence[Ticket], mem: int | None, config: Mapping, drained: bool
) -> Ticket | None:
    """The ticket `mine` must wait for, or None when it may take the lock (R194, P48).

    The head may. Another may only while nothing ahead of it fits and the head has waited less
    than reserve_after_minutes. In a drain, the tickets it holds back are not in the order.
    """
    order = [t for t in tickets if t.name == mine or not (drained and t.blocked == "drain")]
    if drained:  # a rig-less ticket is held back whether or not its owner has said so yet
        order = [t for t in order if t.name == mine or t.rig is not None]
    head, reserved = _head_and_reservation(order, config)
    if head is None or head.name == mine:
        return None
    if reserved is not None:
        return head
    for ticket in order:
        if ticket.name == mine:
            break
        if _fits(ticket, mem, config):
            return ticket
    return None


class _Waiter:
    """This process's ticket from enqueue to release."""

    def __init__(self, job: str, rig_name: str | None, ram_mb: int, gpu: bool) -> None:
        """A ticket not yet written; it is dated now."""
        now = datetime.now(timezone.utc)
        self.job, self.rig, self.ram_mb, self.gpu = job, rig_name, ram_mb, gpu
        self.token = secrets.token_hex(16)
        self.enqueued_at = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self.name = f"{now:%Y%m%dT%H%M%S%fZ}-{os.getpid()}-{secrets.token_hex(4)}.json"
        self.root = state_dir().absolute()
        self.path = self.root / "queue" / self.name

    def publish(self, blocked: dict | None, config: Mapping) -> None:
        """Write the ticket, or its changed `blocked` verdict; keys added by others are kept.

        Raises:
            NewerSchema: The host state is newer, whether or not there is anything to write.
        """
        _refuse_newer(self.root)
        try:
            current = read_state(self.path)
        except StateError:
            current = None
        if current is not None and "blocked" in current:
            before = current["blocked"]
            kind = before.get("kind") if isinstance(before, dict) else None
            if kind == (blocked or {}).get("kind"):
                return

        def mutate(data: dict) -> None:
            defaults = {
                "job": self.job,
                "rig": self.rig,
                "pid": os.getpid(),
                "token": self.token,
                "needs": {"ram_mb": self.ram_mb, "gpu": self.gpu},
                "tier": tier_of(self.rig, config["priority"]),
                "enqueued_at": self.enqueued_at,
            }
            for key, value in defaults.items():
                data.setdefault(key, value)
            data["blocked"] = blocked

        update_state(f"queue/{self.name}", mutate)

    def remove(self) -> None:
        """Delete the ticket; under a newer schema it stays and its dead pid retires it."""
        if not self.path.exists():
            return
        with contextlib.suppress(NewerSchema, OSError), state_lock(self.root):
            self.path.unlink(missing_ok=True)


# ------------------------------------------------------------------------ the heavy job: lease


class HostBusy(Exception):
    """The bounded wait ran out; `reason` is the last thing that kept the job waiting."""

    def __init__(self, reason: str) -> None:
        """Keep the reason for the `host busy` line."""
        super().__init__(reason)
        self.reason = reason


# Leases this process holds, so a stop signal that lands before the caller has one still frees it.
_LEASES: set[Lease] = set()


class Lease:
    """A held lock (or a re-entrant pass-through when `fd` is None); a context manager."""

    def __init__(self, lock: Path, fd: int | None, token: str, job: str, waited: float) -> None:
        """A lease on `lock`; `fd` is the locked descriptor (None: re-entrant)."""
        self.lock, self.fd, self.token, self.job, self.waited = lock, fd, token, job, waited
        self.started = time.monotonic()
        self.rig: str | None = None
        self.ram_mb, self.gpu = 0, False
        self.ticket: _Waiter | None = None

    @property
    def reentrant(self) -> bool:
        """True when an ancestor holds the lock and this lease only passes its token on."""
        return self.fd is None

    def env(self) -> dict[str, str]:
        """What a child needs to run inside this lease."""
        return {TOKEN_VAR: self.token}

    def release(self) -> None:
        """Remove our record and ticket, log the release and unlock (no-op if re-entrant/done)."""
        if self.fd is None:
            return
        _LEASES.discard(self)
        try:
            record = read_record(self.lock)
            if record and record.get("token") == self.token:
                record_path(self.lock).unlink(missing_ok=True)
            if self.ticket is not None:
                self.ticket.remove()
            _log(self, "release", time.monotonic() - self.started)
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


def _take(
    waiter: _Waiter,
    lock: Path,
    mem: Callable[[], int | None],
    waited: Callable[[], float],
    lock_word: str,
) -> tuple[Lease | None, str]:
    """One admission check (R187, R189, R190, R194): (the lease, "") or (None, why not yet)."""
    config, _ = load_config()
    root = waiter.root
    drain = idle_point(root)
    available = mem()  # (b) memory first, without the lock
    needed = _needed_mb(waiter.ram_mb, config)
    blocked = None
    if drain is not None and not _in_flight(waiter.rig, config):
        blocked = {"kind": "drain", "reason": f"idle point: {_one_line(drain['purpose'])}"}
    elif available is not None and available < needed:
        blocked = {"kind": "ram", "reason": f"MemAvailable {available} MB < {needed} MB"}
    elif waiter.gpu and (busy := _gpu_busy(config, root, lock)):
        blocked = {"kind": "gpu", "reason": busy}
    waiter.publish(blocked, config)
    if blocked is not None:
        return None, blocked["reason"]
    tickets = waiting_tickets(root, lock)
    ahead = _behind(waiter.name, tickets, available, config, drain is not None)
    if ahead is not None:
        return None, f"queued behind {ahead.label}"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # (e)
        except BlockingIOError:
            line = holder_line(lock)
            return None, f"{lock_word}{line}" if line.startswith("held by") else line
        available = mem()  # memory again, holding the lock
        if available is not None and available < needed:
            fcntl.flock(fd, fcntl.LOCK_UN)
            short = f"MemAvailable {available} MB < {needed} MB"
            return None, f"{short} right after acquiring; released"
        lease = Lease(lock, fd, waiter.token, waiter.job, waited())
        lease.rig, lease.ram_mb, lease.gpu = waiter.rig, waiter.ram_mb, waiter.gpu
        lease.ticket = waiter
        record = {
            "schema": JOB_SCHEMA,
            "pid": os.getpid(),
            "token": waiter.token,
            "job": waiter.job,
            "cwd": os.getcwd(),
            "started_at": _utc(),
            "needs": {"ram_mb": waiter.ram_mb, "gpu": waiter.gpu},
            "ticket": waiter.name,
        }
        if waiter.rig is not None:
            record["rig"] = waiter.rig
        fd = -1  # the lease owns it now
        _LEASES.add(lease)
        try:
            _write_record(lock, record)
            _log(lease, "acquire", lease.waited)
        except BaseException:
            lease.release()
            raise
        return lease, ""
    finally:
        if fd >= 0:
            os.close(fd)  # closing the descriptor releases the lock


def acquire(
    job: str,
    wait_s: float | None = None,
    *,
    rig_name: str | None = None,
    ram_mb: int = 0,
    gpu: bool = False,
    mem: Callable[[], int | None] | None = None,
    out=None,
    prog: str = "hacf-host",
    lock_word: str = "lock ",
) -> Lease:
    """Take the heavy-job lock for `job` through the ticket queue.

    A valid inherited token returns a re-entrant Lease at once, with no ticket. Messages go to
    `out` (stderr). `mem` replaces the MemAvailable reader, `prog` names the caller and
    `lock_word` starts the reason that names the holder.

    Raises:
        HostBusy: The bounded wait ran out.
        NewerSchema: The host state is newer; nothing was written and the lock was not taken.
    """
    out = sys.stderr if out is None else out
    mem = mem_available_mb if mem is None else mem
    lock = heavy_lock_path()
    if os.environ.get(TOKEN_VAR):
        if token_valid():
            return Lease(lock, None, os.environ[TOKEN_VAR], job, 0.0)
        if LOCK_PATH_VAR not in os.environ:  # under the override an inherited token is dropped
            print(f"{prog}: ignoring an invalid {TOKEN_VAR}; acquiring", file=out, flush=True)
    wait_s = _env_number("HACF_HEAVY_WAIT_S", DEFAULT_WAIT_S) if wait_s is None else wait_s
    poll = max(0.01, _env_number("HACF_HEAVY_POLL_S", DEFAULT_POLL_S))
    waiter = _Waiter(job, rig_name, ram_mb, gpu)
    lock.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    last_report: float | None = None
    lease = None
    try:
        while True:
            lease, reason = _take(waiter, lock, mem, lambda: time.monotonic() - start, lock_word)
            if lease is not None:
                return lease
            now = time.monotonic()
            if now - start >= wait_s:
                raise HostBusy(reason)
            if last_report is None or now - last_report >= REPORT_EVERY_S:
                print(f"{prog}: {job} waiting: {reason}", file=out, flush=True)
                last_report = now
            time.sleep(min(poll, max(0.0, wait_s - (now - start))))
    finally:
        if lease is None:
            waiter.remove()


# -------------------------------------------------------------------------- the heavy job: run


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


class _Stopped(BaseException):
    """A stop signal arrived while `job run` had no command to forward it to."""


def run_job(argv: list[str], take: Callable[[], Lease]) -> int:
    """Run argv holding the lock `take` returns; its exit code, or 75 when the wait runs out.

    SIGTERM, SIGINT and SIGHUP are handled from before `take` is called. Until the command has
    started, one ends the run: no command, the lease released, exit 128 + signal. After that the
    first one is forwarded to the command's group, then SIGKILL after the grace.

    With a valid re-entrancy token argv replaces this process, and no ticket is written.
    """
    if token_valid():  # an ancestor holds it: run in place
        os.execvp(argv[0], argv)
    received: list[int] = []
    child: subprocess.Popen | None = None
    deferred = False  # True: the handler only records, as the code looks at `received` itself
    forwarded = False

    def forward() -> None:
        nonlocal forwarded
        if child is not None and received and not forwarded:
            forwarded = True
            _killpg(child.pid, signal.SIGTERM)

    def on_signal(signum: int, _frame: object) -> None:
        if received:
            return
        received.append(signum)
        if child is None and not deferred:
            raise _Stopped
        forward()

    previous = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
    # A handled signal reaches the command at its default; one ignored here stays ignored there.
    ignored = [sig for sig, handler in previous.items() if handler == signal.SIG_IGN]

    def ignore() -> None:
        for sig in ignored:
            signal.signal(sig, signal.SIG_IGN)

    held = set(_LEASES)
    try:
        for sig in STOP_SIGNALS:
            signal.signal(sig, on_signal)
        try:
            lease = take()
        except HostBusy as exc:
            print(f"host busy: {exc.reason}; retry later (exit {EXIT_BUSY})", file=sys.stderr)
            return EXIT_BUSY
        env = {**os.environ, **lease.env()}
        env.pop(HOLDER_PID_VAR, None)
        deferred = True  # a signal during Popen must not lose the child it is starting
        if received:
            raise _Stopped
        # The child holds the locked fd too (pass_fds), so a killed parent never frees the lock
        # while the job runs; it gets its own process group so a signal can stop all of it.
        child = subprocess.Popen(
            argv,
            env=env,
            pass_fds=(lease.fd,),
            start_new_session=True,
            preexec_fn=ignore if ignored else None,
        )
        forward()
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
    except _Stopped:
        return 128 + received[0]
    finally:
        deferred = True  # nothing may interrupt the release
        for lease_held in _LEASES - held:
            lease_held.release()
        for sig, handler in previous.items():
            if handler is not None:
                signal.signal(sig, handler)


# ----------------------------------------------------------------------- the heavy job: status


def _queue_entries(tickets: Sequence[Ticket]) -> list[dict]:
    """The waiting tickets as status prints them: no token."""
    now = datetime.now(timezone.utc)
    return [
        {
            "ticket": t.name,
            "job": t.job,
            "rig": t.rig,
            "pid": t.pid,
            "tier": t.tier,
            "needs": {"ram_mb": t.ram_mb, "gpu": t.gpu},
            "enqueued_at": t.enqueued.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "waiting_s": max(0, int((now - t.enqueued).total_seconds())),
            "blocked": t.blocked,
        }
        for t in tickets
    ]


def queue_entries() -> list[dict]:
    """The jobs waiting for the lock, in admission order."""
    return _queue_entries(waiting_tickets(state_dir(), heavy_lock_path()))


class _Heavy(NamedTuple):
    """One reading of the heavy job; `reserved` is the head and since when it holds the host."""

    holder: dict | None
    locked: bool | None
    tickets: list[Ticket]
    blocked_head: str | None
    reserved: tuple[Ticket, str] | None
    foreign: list[str]
    mem_mb: int | None
    drain: dict | None


def _heavy_view(config: Mapping, prune: bool) -> _Heavy:
    """The holder, the queue, the head's block or reservation, foreign processes and memory."""
    root, lock = state_dir(), heavy_lock_path()
    record = _holding(lock)
    holder = None
    if record is not None:
        holder = {
            "job": record.get("job"),
            "rig": record.get("rig"),
            "pid": record.get("pid"),
            "needs": record.get("needs"),
            "since": record.get("started_at"),
        }
    tickets = waiting_tickets(root, lock, prune)
    drain = idle_point(root)
    order = tickets
    if drain is not None:
        order = [t for t in tickets if t.blocked != "drain" and t.rig is not None]
    available = mem_available_mb()
    head, since = _head_and_reservation(order, config)
    blocked = reserved = None
    if head is not None and since is not None:
        reserved = (head, since.strftime("%Y-%m-%dT%H:%M:%SZ"))
    elif head is not None and not _fits(head, available, config):
        blocked = f"blocked head: {_one_line(head.job)} needs {head.ram_mb} MB"
    foreign = foreign_heavy(config["foreign_heavy_patterns"], root, lock, prune)
    foreign = ["unknown: ps failed"] if foreign is None else foreign
    return _Heavy(
        holder, _probe_locked(lock), tickets, blocked, reserved, foreign, available, drain
    )  # fmt: skip


def job_status() -> dict:
    """The holder, the queue, the head's block or reservation, heavy processes and memory."""
    config, _ = load_config()
    view = _heavy_view(config, prune=True)
    reserved = view.reserved
    return {
        "schema": JOB_STATUS_SCHEMA,
        "holder": view.holder,
        "locked": view.locked,
        "queue": _queue_entries(view.tickets),
        "blocked_head": view.blocked_head,
        "reserved": reserved and f"reserved for: {reserved[0].label} since {reserved[1]}",
        "foreign_heavy": view.foreign,
        "non_cooperating": non_cooperating(),
        "mem_available_mb": view.mem_mb,
        "min_mb": min_mb(),
        "idle_point": view.drain,
    }


def _needs_text(needs: object) -> str:
    """A ticket's or record's needs for a status line."""
    if not isinstance(needs, dict):
        return "needs unknown"
    return f"needs {needs.get('ram_mb')} MB" + (" + gpu" if needs.get("gpu") else "")


def _job_status_lines(data: Mapping) -> list[str]:
    holder = data["holder"]
    if holder:
        lines = [
            f"holder: {holder['job']} ({holder['rig'] or 'no rig'}, pid {holder['pid']}, "
            f"{_needs_text(holder['needs'])}) since {holder['since']}"
        ]
    elif data["locked"]:
        lines = ["holder: unknown (the lock is held with no live holder record)"]
    else:
        lines = ["holder: none"]
    lines.append(f"queue: {len(data['queue'])}")
    for place, entry in enumerate(data["queue"], 1):
        lines.append(
            f"  {place}. {entry['job']} ({entry['rig'] or 'no rig'}, tier {entry['tier']}, "
            f"pid {entry['pid']}, {_needs_text(entry['needs'])}) waiting {entry['waiting_s']} s"
        )
    lines += [line for line in (data["blocked_head"], data["reserved"]) if line]
    for title, key in (
        ("foreign heavy processes", "foreign_heavy"),
        ("non-cooperating heavy processes", "non_cooperating"),
    ):
        lines.append(f"{title}: {len(data[key])}")
        lines += [f"  {line}" for line in data[key]]
    lines.append(f"MemAvailable: {data['mem_available_mb']} MB (floor {data['min_mb']} MB)")
    point = data["idle_point"]
    lines.append(f"idle point: {point['purpose'] if point else 'none'}")
    return [_one_line(line) for line in lines]


# ------------------------------------------------------------------------- auto-park: the predicate


def _row_tags(row: Mapping) -> set[str]:
    tags = row.get("tags")
    return {str(tag) for tag in tags} if isinstance(tags, list) else set()


def _actionable(row: Mapping, rig_name: str, fyi: set[str]) -> bool:
    """R181: a pending or in-progress row addressed to a seat of the rig that is not an FYI."""
    return (
        _of_rig(row["destinationSession"], rig_name)
        and row.get("state") in ACTIONABLE
        and not _row_tags(row) & fyi
    )


def _row_id(row: Mapping) -> str | None:
    qitem = row.get("qitemId")
    return qitem if isinstance(qitem, str) and qitem else None


def waits_on_person(
    rig_name: str, root: str, config: Mapping, rows: list[dict] | None, why: str
) -> tuple[list[str], list[str]]:
    """P3 (R186): the rig sent the person a question and has no row to act on.

    A question row is an active row a seat of the rig sent whose destination is a human
    destination, or whose tags hold a human tag. A row with an FYI tag is never one (P80).

    Args:
        rig_name: The rig.
        root: Its bound project, whose kit routes questions.
        config: The host config.
        rows: The host's active rows, or None when the list failed.
        why: Why that list is unknown or may be short, else "".

    Returns:
        (the question rows' ids, why P3 does not hold). No reason means it holds.
    """
    if rows is None or why:
        return [], [f"P3: unknown: {why}"]
    try:
        value, tags = question_routing(rig_name, root)
        destinations = {routed_address(value, "question"), *config["human"]["destinations"]}
    except Refused as error:
        return [], [f"P3: unknown: {error}"]
    human_tags = {str(tags.get("question") or "hacf:question"), *config["human"]["tags"]}
    fyi = set(config["autopark"]["fyi_tags"])
    questions: list[str] = []
    actionable: list[str] = []
    for row in rows:
        mine = _of_rig(row["destinationSession"], rig_name)
        if mine and row.get("state") not in ACTIVE_STATES.split(","):
            return [], [f"P3: unknown: {_row_id(row)}: state {_shown(row.get('state'))}"]
        if _actionable(row, rig_name, fyi):
            actionable.append(_row_id(row) or "a row without an id")
        found = _row_tags(row)
        asked = row["destinationSession"] in destinations or found & human_tags
        if asked and _of_rig(row.get("sourceSession"), rig_name) and not found & fyi:
            qitem = _row_id(row)
            if qitem is None:
                return [], ["P3: unknown: a question row has no id"]
            questions.append(qitem)
    reasons = []
    if actionable:
        reasons.append(f"P3: actionable rows for the rig: {', '.join(actionable)}")
    if not questions:
        reasons.append("P3: no human-question row")
    return sorted(questions), reasons


def _recent_parks(record: Mapping) -> int | None:
    """The record's parks in the last 24 h, or None when the list cannot be read.

    A time that cannot be read counts.
    """
    parks = record.get("parks", [])
    if not isinstance(parks, list):
        return None
    since = utcnow() - timedelta(hours=24)
    times = [_parse_utc(item) for item in parks]
    return sum(1 for at in times if at is None or at > since)


def _over_the_cap(record: Mapping, config: Mapping) -> str:
    """P1's cap (R181): why the rig may not be parked again in this 24 h, else "".

    A park list or time that cannot be read counts against the cap.
    """
    recent = _recent_parks(record)
    if recent is None:
        return "P1: park cap reached: the park list cannot be read"
    most = config["autopark"]["max_parks_per_day"]
    return f"P1: park cap reached ({recent} in 24 h, at most {most})" if recent >= most else ""


def timeout_start_s(config: Mapping) -> int:
    """The unit's TimeoutStartSec: a park, a restore and a margin (P75 a)."""
    return config["autopark"]["restore_timeout_s"] + TIMEOUT_PARK_S + TIMEOUT_MARGIN_S


# ------------------------------------------------------------------------------ auto-park: the tick


def _reset_grace(rig_name: str, record: Mapping, why: str) -> None:
    """P5: forget the rig's eligible time, logging the reset when it had one (R208)."""
    if not any(key in record for key in GRACE_KEYS):
        return

    def mutate(data: dict) -> None:
        for key in GRACE_KEYS:
            data.pop(key, None)

    update_state(f"rigs/{rig_name}.json", mutate)
    host_log("grace reset", f"{rig_name}:", why)


def _hold_grace(rig_name: str, record: Mapping, config: Mapping) -> datetime:
    """P5: keep the first time the rig was eligible and the latest check of it.

    The start is forgotten when nobody checked for longer than a working timer can leave (P80):
    what the rig did then was never seen.

    Returns:
        The time the rig has been eligible since.
    """
    now = utcnow().replace(microsecond=0)
    since = _parse_utc(record.get("eligible_since"))
    checked = _parse_utc(record.get("eligible_checked"))
    longest = timedelta(seconds=timeout_start_s(config) + TICK_INTERVAL_S)
    if since is not None and (since > now or checked is None or now - checked > longest):
        last = record.get("eligible_checked")
        host_log("grace reset", f"{rig_name}:", f"no check since {_shown(last)}")
        since = None
    since = now if since is None else since

    def mutate(data: dict) -> None:
        data.setdefault("state", RUNNING)
        data.setdefault("since", _utc())
        data["eligible_since"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        data["eligible_checked"] = _utc()

    update_state(f"rigs/{rig_name}.json", mutate)
    return since


def _last_output(rig_name: str) -> tuple[datetime | None, str]:
    """P5 (P107): the newest `lastActivityAt` of the rig's running seats, from a fresh read.

    OpenRig's own field only: the time of a seat's last pane output. Nothing else is consulted.

    Returns:
        (the newest time, or None when no seat runs, why P5 is unknown or ""). One running seat
        with no readable time makes it unknown.
    """
    nodes, why = rig_nodes(rig_name)
    if nodes is None:
        return None, f"P5: unknown: {why}"
    times = []
    for node in nodes:
        if node.get("sessionStatus") != "running":
            continue
        at = _parse_utc(node.get("lastActivityAt"))
        if at is None:
            seat = node.get("canonicalSessionName") or node.get("logicalId")
            return None, f"P5: unknown: OpenRig gives no lastActivityAt for {seat}"
        times.append(at)
    return max(times, default=None), ""


class _Due(NamedTuple):
    """A rig a tick may park or restore; `order` sorts the one to act on first."""

    order: tuple
    rig: str
    root: str
    rows: list[str]  # park: the question rows; restore: the trigger


def _restore_trigger(
    rig_name: str, record: Mapping, config: Mapping, rows: list[dict], why: str
) -> str:
    """MR 3: the actionable row's id, or `waits_over`, or "" when the rig still waits.

    A list that may be short shows work, never that a wait is over.
    """
    fyi = set(config["autopark"]["fyi_tags"])
    work = sorted(_row_id(row) or "unknown" for row in rows if _actionable(row, rig_name, fyi))
    if work:
        return work[0]
    waits = record.get("waits_on")
    if why or not isinstance(waits, list) or not waits:
        return ""
    active = {_row_id(row) for row in rows}
    return "" if any(item in active for item in waits) else "waits_over"


class _Tick:
    """One pass: judge every mapped rig, then park at most one and restore at most one."""

    def __init__(self, config: Mapping, dry_run: bool) -> None:
        self.config = config
        self.dry_run = dry_run
        self.parks: list[_Due] = []
        self.restores: list[_Due] = []
        self.code = EXIT_OK

    def skip(self, rig_name: str, why: str) -> None:
        host_log("skip", f"{rig_name}:", why)

    def not_eligible(self, rig_name: str, record: Mapping, why: str) -> None:
        self.skip(rig_name, why)
        _reset_grace(rig_name, record, why)

    def run(self) -> int:
        config = self.config
        listed, warnings = paused_in_files(config)
        ps, why_ps = _rig_objects("ps", "--json", timeout=RIG_PS_TIMEOUT_S)
        rows, why_rows = _queue_rows(ACTIVE_STATES)
        if ps is None or why_rows:
            self.code = EXIT_FAIL
        for rig_name in sorted(config["projects"]):
            root = _bound_root(rig_name, config)
            if root is None or not RIG_NAME_RE.fullmatch(rig_name):
                continue
            # A rig that may be paused gets no state write at all (R183).
            if warnings:
                self.skip(rig_name, "; ".join(warnings))
            elif rig_name in listed:
                self.skip(rig_name, f"paused by the person: listed in {listed[rig_name]}")
            else:
                try:
                    statuses = (
                        None
                        if ps is None
                        else [row.get("status") for row in ps if row.get("name") == rig_name]
                    )
                    self.judge(rig_name, root, statuses, why_ps, rows, why_rows)
                except StateError as error:
                    self.skip(rig_name, str(error))
                    self.code = EXIT_FAIL
        self.park_one()
        self.restore_one()
        return self.code

    def judge(
        self,
        rig_name: str,
        root: str,
        statuses: list | None,
        why_ps: str,
        rows: list[dict] | None,
        why_rows: str,
    ) -> None:
        """Put one rig that is not paused on the park or restore list, or say why not."""
        record = rig_record(rig_name)
        state = record.get("state", RUNNING)
        if state == PAUSED:
            self.skip(rig_name, "paused by the person")
            return
        if statuses is None:
            self.not_eligible(rig_name, record, f"unknown: rig ps failed: {why_ps}")
            return
        if state == PARKED:
            self.judge_parked(rig_name, root, record, statuses, rows, why_rows)
            return
        if not statuses:
            return  # a rig OpenRig does not list is not evaluated
        if all(status == "stopped" for status in statuses):
            _reset_grace(rig_name, record, "rig ps shows it stopped")
            return
        capped = "P1: restore-failed" if state == FAILED else _over_the_cap(record, self.config)
        if capped:
            self.not_eligible(rig_name, record, capped)
            return
        report = boundary(rig_name, root)
        reasons = [f"{k}: {v['reason']}" for k, v in report["checks"].items() if not v["ok"]]
        waits, unmet = waits_on_person(rig_name, root, self.config, rows, why_rows)
        reasons += unmet
        if reasons:
            self.not_eligible(rig_name, record, "; ".join(reasons))
            return
        output, why = _last_output(rig_name)
        if why:
            self.not_eligible(rig_name, record, why)
            return
        since = _hold_grace(rig_name, record, self.config)
        # R208, P107: output seen between two ticks restarts the grace; a future time is now.
        start = since if output is None else max(since, min(output, utcnow()))
        grace = timedelta(minutes=self.config["autopark"]["grace_minutes"])
        if utcnow() - start < grace:
            stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
            seen = "none" if output is None else output.astimezone(timezone.utc).isoformat()
            host_log(
                "eligible",
                f"{rig_name}:",
                f"since {stamp} last_activity={seen} waits_on={','.join(waits)}",
            )
            return
        # P77: the lowest tier first, then the earliest eligible.
        order = (-tier_of(rig_name, self.config["priority"]), since, rig_name)
        self.parks.append(_Due(order, rig_name, root, waits))

    def judge_parked(
        self,
        rig_name: str,
        root: str,
        record: Mapping,
        statuses: list,
        rows: list[dict] | None,
        why_rows: str,
    ) -> None:
        if record.get("by") != "supervisor":  # P75 b
            host_log("skip", f"{rig_name}:", "parked by person")
            return
        if not statuses or any(status != "stopped" for status in statuses):
            self.skip(rig_name, "parked, and rig ps does not show it stopped")
            return
        if rows is None:
            self.skip(rig_name, f"unknown: {why_rows}")
            return
        trigger = _restore_trigger(rig_name, record, self.config, rows, why_rows)
        if not trigger:
            waits = record.get("waits_on")
            waits = ", ".join(map(str, waits)) if isinstance(waits, list) and waits else "no row"
            self.skip(rig_name, f"still waiting on {waits}")
            return
        parked = _parse_utc(record.get("since")) or utcnow()
        # P76: the highest tier first, then the earliest parked.
        order = (tier_of(rig_name, self.config["priority"]), parked, rig_name)
        self.restores.append(_Due(order, rig_name, root, [trigger]))

    def park_one(self) -> None:
        if not self.parks:
            return
        first, *others = sorted(self.parks)
        for due in others:
            host_log("park queued:", due.rig, "(next tick)")
        if self.dry_run:
            host_log("would-park", first.rig, f"waits_on={','.join(first.rows)}")
            return
        try:
            with rig_operation(first.rig):
                self.park(first)
        except RigBusy as error:
            self.skip(first.rig, str(error))
        except (StateError, OSError) as error:
            self.code = EXIT_FAIL
            host_log("park", first.rig, f"failed {_one_line(error)}")
            with contextlib.suppress(StateError, OSError):
                record = rig_record(first.rig)
                _reset_grace(first.rig, record, f"park aborted: {_one_line(error)}")

    def park(self, due: _Due) -> None:
        """MR 2's park, under the rig's lock."""
        state = rig_record(due.rig).get("state", RUNNING)
        listed, warnings = paused_in_files(load_config()[0])
        if state != RUNNING or warnings or due.rig in listed:  # R183: not even a snapshot
            self.skip(due.rig, "its state changed during this tick")
            return

        def still_waits() -> str:
            """R191: P3 again, from a fresh queue read; any change aborts."""
            rows, why = _queue_rows(ACTIVE_STATES)
            config = load_config()[0]
            waits, unmet = waits_on_person(due.rig, due.root, config, rows, why)
            if unmet:
                return "; ".join(unmet)
            return "" if waits == due.rows else f"the question rows changed: {', '.join(waits)}"

        count = len(due.rows)
        started = clock()
        outcome = park_rig(
            due.rig,
            "supervisor",
            f"waits on {count} question rows",
            p3=still_waits,
            waits_on=due.rows,
        )
        took = f"seconds={clock() - started:.1f} waits_on={','.join(due.rows)}"
        if not outcome.ok:
            if outcome.state is None:  # the rig is still up: the next try waits a grace (P80)
                host_log("park", due.rig, f"aborted {took} {outcome.reason}")
                _reset_grace(due.rig, rig_record(due.rig), f"park aborted: {outcome.reason}")
            else:
                self.code = EXIT_FAIL
                host_log("park", due.rig, f"failed {took} {outcome.reason}")
            return

        def mutate(data: dict) -> None:
            parks = data.get("parks")
            data["parks"] = [*(parks if isinstance(parks, list) else []), _utc()]
            for key in GRACE_KEYS:
                data.pop(key, None)

        record = update_state(f"rigs/{due.rig}.json", mutate)
        host_log("park", due.rig, f"done {took}")
        _send_park_fyi(due.rig, due.root, count, str(record.get("resume_point")))

    def restore_one(self) -> None:
        if not self.restores:
            return
        if idle_point(state_dir()) is not None:  # R187
            for due in self.restores:
                self.skip(due.rig, "restore deferred by idle point")
            return
        due_rigs = sorted(self.restores)
        while due_rigs:
            if self.restore_next(due_rigs.pop(0)):
                break
        for due in due_rigs:
            host_log("restore queued:", due.rig, "(next tick)")

    def restore_next(self, due: _Due) -> bool:
        """Try one due rig; False when its restore cannot start, so the next may (P83)."""
        trigger = f"trigger={due.rows[0]}"
        try:
            if self.dry_run:
                why = unusable(rig_record(due.rig))
                host_log("would-fail" if why else "would-restore", due.rig, why or trigger)
                return not why
            with rig_operation(due.rig):
                return self.restore(due, trigger)
        except RigBusy as error:
            self.skip(due.rig, str(error))
        except (StateError, OSError) as error:
            self.code = EXIT_FAIL
            host_log("restore", due.rig, f"failed {_one_line(error)}")
        return True

    def restore(self, due: _Due, trigger: str) -> bool:
        """MR 3's restore, under the rig's lock, with no wait for memory (P81).

        Returns:
            Whether it used the tick's one restore.
        """
        record = rig_record(due.rig)
        listed, warnings = paused_in_files(load_config()[0])
        if (record.get("state"), record.get("by")) != (PARKED, "supervisor") or (
            warnings or due.rig in listed
        ):
            self.skip(due.rig, "its state changed during this tick")
            return True
        why = unusable(record)
        if why:
            self.code = EXIT_FAIL
            outcome = fail_unusable(due.rig, why)
            host_log("restore", due.rig, f"failed {trigger} {outcome.reason}")
            return False
        started = clock()
        outcome = restore_rig(due.rig, ram_wait_s=0)
        took = f"seconds={clock() - started:.1f} {trigger}"
        if outcome.ok:
            host_log("restore", due.rig, f"done {took}")
        elif outcome.busy:
            short = "MemAvailable" in outcome.reason
            why = f"ram {took} {outcome.reason}" if short else f"{outcome.reason} {took}"
            host_log("restore deferred", f"{due.rig}:", why)
        else:
            self.code = EXIT_FAIL
            host_log("restore", due.rig, f"failed {took} {outcome.reason}")
        return True


def _send_park_fyi(rig_name: str, root: str, count: int, point: str) -> None:
    """MR 2: one FYI row to the update destination; a failure is logged, never guessed (R199)."""
    try:
        value, tags = question_routing(rig_name, root, "update.destination")
        destination = routed_address(value, "update")
    except Refused as error:
        host_log("fyi", rig_name, f"not sent: {error}")
        return
    summary = f"HACF host: parked {rig_name} (waits on {count} question rows; resume point {point})"
    body = "\n".join(
        [
            f"The HACF host parked the rig {rig_name}: it only waits on {count} question rows.",
            "It restores the rig when a row for it can be acted on, or those rows are over.",
            "",
            f"Resume point: {point}",
        ]
    )
    tags = _routing_tags(tags, "update", rig_name)
    why = _create_row(destination, summary, tags, body, point, "update")
    host_log("fyi", rig_name, f"not sent: {why}" if why else f"sent to {destination}")


@contextlib.contextmanager
def supervise_lock() -> Iterator[None]:
    """Hold `supervise.lock`, so two ticks never park or restore side by side.

    Raises:
        NewerSchema: The host state is newer; nothing is created.
        RigBusy: Another tick is running.
    """
    root = state_dir()
    _refuse_newer(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(root / "supervise.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RigBusy("another supervisor tick is running") from None
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def _stop_signals() -> Iterator[list[int]]:
    """Turn the first stop signal into _Stopped, so a cut park or restore is marked (P81).

    Later ones are only recorded, and so is every one once the caller has added to the list: the
    marking and its row must finish.
    """
    received: list[int] = []

    def on_signal(signum: int, _frame: object) -> None:
        received.append(signum)
        if len(received) == 1:
            raise _Stopped

    previous = {sig: signal.getsignal(sig) for sig in STOP_SIGNALS}
    try:
        for sig, handler in previous.items():
            if handler != signal.SIG_IGN:
                signal.signal(sig, on_signal)
        yield received
    finally:
        for sig, handler in previous.items():
            if handler is not None:
                signal.signal(sig, handler)


def _ask_about_the_cut(before: set[str], config: Mapping) -> None:
    """P81: one question row for each rig a stop or an error cut in this tick.

    The engine marks such a rig restore-failed and sends nothing itself.
    """
    for rig_name in sorted(config["projects"]):
        root = _bound_root(rig_name, config)
        if root is None or rig_name in before:
            continue
        try:
            record = rig_record(rig_name)
        except StateError:
            continue
        reason = record.get("reason")
        verb = next((v for v in ("park", "restore") if reason == f"{v} was interrupted"), None)
        if record.get("state") == FAILED and verb:
            outcome = Outcome(verb, rig_name, False, FAILED, reason, [])
            escalate(outcome, root, record.get("resume_point"))


def _failed_rigs(config: Mapping) -> set[str]:
    found = set()
    for rig_name in config["projects"]:
        with contextlib.suppress(StateError):
            if rig_record(rig_name).get("state") == FAILED:
                found.add(rig_name)
    return found


def cmd_supervise(args: argparse.Namespace) -> int:
    """`supervise --once`: one tick of auto-park (SuperPRD §5.7)."""
    # A default for a broken key here could park sooner, or in another order.
    config = _host_refusals(("autopark", "priority"))
    _refuse_newer(state_dir())
    if not config["autopark"]["enabled"] and not args.dry_run:
        host_log("supervise", "-", "autopark disabled")
        print("autopark disabled")
        return EXIT_OK
    if args.dry_run:  # it changes no rig, so it needs no lock and writes none
        host_log("supervise", "-", "tick start (dry-run)")
        with one_spec_listing():
            return _Tick(config, True).run()
    with supervise_lock(), one_spec_listing():
        host_log("supervise", "-", "tick start")
        before = _failed_rigs(config)
        with _stop_signals() as received:
            try:
                code = _Tick(config, False).run()
                received.append(0)  # from here a first signal no longer raises
            except _Stopped:
                name = signal.Signals(received[0]).name
                host_log("supervise", "-", f"stopped by {name}")
                code = EXIT_FAIL
            _ask_about_the_cut(before, config)
            return code


# ----------------------------------------------------------------------- auto-park: the user timer


def systemctl(*argv: str) -> tuple[int, str, str]:
    """Run `systemctl --user <argv>` from PATH: (exit code, stdout, stderr); 127 if it cannot."""
    try:
        done = subprocess.run(
            ["systemctl", "--user", *argv],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=SYSTEMCTL_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return RIG_TIMED_OUT, "", f"timed out after {SYSTEMCTL_TIMEOUT_S:g} s"
    except OSError as error:
        return RIG_NOT_RUN, "", f"cannot run systemctl: {error.strerror}"
    return done.returncode, done.stdout, done.stderr


def unit_dir() -> Path:
    """The systemd user unit dir: ${XDG_CONFIG_HOME:-~/.config}/systemd/user."""
    return _xdg(os.environ, "XDG_CONFIG_HOME", ".config") / "systemd" / "user"


def copy_dir() -> Path:
    """Where the supervisor's copies live, away from every project (R193)."""
    return _xdg(os.environ, "XDG_DATA_HOME", ".local", "share") / "hacf" / "host" / "bin"


def _unit_text(value: str) -> str:
    """`value` inside a double-quoted unit setting: no escape or %-specifier is read in it.

    Raises:
        Refused: It holds a line break or another control character.
    """
    if not value.isprintable():
        raise Refused(f"{_one_line(value)}: a unit file cannot hold it")
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def _unit_environment() -> list[tuple[str, str]]:
    """P82: the variables the ticks need from the installing shell.

    PATH keeps its absolute entries, less seat-bin dirs; `rig` must resolve on it.

    Raises:
        Refused: `rig` (or HACF_HOST_RIG) does not resolve to a program a tick could run.
    """
    entries = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if os.path.isabs(entry) and SEAT_BIN not in os.path.realpath(entry) + os.sep
    ]
    path = os.pathsep.join(entries)
    name = os.environ.get("HACF_HOST_RIG") or "rig"
    found = shutil.which(name, path=path) if os.path.isabs(name) or "/" not in name else None
    if found is None or SEAT_BIN in os.path.realpath(found):
        raise Refused(
            f"rig does not resolve outside a seat: {name} on the PATH without seat-bin entries; "
            "run install from a login shell"
        )
    set_ones = [(key, os.environ[key]) for key in UNIT_VARIABLES if os.environ.get(key)]
    return [("PATH", path), *set_ones]


def _units(copy: Path, config: Mapping, dry_run_mode: bool) -> dict[str, str]:
    """The service and timer files, by name."""
    target = _unit_text(str(copy)).replace("$", "$$")  # ExecStart also expands $
    flag = " --dry-run" if dry_run_mode else ""
    service = [
        "[Unit]",
        "Description=HACF host supervisor: one auto-park tick",
        "",
        "[Service]",
        "Type=oneshot",
        *(f'Environment="{key}={_unit_text(value)}"' for key, value in _unit_environment()),
        f'ExecStart=/usr/bin/env python3 "{target}" supervise --once{flag}',
        "Nice=10",
        f"TimeoutStartSec={timeout_start_s(config)}",
    ]
    timer = [
        "[Unit]",
        "Description=HACF host supervisor, every 2 minutes",
        "",
        "[Timer]",
        "OnBootSec=2min",
        "OnUnitActiveSec=2min",
        "",
        "[Install]",
        "WantedBy=timers.target",
    ]
    return {
        f"{UNIT_NAME}.service": "".join(f"{line}\n" for line in service),
        f"{UNIT_NAME}.timer": "".join(f"{line}\n" for line in timer),
    }


def _version_key(text: object) -> tuple[int, ...] | None:
    if not isinstance(text, str) or not re.fullmatch(r"[0-9]+(\.[0-9]+)*", text):
        return None
    return tuple(int(part) for part in text.split("."))


def _supervisor_record() -> dict | None:
    """`supervisor.json`, or None when nothing is recorded.

    Raises:
        StateError: It exists and cannot be read or judged.
    """
    path = state_dir() / "supervisor.json"
    data = read_state(path)
    if data is not None and schema_major(data) != STATE_MAJOR:
        raise StateError(f"{path}: not a {STATE_SCHEMA} file")
    return data


def _require_systemd() -> None:
    if systemctl("show-environment")[0] != 0:
        raise Refused(NEEDS_SYSTEMD)


def _systemctl_step(*argv: str) -> bool:
    rc, _, err = systemctl(*argv)
    if rc != 0:
        print(f"systemctl --user {' '.join(argv)}: {_exit_reason(rc, err)}", file=sys.stderr)
    return rc == 0


def supervisor_install(dry_run_mode: bool) -> int:
    """Copy this script, write the service and timer that run the copy, and start the timer."""
    config = _host_refusals(("autopark",))
    _refuse_newer(state_dir())
    _require_systemd()
    record = _supervisor_record()
    if record is not None:
        recorded = _version_key(record.get("version"))
        if recorded is None or recorded > _version_key(HACF_HOST_VERSION):
            raise Refused(
                f"supervisor.json records version {_shown(record.get('version'))}; this is "
                f"{HACF_HOST_VERSION}, and an older or unknown one is never installed over it"
            )
    source = Path(__file__).resolve()
    copy = copy_dir() / f"hacf-host-{HACF_HOST_VERSION}.py"
    units = _units(copy, config, dry_run_mode)  # every refusal comes before any write
    text = source.read_text(encoding="utf-8")
    _write_atomic(copy, text)
    copy.chmod(0o644)
    for name, content in units.items():
        _write_atomic(unit_dir() / name, content)
        (unit_dir() / name).chmod(0o644)
    if not _systemctl_step("daemon-reload"):
        return EXIT_FAIL
    if not _systemctl_step("enable", "--now", f"{UNIT_NAME}.timer"):
        return EXIT_FAIL

    def mutate(data: dict) -> None:
        data.update(version=HACF_HOST_VERSION, source=str(source), installed_at=_utc())
        data["dry_run_mode"] = dry_run_mode

    update_state("supervisor.json", mutate)
    mode = " in dry-run mode" if dry_run_mode else ""
    print(f"supervisor {HACF_HOST_VERSION} installed{mode}: {copy}")
    if not dry_run_mode and not config["autopark"]["enabled"]:
        print("autopark.enabled is false: each tick only logs `autopark disabled`")
    return EXIT_OK


def supervisor_remove() -> int:
    """Stop the timer, then remove the two units, the copies and the record."""
    _host_refusals()
    _refuse_newer(state_dir())
    _require_systemd()
    units = [unit_dir() / f"{UNIT_NAME}.{kind}" for kind in ("service", "timer")]
    if units[1].exists() and not _systemctl_step("disable", "--now", units[1].name):
        return EXIT_FAIL
    for path in [*units, *copy_dir().glob("hacf-host-*.py")]:
        path.unlink(missing_ok=True)
    reloaded = _systemctl_step("daemon-reload")
    if (state_dir() / "supervisor.json").exists():
        with state_lock(state_dir()):
            (state_dir() / "supervisor.json").unlink(missing_ok=True)
    print("supervisor removed")
    return EXIT_OK if reloaded else EXIT_FAIL


def _installed_setting(service: str, key: str) -> str | None:
    """An `Environment="KEY=value"` of the installed service, unescaped."""
    found = re.search(rf'^Environment="{key}=(.*)"$', service, re.M)
    if found is None:
        return None
    return re.sub(r'\\(["\\])|%(%)', lambda m: m[1] or m[2], found[1])


def _queued_lines() -> list[str]:
    """The `park queued:` and `restore queued:` lines of the latest tick in supervisor.log."""
    try:
        with open(state_dir() / "supervisor.log", "rb") as handle:
            handle.seek(max(0, handle.seek(0, os.SEEK_END) - LOG_TAIL_BYTES))
            lines = handle.read().decode(errors="replace").splitlines()
    except OSError:
        return []
    found: list[str] = []
    for line in lines:
        text = line.partition(" ")[2]
        if text.startswith("supervise - "):
            if "tick start" in text or "autopark disabled" in text:
                found = []
        elif text.startswith(("park queued: ", "restore queued: ")):
            found.append(text)
    return found


def supervisor_status() -> int:
    """Show the record, the timer, the unit's PATH and rig, warnings and the queued rigs."""
    config, notes = load_config()
    for line in notes:
        print(_one_line(line), file=sys.stderr)
    record = _supervisor_record()
    print("supervisor: " + ("not installed" if record is None else json.dumps(record, indent=2)))
    rc, out, err = systemctl("is-active", f"{UNIT_NAME}.timer")
    state = out.strip() or f"unknown ({_exit_reason(rc, err)})"
    print(f"timer: {_one_line(state)}")
    try:
        service = (unit_dir() / f"{UNIT_NAME}.service").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        service = None
    if service is not None:
        path = _installed_setting(service, "PATH") or ""
        name = _installed_setting(service, "HACF_HOST_RIG") or "rig"
        print(f"unit PATH: {path or 'none'}")
        found = shutil.which(name, path=path) or "not found on the unit's PATH"
        print(f"unit rig: {found}")
        limit = re.search(r"^TimeoutStartSec=([0-9]+)$", service, re.M)
        if limit is None or int(limit[1]) < timeout_start_s(config):
            print("warning: supervisor timeout below restore_timeout_s: re-run supervisor install")
    for line in _queued_lines():
        print(line)
    for path in unreadable_records():
        print(_one_line(f"unreadable record: {path}"))
    return EXIT_OK


def cmd_supervisor(args: argparse.Namespace) -> int:
    """`supervisor install|remove|status` (R193, R195)."""
    if args.action == "install":
        return supervisor_install(args.dry_run_mode)
    return supervisor_remove() if args.action == "remove" else supervisor_status()


# ------------------------------------------------------------------------------- the idle point


class _InForce(Exception):
    """An idle point is already in force."""


def _point_text(point: Mapping) -> str:
    """`<purpose> (requested by <who>, expires <when>)`."""
    return _one_line(
        f"{point.get('purpose', 'unknown')} (requested by "
        f"{point.get('requested_by', 'unknown')}, expires {point.get('expires_at', 'unknown')})"
    )


def request_idle_point(purpose: str, ttl_h: float) -> int:
    """Write `idle-point.json` unless one is in force: 0 written, 1 refused naming it.

    Raises:
        Refused: An empty purpose, or a --ttl past the last date.
        NewerSchema: The host state is newer.
    """
    if not purpose.strip():
        raise Refused("--purpose must say what the idle point is for")
    now = utcnow().replace(microsecond=0)
    try:
        expires = now + timedelta(seconds=math.ceil(ttl_h * 3600))
    except OverflowError:
        raise Refused(f"--ttl {ttl_h:g} is too long") from None
    by = os.environ.get("OPENRIG_SESSION_NAME") or os.environ.get("USER") or "unknown"
    point = {
        "purpose": _one_line(purpose.strip()),
        "requested_by": by,
        "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    def mutate(data: dict) -> None:
        if data and not _expired(data):  # another request got here first
            raise _InForce
        data.update(point)

    root = state_dir()
    _refuse_newer(root)
    current = idle_point(root)  # an unreadable one is in force, and is left as it is
    if current is None:
        try:
            update_state("idle-point.json", mutate)
        except _InForce:
            current = idle_point(root) or {}
    if current is not None:
        print(
            f"hacf-host.py idle-point request: one is already in force: {_point_text(current)}; "
            "release it first",
            file=sys.stderr,
        )
        return EXIT_FAIL
    print(f"idle point requested: {_point_text(point)}")
    return EXIT_OK


def release_idle_point() -> int:
    """Remove `idle-point.json`, in force, expired or unreadable."""
    root = state_dir()
    _refuse_newer(root)
    path = root / "idle-point.json"
    if os.path.lexists(path):
        with state_lock(root):
            point = idle_point(root)
            path.unlink(missing_ok=True)
        if point is not None:
            print(f"idle point released: {_point_text(point)}")
        else:
            print("no idle point is in force; removed the expired one")
        return EXIT_OK
    print("no idle point is requested; nothing to release")
    return EXIT_OK


def idle_status() -> dict:
    """The idle point and whether it is in force or has expired. It writes nothing."""
    root = state_dir()
    point = idle_point(root)
    held = point
    if point is None:
        with contextlib.suppress(StateError):
            held = read_state(root / "idle-point.json")
    return {
        "schema": IDLE_SCHEMA,
        "in_force": point is not None,
        "expired": point is None and held is not None,
        "idle_point": held,
    }


def _idle_rig(rig_name: str, config: Mapping, paused: set[str]) -> tuple[str, str]:
    """A rig that is not stopped: (why it is idle, "") or ("", why it is in flight)."""
    root = config["projects"].get(rig_name)
    if root is None:
        return "", "unmapped"  # R185: not even a paused file makes it idle
    if rig_name in paused:
        return "paused", ""
    try:
        parked = rig_record(rig_name).get("state") == PARKED
    except StateError:
        parked = False
    if parked:  # a parked rig someone started again is judged like any other
        nodes, _ = rig_nodes(rig_name)
        if nodes is not None and not _running_nodes(nodes):
            return "parked", ""
    report = boundary(rig_name, root)
    if report["at_boundary"]:
        return "boundary", ""
    failed = [f"{k}: {v['reason']}" for k, v in report["checks"].items() if not v["ok"]]
    return "", "; ".join(failed)


def _heavy_holder() -> str:
    """Who holds the heavy-job lock, or why that is unknown; "" when nobody does."""
    lock = heavy_lock_path()
    locked = _probe_locked(lock)
    if locked:
        return holder_line(lock)
    if locked is None and os.path.lexists(lock):
        return f"unknown: {lock} cannot be opened"
    return ""


def _idle_pass(config: Mapping) -> tuple[list[str], list[str]]:
    """One reading of the host: (`<rig>: <why idle>` each, `<what>: <why>` still in flight)."""
    paused, warnings = paused_rigs(config)
    for warning in warnings:  # a rig it may have named is judged at its boundary
        print(_one_line(warning), file=sys.stderr)
    ignored = set(config["ignore_rigs"])
    idle: dict[str, str] = {}
    busy: list[str] = []
    names, why = running_rigs()
    if names is None:
        busy.append(f"rigs: unknown: rig ps failed: {why}")
    else:
        for name in names:
            if name in ignored:
                continue
            state, why = _idle_rig(name, config, paused)
            if state:
                idle[name] = state
            else:
                busy.append(f"{name}: {why}")
        for name in set(config["projects"]) - ignored - set(names):  # stopped: named if held
            if name in paused:
                idle[name] = "paused"
            else:
                with contextlib.suppress(StateError):
                    if rig_record(name).get("state") == PARKED:
                        idle[name] = "parked"
    holder = _heavy_holder()  # read last: a job may have started meanwhile
    if holder:
        busy.append(f"heavy job: {holder}")
    lines = [_one_line(f"{name}: {state}") for name, state in sorted(idle.items())]
    return lines, [_one_line(line) for line in busy]


def wait_for_idle(timeout: float) -> int:
    """Read the host every 30 s until it is idle: 0, 75 at the timeout, 1 with no idle point.

    Raises:
        NewerSchema: The host state is newer; nothing can be judged.
    """
    root = state_dir()
    deadline = clock() + timeout
    while True:
        _refuse_newer(root)
        if idle_point(root) is None:
            print(
                "hacf-host.py idle-point wait: no idle point is in force: request first",
                file=sys.stderr,
            )
            return EXIT_FAIL
        idle, busy = _idle_pass(load_config()[0])
        if not busy:
            if idle_point(root) is None:  # it ended during the reading
                continue
            print("idle reached: " + (", ".join(idle) or "no rig is running"))
            return EXIT_OK
        left = deadline - clock()
        if left <= 0:
            print(f"idle not reached in {timeout:g} s; still in flight:", file=sys.stderr)
            for line in busy:
                print(f"  {line}", file=sys.stderr)
            return EXIT_BUSY
        pause_for(min(IDLE_POLL_S, left))


# --------------------------------------------------------------------------- the status view


def _park_history() -> tuple[list[str], dict[str, float], str | None]:
    """Read the whole of supervisor.log for the view.

    Returns:
        (the last park and restore lines, {rig: seconds of its last done restore}, the time of
        the last tick).
    """
    lines: deque[str] = deque(maxlen=PARK_LOG_LINES)
    restored: dict[str, float] = {}
    tick = None
    try:
        with open(state_dir() / "supervisor.log", encoding="utf-8", errors="replace") as handle:
            for line in handle:  # the skip lines of one day bury a cycle far from the end
                line = line.rstrip("\n")
                at, _, text = line.partition(" ")
                if text.startswith("supervise - "):
                    if "tick start" in text or "autopark disabled" in text:
                        tick = at
                    continue
                found = PARK_LOG_RE.fullmatch(line)
                if found is None:
                    continue
                lines.append(line)
                verb, rig_name, result, seconds = found.groups()
                if (verb, result) == ("restore", "done") and seconds is not None:
                    restored[rig_name] = float(seconds)
    except OSError:
        pass  # no log yet; an unreadable one shows no line
    return list(lines), restored, tick


def _rig_view(name: str, config: Mapping, listed: Mapping[str, str], paused_unknown: bool) -> dict:
    """One rig's binding, tier and HACF state, from the config, its record and the paused files.

    The state is the record's mark; a paused file makes a running one paused-by-person (R183).
    An unmapped or ignored rig keeps that name (R185). A record that cannot be read gives null,
    unless a paused file names the rig.
    """
    projects, ignored = config["projects"], name in config["ignore_rigs"]
    record: dict | None = {}
    if RIG_NAME_RE.fullmatch(name):
        try:
            record = rig_record(name)
        except StateError:
            record = None
    held = record or {}
    mark = held.get("state", RUNNING)
    since, reason = held.get("since"), held.get("reason")
    if ignored or name not in projects:
        state = "ignored" if ignored else "unmapped"
        since = None
        reason = None if mark == RUNNING else f"its record says {mark}"
    elif name in listed and (record is None or mark == RUNNING):
        state, since, reason = PAUSED, None, "listed in a paused file"
    else:
        state = None if record is None else mark
    if record is None:
        reason = f"unreadable record: {state_dir() / 'rigs' / f'{name}.json'}"
    elif state == RUNNING:
        notes = [_over_the_cap(held, config).removeprefix("P1: ")]
        if paused_unknown:
            notes.append("a paused file cannot be read: it may be paused")
        reason = "; ".join(note for note in notes if note) or None
    waits = held.get("waits_on")
    row = {
        "name": name,
        "mapped": name in projects,
        "project": projects.get(name),
        "ignored": ignored,
        "tier": tier_of(name, config["priority"]),
        "state": state,
        "since": since,
        "reason": reason,
        "waits_on": waits if isinstance(waits, list) else [],
        "parks_24h": None if record is None else _recent_parks(held),
    }
    if name in listed:
        row["in_paused_file"] = listed[name]
    return row


def _supervisor_view(last_tick: str | None) -> dict:
    """`supervisor.json` (None when absent or unreadable), whether its timer is active, the tick."""
    try:
        record = _supervisor_record()
    except StateError:
        record = None  # status_report's notes name the file
    word = systemctl("is-active", f"{UNIT_NAME}.timer")[1].strip()
    return {
        "record": record,
        "is_active": word if word in UNIT_STATES else "unknown",
        "last_tick": last_tick,
    }


def status_report() -> tuple[dict, list[str]]:
    """The cross-rig view and the notes about what could not be read. It writes nothing.

    The rigs listed are those `rig ps` shows not stopped and those the config, a state record or
    a paused file names.
    """
    config, notes = load_config()
    root = state_dir()
    newer, unreadable = scan_state(root)
    notes += [f"{name}: newer schema (v{major})" for name, major in newer.items()]
    notes += [f"{name}: unreadable" for name in unreadable]
    notes += [f"unreadable record: {path}" for path in unreadable_records()]
    listed, warnings = paused_in_files(config)
    notes += warnings
    statuses, why = rig_statuses()
    if statuses is None:
        notes.append(f"rig ps failed: {why}; which rigs run is unknown")
    names = set(config["projects"]) | set(config["ignore_rigs"])
    names |= {p.stem for p in root.glob("rigs/*.json")}
    names |= {name for name in listed if RIG_NAME_RE.fullmatch(name)}
    names |= set(_not_stopped(statuses or {}))
    for tier in config["priority"]:
        names.update(tier if isinstance(tier, list) else [tier])
    park_log, restored, last_tick = _park_history()
    rigs = []
    for name in sorted(names):
        row = _rig_view(name, config, listed, bool(warnings))
        row["last_restore_s"] = restored.get(name)
        seen = None if statuses is None else statuses.get(name, ["not listed"])
        row["ps_status"] = None if seen is None else ", ".join(sorted(set(seen)))
        rigs.append(row)
    heavy = _heavy_view(config, prune=False)
    queue = _queue_entries(heavy.tickets)
    for entry in queue:
        entry["wait_s"] = entry.pop("waiting_s")
    reserved = None
    if heavy.reserved is not None:
        head, since = heavy.reserved
        reserved = {"job": head.job, "rig": head.rig, "tier": head.tier, "since": since}
    report = {
        "schema": STATUS_SCHEMA,
        "generated_at": _utc(),
        "config": config,
        "rigs": rigs,
        "rig_ps_error": None if statuses is not None else why,
        "holder": heavy.holder,
        "locked": heavy.locked,
        "queue": queue,
        "blocked_head": heavy.blocked_head,
        "reserved_for": reserved,
        "idle_point": heavy.drain,
        "foreign_heavy": heavy.foreign,
        "supervisor": _supervisor_view(last_tick),
        "park_log": park_log,
        "mem_available_mb": heavy.mem_mb,
        "state_dir": str(root),
        "newer_schema_files": sorted(newer),
    }
    return report, notes


def _or(value: object, absent: str = "-") -> str:
    """A value for a status.md line; `absent` for None or an empty list."""
    if value is None or value == []:
        return absent
    return _one_line(", ".join(map(str, value)) if isinstance(value, list) else value)


RIG_COLUMNS = (
    ("rig", "name"),
    ("state", "state"),
    ("rig ps", "ps_status"),
    ("tier", "tier"),
    ("since", "since"),
    ("parks 24 h", "parks_24h"),
    ("last restore (s)", "last_restore_s"),
    ("waits on", "waits_on"),
    ("paused file", "in_paused_file"),
    ("project", "project"),
    ("reason", "reason"),
)


def _rig_table(rigs: Sequence[Mapping]) -> list[str]:
    if not rigs:
        return ["no rig"]
    lines = [
        "| " + " | ".join(title for title, _ in RIG_COLUMNS) + " |",
        "|" + " --- |" * len(RIG_COLUMNS),
    ]
    for row in rigs:
        cells = []
        for _, key in RIG_COLUMNS:
            unknown = key in ("state", "ps_status", "parks_24h")
            cell = _or(row.get(key), "unknown" if unknown else "-")
            cells.append(cell.replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _heavy_lines(report: Mapping) -> list[str]:
    holder = report["holder"]
    if holder:
        text = (
            f"holder: {holder['job']} ({holder['rig'] or 'no rig'}, pid {holder['pid']}, "
            f"{_needs_text(holder['needs'])}) since {holder['since']}"
        )
    elif report["locked"]:
        text = "holder: unknown: the lock is held with no live holder record"
    else:
        text = "holder: none"
    lines = [_one_line(text)]
    lines.append("queue:" if report["queue"] else "queue: empty")
    for entry in report["queue"]:
        held = f", blocked: {entry['blocked']}" if entry["blocked"] else ""
        lines.append(
            _one_line(
                f"- {entry['job']} ({entry['rig'] or 'no rig'}, tier {entry['tier']}, "
                f"{_needs_text(entry['needs'])}) waiting {entry['wait_s']} s{held}"
            )
        )
    if report["blocked_head"]:
        lines.append(report["blocked_head"])
    reserved = report["reserved_for"]
    if reserved:
        lines.append(
            _one_line(
                f"reserved for: {reserved['job']} ({reserved['rig'] or 'no rig'}, tier "
                f"{reserved['tier']}) since {reserved['since']}"
            )
        )
    lines.append("foreign heavy: " + ("" if report["foreign_heavy"] else "none"))
    lines += [f"- {_one_line(line)}" for line in report["foreign_heavy"]]
    lines.append(f"memory available (MB): {_or(report['mem_available_mb'], 'unknown')}")
    return lines


def _supervisor_lines(report: Mapping) -> list[str]:
    supervisor = report["supervisor"]
    record = supervisor["record"]
    if record is None:
        present = os.path.lexists(state_dir() / "supervisor.json")
        version = "unknown: supervisor.json cannot be read" if present else "not installed"
        mode = "unknown" if present else "none"
    else:
        version = _or(record.get("version"), "unknown")
        if record.get("dry_run_mode"):
            mode = "dry-run"
        elif report["config"]["autopark"]["enabled"]:
            mode = "auto-park on"
        else:
            mode = "auto-park off (autopark.enabled is false)"
    return [
        f"version: {version}",
        f"mode: {mode}",
        f"timer: {supervisor['is_active']}",
        f"last tick: {_or(supervisor['last_tick'], 'none logged')}",
    ]


def status_markdown(report: Mapping, notes: Sequence[str]) -> str:
    """status.md: plain text with one table, for a terminal as well."""
    point = report["idle_point"]
    sections = (
        ("Rigs", _rig_table(report["rigs"]) + [f"note: {_one_line(note)}" for note in notes]),
        ("Heavy job", _heavy_lines(report)),
        ("Idle point", ["none" if point is None else f"in force: {_point_text(point)}"]),
        ("Supervisor", _supervisor_lines(report)),
        ("Park log", [_one_line(line) for line in report["park_log"]] or ["none"]),
    )
    out = [
        "# HACF host status",
        "",
        f"schema: {STATE_SCHEMA}",
        f"as of: {report['generated_at']}",
        f"state dir: {_one_line(report['state_dir'])}",
    ]
    for title, lines in sections:
        out += ["", f"## {title}", ""]
        # A table keeps its rows together; every other line stands alone when rendered.
        out += [line if line.startswith(("|", "- ")) else f"{line}  " for line in lines]
    return "\n".join(out) + "\n"


def write_status(report: Mapping, notes: Sequence[str]) -> Path:
    """Replace status.json and status.md in the state dir; returns the path of status.md.

    status.json is the report with the state files' `schema`; its own is under `report`.

    Raises:
        NewerSchema: The host state is newer; nothing is written.
        StateError: status.json exists and is not a state file; nothing is written.
    """

    def mutate(data: dict) -> None:
        data.update(report, report=STATUS_SCHEMA)

    update_state("status.json", mutate)
    root = state_dir()
    path = root / "status.md"
    with state_lock(root):
        _write_atomic(path, status_markdown(report, notes))
    return path


def cmd_status(args: argparse.Namespace) -> int:
    """`--json` prints the view; `--md`, the default, writes it and prints status.md's path."""
    if not args.json:
        _refuse_newer(state_dir())
    report, notes = status_report()
    for line in notes:
        print(_one_line(line), file=sys.stderr)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(write_status(report, notes))
    return EXIT_OK


# ------------------------------------------------------------------------------------ verbs


def cmd_config_check(args: argparse.Namespace) -> int:
    """`config check`: problems fail the check; unmapped running rigs only warn."""
    path = config_path()
    config, problems = load_config(path)
    names, why = running_rigs()
    if names is None:
        warnings = [f"warning: rig ps failed ({_one_line(why)}); unmapped rigs were not checked"]
    else:
        known = set(config["projects"]) | set(config["ignore_rigs"])
        warnings = [
            f"warning: running rig {_one_line(name)} is unmapped: the idle point counts it in "
            "flight and it is never parked"
            for name in names
            if name not in known
        ]
    if args.json:
        report = {
            "schema": CHECK_SCHEMA,
            "ok": not problems,
            "path": str(path),
            "problems": problems,
            "warnings": warnings,
        }
        print(json.dumps(report, indent=2))
    else:
        for line in problems + warnings:
            print(line, file=sys.stderr)
        if not problems:
            print(f"host.yml: ok ({path})")
    return EXIT_FAIL if problems else EXIT_OK


def cmd_boundary(args: argparse.Namespace) -> int:
    """`boundary <rig>`: exit 0 at a boundary, 1 not, 2 when the rig is not mapped."""
    config, notes = load_config()
    for line in notes:
        print(_one_line(line), file=sys.stderr)
    root = _bound_root(args.rig, config)
    if root is None:
        print(_unmapped_line(args.rig), file=sys.stderr)
        return EXIT_USAGE
    report = boundary(args.rig, root)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("\n".join(_boundary_lines(report)))
    return EXIT_OK if report["at_boundary"] else EXIT_FAIL


def _hours(text: str) -> float:
    """A finite number of hours above zero."""
    value = float(text)
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError("must be a number of hours above 0")
    return value


def _count(text: str) -> int:
    """A non-negative integer argument."""
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def cmd_job(args: argparse.Namespace) -> int:
    """`job run` runs a command through the scheduler; `job status` shows it."""
    if args.action == "status":
        data = job_status()
        print(json.dumps(data, indent=2) if args.json else "\n".join(_job_status_lines(data)))
        return EXIT_OK
    command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not command:
        print("hacf-host.py job run: needs a command after --", file=sys.stderr)
        return EXIT_USAGE

    def take() -> Lease:
        return acquire(
            args.job,
            args.wait_s,
            rig_name=job_rig(args, os.environ),
            ram_mb=args.ram_mb,
            gpu=args.gpu,
        )

    return run_job(command, take)


def _acting_refusals(verb: str, rig_name: str) -> tuple[dict, str]:
    """The refusals every verb that stops or starts a rig applies, before any `rig` call.

    Returns:
        (the config, the rig's project root).

    Raises:
        Refused: A seat of a mapped rig (R200), a config this verb cannot trust, a name `rig`
            could misread, an unmapped or ignored rig, or an unreadable paused file.
        NewerSchema: The host state is newer.
    """
    config = _host_refusals()
    if not RIG_NAME_RE.fullmatch(rig_name):
        raise Refused(f"{rig_name}: not a rig name this verb takes")
    root = _bound_root(rig_name, config)
    if root is None:
        raise Refused(_unmapped_line(rig_name))
    _refuse_newer(state_dir())
    _, warnings = paused_in_files(config)
    if warnings:
        raise Refused("; ".join(warnings))
    return config, root


def _host_refusals(keys: Sequence[str] = (), never: str = "stops or starts a rig") -> dict:
    """The config, after the refusals that name no rig: the caller's seat and a broken config.

    Args:
        keys: Further config keys whose problems refuse: ones where a default would act more.
        never: What a seat of a mapped rig never does, for the refusal.

    Raises:
        Refused: A seat of a mapped rig (R200), or a config this verb cannot trust.
    """
    config, problems = load_config()
    session = os.environ.get("OPENRIG_SESSION_NAME", "")
    seat_of = session.split("@")[1] if "@" in session else None
    if seat_of in config["projects"]:
        raise Refused(
            f"{session} is a seat of the mapped rig {seat_of}: a HACF seat never {never}; "
            "the person, or the operator on the person's word, runs this"
        )
    # With one of these keys dropped, a paused or ignored rig could look free. A rig in both
    # projects and ignore_rigs drops nothing: ignored wins.
    unsafe = ("host.yml: (top level)", "host.yml: paused_files", "host.yml: ignore_rigs")
    broken = [p for p in problems if p.startswith(unsafe) and "in both projects" not in p]
    # A projects entry of the wrong type is dropped, which would unbind a seat's rig.
    broken += [p for p in problems if p.startswith("host.yml: projects") and "expected" in p]
    broken += [p for p in problems if p.startswith(tuple(f"host.yml: {key}" for key in keys))]
    if broken:
        raise Refused("; ".join(broken))
    return config


def _refuse_paused(rig_name: str, config: Mapping, record: Mapping) -> None:
    listed, _ = paused_in_files(config)
    if rig_name in listed:
        raise Refused(
            f"{rig_name} is already paused: {rig_name} is listed in {listed[rig_name]}; "
            "remove that line (HACF never edits it)"
        )
    if record.get("state") == PAUSED:
        raise Refused(f"{rig_name} is already paused (use resume {rig_name})")


def _report(outcome: Outcome) -> int:
    """Print each step and the result; the exit code."""
    for step in outcome.steps:
        print(f"{step['step']}: {step['detail']}")
    verdict = "done" if outcome.ok else f"failed: {outcome.reason}"
    state = f" ({outcome.state})" if outcome.state else ""
    print(f"{outcome.verb} {outcome.rig}: {verdict}{state}")
    if outcome.ok:
        return EXIT_OK
    return EXIT_BUSY if outcome.busy else EXIT_FAIL


def _at_boundary(rig_name: str, root: str) -> bool:
    """Read the boundary, printing the checks when the rig is not at one."""
    report = boundary(rig_name, root)
    if not report["at_boundary"]:
        print("\n".join(_boundary_lines(report)))
    return report["at_boundary"]


def cmd_pause(args: argparse.Namespace) -> int:
    """`pause <rig>`: mark a rig at a boundary as the person's; --stop also parks it."""
    config, root = _acting_refusals("pause", args.rig)
    with rig_operation(args.rig):
        _refuse_paused(args.rig, config, rig_record(args.rig))
        if not _at_boundary(args.rig, root):
            return EXIT_FAIL
        by = os.environ.get("OPENRIG_SESSION_NAME") or os.environ.get("USER") or "unknown"
        point = str(write_resume_point(args.rig))

        def mutate(data: dict) -> None:
            data.update(state=PAUSED, since=_utc(), by=by, reason=args.reason)
            data.update(resume_point=point, waits_on=[])
            for key in ("snapshot", *GRACE_KEYS):  # an older park's snapshot is not this pause's
                data.pop(key, None)

        update_state(f"rigs/{args.rig}.json", mutate)
        print(f"{args.rig}: {PAUSED}; resume point: {point}")
        if not args.stop:
            return EXIT_OK
        return _report(park_rig(args.rig, by, args.reason))


def cmd_resume(args: argparse.Namespace) -> int:
    """`resume <rig>`: restore it when it is stopped, then clear the mark."""
    config, _ = _acting_refusals("resume", args.rig)
    listed, _ = paused_in_files(config)
    if args.rig in listed:
        raise Refused(
            f"{args.rig} is listed in {listed[args.rig]}; remove that line (HACF never edits it)"
        )
    with rig_operation(args.rig):
        record = rig_record(args.rig)
        if record.get("state", RUNNING) == RUNNING:
            print(f"{args.rig}: not paused or parked; nothing to resume")
            return EXIT_OK
        seen, detail = observe(args.rig)
        if seen == "stopped":
            if not record.get("snapshot"):
                raise Refused(
                    f"{args.rig} is stopped and HACF recorded no snapshot of it; start it with "
                    f"`rig up {args.rig} --existing`, then resume"
                )
            return _report(restore_rig(args.rig))
        # A pause that never stopped the rig has no restore to judge: its mark may go.
        restored = record["state"] != PAUSED or bool(record.get("snapshot"))
        if seen == "unknown" or (seen == "partial" and restored):
            print(f"resume {args.rig}: failed: the rig is not whole or stopped ({detail})")
            return EXIT_FAIL
        sent, told = send_lead(args.rig, record.get("resume_point"))
        print(f"lead message: {told if sent else f'failed: {told}'}")
        mark_running(args.rig)
        print(f"resume {args.rig}: done ({RUNNING})")
        return EXIT_OK


def cmd_park(args: argparse.Namespace) -> int:
    """`park <rig>`: the person's park of a rig at a boundary."""
    config, root = _acting_refusals("park", args.rig)
    with rig_operation(args.rig):
        _refuse_paused(args.rig, config, rig_record(args.rig))
        if not _at_boundary(args.rig, root):
            return EXIT_FAIL
        return _report(park_rig(args.rig, "person", "manual park"))


def cmd_restore(args: argparse.Namespace) -> int:
    """`restore <rig>`: the person's restore of a parked or restore-failed rig."""
    config, _ = _acting_refusals("restore", args.rig)
    with rig_operation(args.rig):
        record = rig_record(args.rig)
        _refuse_paused(args.rig, config, record)
        if record.get("state") not in (PARKED, FAILED):
            raise Refused(f"{args.rig} is not parked or restore-failed; nothing to restore")
        return _report(restore_rig(args.rig))


def cmd_idle_point(args: argparse.Namespace) -> int:
    """`idle-point request|wait|release|status` (R187, R198)."""
    if args.action in ("request", "release"):  # R200
        _host_refusals(never="drains the host or ends a drain")
    if args.action == "request":
        return request_idle_point(args.purpose, args.ttl)
    if args.action == "release":
        return release_idle_point()
    if args.action == "wait":
        for line in load_config()[1]:
            print(_one_line(line), file=sys.stderr)
        return wait_for_idle(args.timeout)
    report = idle_status()
    point = report["idle_point"]
    if args.json:
        print(json.dumps(report, indent=2))
    elif point is None:
        print("idle point: none")
    else:
        word = "" if report["in_force"] else "expired, counts as absent: "
        print(f"idle point: {word}{_point_text(point)}")
    return EXIT_OK


def cmd_unbuilt(args: argparse.Namespace) -> int:
    """A verb a later build adds."""
    print(f"hacf-host.py {args.verb}: not in this build", file=sys.stderr)
    return EXIT_USAGE


# verb → handler. A verb with sub-verbs has one handler per verb.
HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "job": cmd_job,
    "status": cmd_status,
    "boundary": cmd_boundary,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "park": cmd_park,
    "restore": cmd_restore,
    "supervise": cmd_supervise,
    "supervisor": cmd_supervisor,
    "idle-point": cmd_idle_point,
    "config": cmd_config_check,
}
SUB_VERBS = {
    "job": ("run", "status"),
    "supervisor": ("install", "remove", "status"),
    "idle-point": ("request", "wait", "release", "status"),
    "config": ("check",),
}
VERB_HELP = {
    "job": "run a heavy job through the scheduler, or show it",
    "status": "show the host (--md, the default, or --json)",
    "boundary": "is a rig at a slice boundary",
    "pause": "pause a rig at a slice boundary",
    "resume": "resume a paused or parked rig",
    "park": "stop a rig at a slice boundary, with a snapshot",
    "restore": "restore a parked or restore-failed rig",
    "supervise": "one auto-park pass",
    "supervisor": "install, remove or show the auto-park timer",
    "idle-point": "drain the host before maintenance",
    "config": "check the host config",
}


def build_parser() -> argparse.ArgumentParser:
    """The command line. A verb not built yet takes any further arguments."""
    summary, _, details = (__doc__ or "").partition("\n")
    parser = argparse.ArgumentParser(
        prog="hacf-host.py",
        description=f"{summary}\n{details}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    verbs = parser.add_subparsers(dest="verb", required=True, metavar="<verb>")
    for verb in HANDLERS:
        sub = verbs.add_parser(verb, help=VERB_HELP[verb], allow_abbrev=False)
        if verb == "status":
            form = sub.add_mutually_exclusive_group()
            form.add_argument("--json", action="store_true", help="print the status as JSON")
            form.add_argument("--md", action="store_true", help="write status.md and status.json")
        if verb in ("boundary", "pause", "resume", "park", "restore"):
            sub.add_argument("rig", help="a rig mapped in host.yml projects")
        if verb == "supervise":
            sub.add_argument("--once", action="store_true", required=True, help="run one tick")
            sub.add_argument("--dry-run", action="store_true", help="only log what it would do")
        if verb == "boundary":
            sub.add_argument("--json", action="store_true", help="print a JSON report")
        if verb == "pause":
            sub.add_argument("--reason", required=True, help="why, for the record")
            sub.add_argument("--stop", action="store_true", help="also park the rig")
        if verb in SUB_VERBS:
            actions = sub.add_subparsers(dest="action", required=True, metavar="<action>")
            for action in SUB_VERBS[verb]:
                leaf = actions.add_parser(action, allow_abbrev=False)
                if verb == "config" or (verb, action) == ("job", "status"):
                    leaf.add_argument("--json", action="store_true", help="print a JSON report")
                if (verb, action) == ("idle-point", "status"):
                    leaf.add_argument("--json", action="store_true", help="print a JSON report")
                if (verb, action) == ("idle-point", "request"):
                    leaf.add_argument("--purpose", required=True, help="what it is for")
                    leaf.add_argument(
                        "--ttl", type=_hours, default=IDLE_TTL_H, metavar="HOURS",
                        help="it expires after this many hours (default 6)",
                    )  # fmt: skip
                if (verb, action) == ("idle-point", "wait"):
                    leaf.add_argument(
                        "--timeout", type=_count, default=IDLE_WAIT_S, metavar="S",
                        help="seconds to wait at most (default 3600)",
                    )  # fmt: skip
                if (verb, action) == ("supervisor", "install"):
                    leaf.add_argument(
                        "--dry-run-mode", action="store_true", help="the ticks only log"
                    )
                if (verb, action) == ("job", "run"):
                    leaf.add_argument("--job", required=True, help="the job's name")
                    leaf.add_argument("--ram-mb", type=_count, default=0, metavar="N")
                    leaf.add_argument("--gpu", action="store_true", help="the job needs the GPU")
                    leaf.add_argument("--rig", help="else OPENRIG_SESSION_NAME's rig")
                    leaf.add_argument("--wait-s", type=float, metavar="N")
                    leaf.add_argument("argv", nargs=argparse.REMAINDER, help="-- then the command")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one verb and return its exit code."""
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    handler = HANDLERS[args.verb]
    if extra and handler is not cmd_unbuilt:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if yaml is None:
        sys.stderr.write("hacf-host.py: PyYAML is not installed\n")
        return EXIT_USAGE
    try:
        return handler(args)
    except (NewerSchema, RigBusy) as error:
        print(_one_line(error), file=sys.stderr)
        return EXIT_BUSY
    except Refused as error:
        print(f"hacf-host.py {args.verb}: refused: {_one_line(error)}", file=sys.stderr)
        return EXIT_USAGE
    except (StateError, OSError) as error:
        print(f"hacf-host.py: {_one_line(error)}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
