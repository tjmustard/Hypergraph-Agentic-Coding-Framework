#!/usr/bin/env python3
"""List, and with --apply remove, HACF's own leftovers on this host. A dev tool, not shipped.

    python3 dev/scripts/host_sweep.py [--apply] [--json]

It looks at three things, and at nothing else:

- Run dirs: real directories of this user named openrig-compat-?????? directly under a root. The
  roots are every directory under $HOME/.cache/hacf-tmp/, plus /dev/shm and /tmp. One is stale when
  the pid in its state/owner.json is dead, or when it has no owner record and its mtime is over
  24 h old. An owner record that names no pid keeps the dir.
- Listeners: this user's `python openrig_compat.py listen --port-file <run dir>/state/port`
  processes whose run dir is directly under a root. One is stale when that dir is missing or its
  owner pid is dead.
- Test rigs: `rig ps --json` names that fully match dev/test_rig.py's pattern. One is stale when
  the UTC stamp ending its name is over 6 h old. Another `hacf-test-*` name is listed, never
  deleted.

Without --apply nothing changes. With it, each stale listener gets SIGTERM, then SIGKILL after 5 s,
each time only after /proc shows the same process again (command line, run dir, uid and start
time); each stale run dir is judged again and removed; each stale rig is deleted with
`rig down <name> --delete`. Deleting cannot be undone.

HACF_SWEEP_ROOTS (a test seam, os.pathsep separated) replaces the roots; set but empty, there are
none.

Exit: 0 nothing stale, or with --apply everything stale was handled; 1 something is stale, or was
left after --apply; 2 usage, or the rig list could not be read (so neither answer is known).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

RUN_DIR_RE = re.compile(r"openrig-compat-[A-Za-z0-9]{6}")
# dev/test_rig.py's P87 pattern (a test holds the two equal): the only rigs that may be deleted.
RIG_RE = re.compile(r"hacf-test-[a-z0-9][a-z0-9._-]*-[0-9]{8}T[0-9]{6}")
RIG_PREFIX = "hacf-test-"
NOT_SWEPT = "not swept: name does not match the test-rig pattern"
PYTHON_RE = re.compile(r"python[0-9.]*")
LISTENER_MODULE = "openrig_compat.py"
OWNERLESS_AGE_S = 24 * 3600
RIG_AGE_H = 6.0
GRACE_S = 5.0  # a listener's time between SIGTERM and SIGKILL
POLL_S = 0.1
PS_TIMEOUT_S = 60.0
DOWN_TIMEOUT_S = 300.0
MAX_RECORD_BYTES = 4096
PROC = Path("/proc")  # a test reads a fake tree instead
MY_UID = os.getuid()


def send_signal(pid: int, sig: int) -> None:
    """The one place a signal is sent; stop_listener has just identified the pid."""
    os.kill(pid, sig)


def log(text: str) -> None:
    """A line about an action not taken or taken, on stderr."""
    print(text, file=sys.stderr, flush=True)


# ------------------------------------------------------------------------------------ roots


def sweep_roots() -> list[Path]:
    """The real directories whose direct children may be run dirs, each once."""
    seam = os.environ.get("HACF_SWEEP_ROOTS")
    if seam is not None:
        found = [Path(p) for p in seam.split(os.pathsep) if os.path.isabs(p)]
    else:
        found = []
        home = os.environ.get("HOME", "")
        if os.path.isabs(home):
            try:
                children = sorted(Path(home, ".cache", "hacf-tmp").iterdir())
            except OSError:
                children = []
            found = [c for c in children if not c.is_symlink()]
        found += [Path("/dev/shm"), Path("/tmp")]
    return list(dict.fromkeys(Path(os.path.realpath(p)) for p in found if p.is_dir()))


def _my_dir(path: Path) -> os.stat_result | None:
    """The lstat of a real directory this user owns; None for anything else."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    return info if stat.S_ISDIR(info.st_mode) and info.st_uid == MY_UID else None


# ------------------------------------------------------------------------------------ run dirs


def pid_alive(pid: int) -> bool:
    """Whether a process holds this pid; in doubt, it does."""
    try:
        os.kill(pid, 0)  # signal 0 sends nothing
    except ProcessLookupError:
        return False
    except (OSError, OverflowError):
        return True
    return True


def owner_of(run_dir: Path) -> tuple[bool, int | None]:
    """(whether there is an owner record, the pid it names or None when it is unreadable)."""
    state = run_dir / "state"
    record = state / "owner.json"
    try:
        if not stat.S_ISDIR(os.lstat(state).st_mode):
            return True, None  # a linked or odd state dir: unknown, so the dir is kept
        fd = os.open(record, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False, None
    except OSError:
        return True, None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return True, None
        data = json.loads(os.read(fd, MAX_RECORD_BYTES))
    except (OSError, ValueError, RecursionError):
        return True, None
    finally:
        os.close(fd)
    pid = data.get("pid") if isinstance(data, dict) else None
    return True, pid if type(pid) is int and pid > 0 else None


def judge_run_dir(path: Path, mtime: float) -> tuple[bool, str]:
    """(stale, why) of a run dir."""
    recorded, pid = owner_of(path)
    if recorded:
        if pid is None:
            return False, "unreadable owner record"
        if pid_alive(pid):
            return False, f"owner pid {pid} is alive"
        return True, f"owner pid {pid} is dead"
    age_h = (time.time() - mtime) / 3600
    if age_h * 3600 > OWNERLESS_AGE_S:
        return True, f"no owner record, unchanged for {age_h:.0f} h"
    return False, "no owner record, not over 24 h old"


def tree_size(path: Path) -> int:
    """The bytes of the files and links under a dir; no link is followed."""
    total = 0
    for folder, dirs, files in os.walk(path, followlinks=False):
        for name in (*dirs, *files):
            try:
                info = os.lstat(os.path.join(folder, name))
            except OSError:
                continue
            if not stat.S_ISDIR(info.st_mode):
                total += info.st_size
    return total


def list_run_dirs(roots: Sequence[Path]) -> list[dict]:
    """Every run dir directly under a root."""
    found = []
    for root in roots:
        try:
            names = sorted(os.listdir(root))
        except OSError as error:
            log(f"cannot list {root}: {error.strerror}")
            continue
        for name in names:
            if not RUN_DIR_RE.fullmatch(name):
                continue
            path = root / name
            info = _my_dir(path)
            if info is None:
                continue
            stale, reason = judge_run_dir(path, info.st_mtime)
            entry = {"path": str(path), "stale": stale, "reason": reason}
            found.append({**entry, "size_bytes": tree_size(path)})
    return found


def remove_run_dir(entry: dict, roots: Sequence[Path]) -> str:
    """Judge a listed run dir again, then remove it; what happened."""
    path = Path(entry["path"])
    real = Path(os.path.realpath(path))
    info = _my_dir(path)
    if (
        real != path
        or real.parent not in roots
        or not RUN_DIR_RE.fullmatch(real.name)
        or info is None
    ):
        return "not removed: not a run dir directly under a root"
    stale, reason = judge_run_dir(real, info.st_mtime)
    if not stale:
        return f"not removed: {reason}"
    try:
        shutil.rmtree(real)
    except OSError as error:
        return f"failed: {error.strerror or error}"
    return "removed"


# ------------------------------------------------------------------------------------ listeners


def read_stat(pid: int) -> tuple[str, int] | None:
    """(state, start time: field 22) of a pid, parsed after the last `)`; None when gone."""
    try:
        text = (PROC / str(pid) / "stat").read_bytes().decode("utf-8", "replace")
        fields = text[text.rindex(")") + 1 :].split()
        return fields[0], int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def listener_run_dir(argv: Sequence[str]) -> Path | None:
    """The run dir of `python [-flags] openrig_compat.py listen --port-file <run>/state/port`."""
    if not argv or not PYTHON_RE.fullmatch(os.path.basename(argv[0])):
        return None
    at = 1
    while at < len(argv) and argv[at].startswith("-") and argv[at] not in ("-c", "-m"):
        at += 1
    rest = argv[at : at + 4]
    if (
        len(rest) < 4
        or os.path.basename(rest[0]) != LISTENER_MODULE
        or rest[1:3] != ["listen", "--port-file"]
    ):
        return None
    port = rest[3]
    if not os.path.isabs(port) or os.path.normpath(port) != port:
        return None
    path = Path(port)
    if path.name != "port" or path.parent.name != "state":
        return None
    run_dir = path.parent.parent
    return run_dir if RUN_DIR_RE.fullmatch(run_dir.name) else None


def identify(pid: int, roots: Sequence[Path]) -> tuple[Path, int] | None:
    """(run dir, start time) when /proc shows this user's compat listener over a root's run dir.

    The start time is read on both sides of the command line, so the two are one process's.
    """
    before = read_stat(pid)
    try:
        uid = os.stat(PROC / str(pid)).st_uid
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    after = read_stat(pid)
    if uid != MY_UID or before is None or after is None or before[1] != after[1]:
        return None
    argv = [os.fsdecode(a) for a in raw.removesuffix(b"\0").split(b"\0")]
    run_dir = listener_run_dir(argv)
    if run_dir is None or run_dir.is_symlink():
        return None
    parent = Path(os.path.realpath(run_dir.parent))
    if parent not in roots:
        return None
    return parent / run_dir.name, after[1]


def _missing(path: Path) -> bool:
    """Whether nothing is at the path; a path that cannot be looked at is not missing."""
    try:
        os.lstat(path)
    except OSError as error:
        return isinstance(error, FileNotFoundError)
    return False


def list_listeners(roots: Sequence[Path]) -> list[dict]:
    """Every compat listener over a run dir under a root."""
    try:
        pids = sorted(int(n) for n in os.listdir(PROC) if n.isdigit())
    except OSError as error:
        log(f"cannot list {PROC}: {error.strerror}")
        return []
    found = []
    for pid in pids:
        seen = identify(pid, roots)
        if seen is None:
            continue
        run_dir, start = seen
        if _missing(run_dir):
            stale, reason = True, "run dir missing"
        else:
            recorded, owner = owner_of(run_dir)
            stale = recorded and owner is not None and not pid_alive(owner)
            reason = f"owner pid {owner} is dead" if stale else "its owner is not known to be dead"
        entry = {"pid": pid, "start_time": start, "run_dir": str(run_dir)}
        found.append({**entry, "stale": stale, "reason": reason})
    return found


def _gone(pid: int) -> bool:
    """Whether the pid has no process, or only one that has exited."""
    seen = read_stat(pid)
    return seen is None or seen[0] in ("Z", "X")


def _refusal(entry: dict, roots: Sequence[Path]) -> str | None:
    """Why no signal may go to a listed listener now (P93); None when it is the same one."""
    pid = entry["pid"]
    if pid <= 1 or pid == os.getpid():  # a group, init or the sweep itself
        return f"skip {pid}: not the listener"
    if _gone(pid):
        return f"skip {pid}: gone"
    if identify(pid, roots) != (Path(entry["run_dir"]), entry["start_time"]):
        return f"skip {pid}: not the listener"
    return None


def stop_listener(entry: dict, roots: Sequence[Path] | None = None) -> str:
    """SIGTERM a listed listener, SIGKILL it after the grace; each only after a fresh check."""
    roots = sweep_roots() if roots is None else roots
    pid = entry["pid"]
    for sig, done in ((signal.SIGTERM, "terminated"), (signal.SIGKILL, "killed")):
        why = _refusal(entry, roots)
        if why is not None:
            log(why)
            return why
        try:
            send_signal(pid, sig)
        except ProcessLookupError:
            log(f"skip {pid}: gone")
            return f"skip {pid}: gone"
        except OSError as error:
            return f"failed: {error.strerror}"
        if sig == signal.SIGKILL:
            break
        deadline = time.monotonic() + GRACE_S
        while not _gone(pid) and time.monotonic() < deadline:
            time.sleep(POLL_S)
        if _gone(pid):
            break
    return f"{done} {pid}"


# ------------------------------------------------------------------------------------ test rigs


def rig(*argv: str, timeout: float) -> tuple[int | None, str, str]:
    """Run `rig <argv>` from PATH: (exit code or None when it did not finish, stdout, why)."""
    try:
        done = subprocess.run(
            ["rig", *argv],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except OSError as error:
        return None, "", f"cannot run rig: {error.strerror}"
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout:g} s"
    return done.returncode, done.stdout, f"exited {done.returncode}"


def judge_rig(name: str, now: datetime) -> dict | None:
    """A rig's entry; None for a rig that is not named hacf-test-*."""
    if not name.startswith(RIG_PREFIX):
        return None
    entry = {"name": name, "stale": False, "age_h": None, "reason": NOT_SWEPT}
    if not RIG_RE.fullmatch(name):
        return entry
    try:
        made = datetime.strptime(name[-15:], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return {**entry, "reason": f"{NOT_SWEPT} (the stamp is not a date)"}
    age = (now - made).total_seconds() / 3600
    stale = age > RIG_AGE_H
    reason = f"age {age:.1f} h" + ("" if stale else ", not over 6 h")
    return {**entry, "stale": stale, "age_h": round(age, 2), "reason": reason}


def list_rigs() -> tuple[list[dict], str | None]:
    """(the hacf-test-* rigs, why the list is unknown or None)."""
    code, out, why = rig("ps", "--json", timeout=PS_TIMEOUT_S)
    if code != 0:
        return [], f"rig ps --json {why}"
    try:
        rows = json.loads(out)
    except ValueError:
        rows = None
    if not isinstance(rows, list) or not all(
        isinstance(r, dict) and isinstance(r.get("name"), str) for r in rows
    ):
        return [], "rig ps --json gave no rig list"
    now = datetime.now(timezone.utc)
    names = dict.fromkeys(r["name"] for r in rows)
    return [e for e in (judge_rig(n, now) for n in names) if e is not None], None


def delete_rig(name: str) -> str:
    """The one `rig down --delete`: only for a name this call itself judges stale."""
    entry = judge_rig(name, datetime.now(timezone.utc))
    if entry is None or not entry["stale"]:
        return "not deleted: not a test rig over 6 h old"
    code, _, why = rig("down", name, "--delete", "--yes", "--json", timeout=DOWN_TIMEOUT_S)
    return "deleted" if code == 0 else f"failed: rig down {why}"


# ------------------------------------------------------------------------------------ the report


def _line(kind: str, entry: dict) -> str:
    word = "stale" if entry["stale"] else "kept"
    if kind == "run dir":
        size = f"{entry['size_bytes'] / 1_000_000:.1f} MB"
        text = f"run dir {word}: {entry['path']} ({size}; {entry['reason']})"
    elif kind == "listener":
        text = f"listener {word}: pid {entry['pid']} {entry['run_dir']} ({entry['reason']})"
    elif entry["reason"].startswith(NOT_SWEPT):
        name = entry["name"] if entry["name"].isprintable() else repr(entry["name"])
        text = f"rig {entry['reason']}: {name}"
    else:
        text = f"rig {word}: {entry['name']} ({entry['reason']})"
    return text + (f" -> {entry['result']}" if "result" in entry else "")


def sweep(apply: bool) -> dict:
    """List everything and, with `apply`, handle what is stale. Listeners go before their dirs."""
    roots = sweep_roots()
    dirs = list_run_dirs(roots)
    listeners = list_listeners(roots)
    rigs, unknown = list_rigs()
    if apply:
        for entry in listeners:
            if entry["stale"]:
                entry["result"] = stop_listener(entry, roots)
        for entry in dirs:
            if entry["stale"]:
                entry["result"] = remove_run_dir(entry, roots)
        for entry in rigs:
            if entry["stale"]:
                entry["result"] = delete_rig(entry["name"])
    report = {"applied": apply, "roots": [str(r) for r in roots], "run_dirs": dirs}
    return {**report, "listeners": listeners, "rigs": rigs, "rigs_unknown": unknown}


def left_over(report: dict) -> int:
    """The stale entries that are still there."""
    handled = ("removed", "terminated ", "killed ", "deleted")
    count = 0
    for entry in (*report["run_dirs"], *report["listeners"], *report["rigs"]):
        result = entry.get("result", "")
        gone = result.startswith(handled) or result.endswith(": gone")
        count += entry["stale"] and not gone
    return count


def main(argv: Sequence[str] | None = None) -> int:
    """Run the sweep; the exit code is in the module's docstring."""
    parser = argparse.ArgumentParser(prog="host_sweep.py", description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--apply", action="store_true", help="remove, stop and delete what is stale"
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    options = parser.parse_args(argv)
    report = sweep(options.apply)
    left = left_over(report)
    if options.json:
        print(json.dumps(report, indent=2))
    else:
        for kind, key in (("run dir", "run_dirs"), ("listener", "listeners"), ("rig", "rigs")):
            for entry in report[key]:
                print(_line(kind, entry))
        if report["rigs_unknown"]:
            print(f"rigs: unknown: {report['rigs_unknown']}")
        print(f"host_sweep: {left} stale {'left' if options.apply else 'found'}")
    if report["rigs_unknown"]:
        return 2
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
