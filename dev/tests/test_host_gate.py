"""Tests of dev/scripts/host_gate.py, the host heavy-job lock and memory budget (OPR.99.0.6.2).

Every test points HACF_HEAVY_LOCK_PATH under tmp_path, clears any inherited token, polls fast and
reads a fake meminfo (HACF_HOST_MEMINFO), so the user's real lock is never held or read here. The
heavy processes are short-lived ones these tests start themselves.
"""

from __future__ import annotations

import fcntl
import inspect
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import conftest as dev_conftest
import pytest
from conftest import REPO_ROOT, clean_env

SCRIPTS = REPO_ROOT / "dev" / "scripts"
TOOL = SCRIPTS / "host_gate.py"
PROBE_SCRIPT = REPO_ROOT / "dev" / "openrig_probe_record.sh"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import host_gate as hg  # noqa: E402

HIGH = "MemTotal: 33554432 kB\nMemAvailable: 16777216 kB\n"  # 16384 MB
LOW = "MemTotal: 33554432 kB\nMemAvailable: 102400 kB\n"  # 100 MB


@pytest.fixture
def lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The lock under tmp_path, the floor met, fast polls and no inherited token."""
    path = tmp_path / "state" / "heavy-job.lock"
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(HIGH)
    monkeypatch.setenv("HACF_HEAVY_LOCK_PATH", str(path))
    monkeypatch.setenv("HACF_HOST_MEMINFO", str(meminfo))
    monkeypatch.setenv("HACF_HEAVY_POLL_S", "0.05")
    monkeypatch.setenv("HACF_HEAVY_WAIT_S", "20")
    for name in ("HACF_HEAVY_LOCK_HELD", "HACF_HEAVY_LOCK_HOLDER_PID", "HACF_TEST_WORKERS_SHARED"):
        monkeypatch.delenv(name, raising=False)
    return path


def child_env(**extra: str) -> dict[str, str]:
    """The test's environment for a child, minus this run's own PYTEST_* settings.

    An xdist worker's PYTEST_XDIST_WORKER would make a child pytest act as a worker.
    """
    env = clean_env(**extra)
    for name in [k for k in env if k.startswith("PYTEST_")]:
        env.pop(name)
    return env


def gate(*args: str, **env: str) -> subprocess.Popen:
    """Start `host_gate.py <args>` with the test's environment (plus `env`)."""
    return subprocess.Popen(
        [sys.executable, str(TOOL), *args],
        env=child_env(**env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def finish(proc: subprocess.Popen, timeout: float = 30) -> tuple[int, str, str]:
    """Wait for a started process; return its exit code, stdout and stderr."""
    out, err = proc.communicate(timeout=timeout)
    return proc.returncode, out, err


def log_events(path: Path) -> list[tuple[str, str]]:
    """(event, job) for each line of the lock's log."""
    log = path.with_suffix(".log")
    if not log.exists():
        return []
    return [
        (ln.split()[1], ln.split()[3].removeprefix("job=")) for ln in log.read_text().splitlines()
    ]


def wait_for(predicate, timeout: float = 10.0) -> None:
    """Poll `predicate` until it is true; fail after `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting")
        time.sleep(0.02)


def locked_now(path: Path) -> bool:
    """A probe like `flock -n`: True when another open file description holds the lock."""
    fd = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def dead_pid() -> int:
    """A pid that no longer runs (a reaped child)."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def write_record(path: Path, pid: int, token: str) -> None:
    """Write a holder record by hand beside the test's lock (a forged or stale holder)."""
    record = {"schema": hg.SCHEMA, "pid": pid, "token": token, "job": "forged", "cwd": "/x",
              "started_at": "2026-11-23T00:00:00Z"}  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".json").write_text(json.dumps(record))


PY = sys.executable


# ----------------------------------------------------------------------------- serialise, memory


def test_two_concurrent_runs_serialise(lock: Path, tmp_path: Path) -> None:
    """The second run prints the holder and starts only after the first exits; no overlap."""
    marker = tmp_path / "first-done"
    job = f"import time, pathlib; time.sleep(1.0); pathlib.Path({str(marker)!r}).touch()"
    first = gate("run", "--job", "first", "--", PY, "-c", job)
    wait_for(lambda: log_events(lock) == [("acquire", "first")])
    second = gate("run", "--job", "second", "--", PY, "-c",
                  f"import pathlib; print(pathlib.Path({str(marker)!r}).exists())")  # fmt: skip
    code1, _, _ = finish(first)
    code2, out2, err2 = finish(second)
    assert (code1, code2) == (0, 0)
    assert out2 == "True\n"  # the first had finished when the second's job started
    assert "second waiting: held by first (pid " in err2
    assert log_events(lock) == [
        ("acquire", "first"),
        ("release", "first"),
        ("acquire", "second"),
        ("release", "second"),
    ]
    assert not lock.with_suffix(".json").exists()


def test_low_memory_waits_without_the_lock_then_exits_75(lock: Path, tmp_path: Path) -> None:
    """Below the floor the run waits without holding the lock, then exits 75.

    A third job takes and releases the lock meanwhile; --wait-s bounds the wait.
    """
    low = tmp_path / "meminfo-low"
    low.write_text(LOW)
    waiter = gate(
        "run", "--job", "starved", "--wait-s", "2", "--", "true", HACF_HOST_MEMINFO=str(low)
    )
    time.sleep(0.3)
    third = gate("run", "--job", "third", "--wait-s", "1", "--", "true")
    assert finish(third)[0] == 0
    assert waiter.poll() is None  # still waiting while the third job held and released the lock
    code, _, err = finish(waiter)
    assert code == 75
    assert "host busy: MemAvailable 100 MB < 6144 MB; retry later (exit 75)" in err
    assert log_events(lock) == [("acquire", "third"), ("release", "third")]


def test_memory_dropping_right_after_acquiring_releases_and_rewaits(
    lock: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c): the floor re-read under the lock fails, so the lock is released and the wait goes on."""
    readings = iter([8000, 100, 8000, 8000])
    seen_unlocked = []

    def fake_mem() -> int:
        value = next(readings)
        if lock.exists():
            seen_unlocked.append(not locked_now(lock))
        return value

    monkeypatch.setattr(hg, "mem_available_mb", fake_mem)
    messages = []

    class Out:
        def write(self, text: str) -> None:
            messages.append(text)

        def flush(self) -> None:
            pass

    with hg.acquire("dropper", 5, out=Out()) as lease:
        assert not lease.reentrant
    assert "MemAvailable 100 MB < 6144 MB right after acquiring; released" in "".join(messages)
    assert seen_unlocked[-2] is True  # the next pass's memory check ran with the lock released
    assert log_events(lock) == [("acquire", "dropper"), ("release", "dropper")]


# --------------------------------------------------------------------------------------- nesting


def test_nested_runs_complete_without_waiting(lock: Path) -> None:
    """Run -- run -- <child>: the inner run sees a valid token and runs in place."""
    proc = gate("run", "--job", "outer", "--wait-s", "2", "--", PY, str(TOOL), "run", "--job",
                "inner", "--wait-s", "2", "--", PY, "-c", "print('inner ran')")  # fmt: skip
    code, out, err = finish(proc)
    assert code == 0, err
    assert out == "inner ran\n"
    assert log_events(lock) == [("acquire", "outer"), ("release", "outer")]


def tiny_suite(tmp_path: Path, record: Path | None = None) -> Path:
    """A tiny test dir whose conftest is a copy of dev/tests/conftest.py's lock hook.

    With `record`, a third test writes the run's `os.environ["CBM_RUNTIME_DIR"]` to that file.
    """
    suite = tmp_path / "tiny"
    suite.mkdir()
    hook = "\n\n".join(
        inspect.getsource(f)
        for f in (dev_conftest.pytest_configure, dev_conftest.pytest_unconfigure)
    )
    (suite / "conftest.py").write_text(
        "import os\nimport sys\nfrom pathlib import Path\n\nimport pytest\n\n"
        f"DEV_SCRIPTS_DIR = Path({str(SCRIPTS)!r})\n"
        "_HEAVY_LEASE = pytest.StashKey[object]()\n\n\n" + hook
    )
    (suite / "test_tiny.py").write_text(
        "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n"
    )
    if record is not None:
        (suite / "test_record.py").write_text(
            "import os\nfrom pathlib import Path\n\n\ndef test_record():\n"
            f"    Path({str(record)!r}).write_text(os.environ['CBM_RUNTIME_DIR'])\n"
        )
    return suite


def pytest_argv(suite: Path, *extra: str) -> list[str]:
    """A child pytest of the tiny suite, without this repository's ini or cache."""
    return [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(suite),
            "-c", os.devnull, *extra, str(suite)]  # fmt: skip


def test_run_of_a_pytest_with_the_hook_does_not_wait(lock: Path, tmp_path: Path) -> None:
    """Run -- python -m pytest <dir>: the hook sees the run's valid token and takes nothing."""
    suite = tiny_suite(tmp_path)
    proc = gate("run", "--job", "outer", "--", *pytest_argv(suite), HACF_HEAVY_WAIT_S="2")
    code, out, err = finish(proc, 120)
    assert code == 0, out + err
    assert log_events(lock) == [("acquire", "outer"), ("release", "outer")]


def test_a_direct_pytest_takes_the_lock(lock: Path, tmp_path: Path) -> None:
    """The conftest hook: a plain pytest run takes and releases the lock (job pytest)."""
    suite = tiny_suite(tmp_path)
    proc = subprocess.run(pytest_argv(suite), env=child_env(), capture_output=True, text=True,
                          timeout=120, check=False)  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert log_events(lock) == [("acquire", "pytest"), ("release", "pytest")]


def test_only_the_xdist_controller_takes_the_lock(lock: Path, tmp_path: Path) -> None:
    """With -n 2 the log shows one acquire: the workers never take the lock."""
    suite = tiny_suite(tmp_path)
    proc = subprocess.run(pytest_argv(suite, "-n", "2"), env=child_env(), capture_output=True,
                          text=True, timeout=120, check=False)  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 passed" in proc.stdout
    assert log_events(lock) == [("acquire", "pytest"), ("release", "pytest")]


def test_a_busy_host_ends_a_direct_pytest_with_75(lock: Path, tmp_path: Path) -> None:
    """The hook's timeout is pytest.exit(..., returncode=75)."""
    suite = tiny_suite(tmp_path)
    low = tmp_path / "meminfo-low"
    low.write_text(LOW)
    proc = subprocess.run(pytest_argv(suite), capture_output=True, text=True, timeout=120,
                          env=child_env(HACF_HOST_MEMINFO=str(low), HACF_HEAVY_WAIT_S="0"),
                          check=False)  # fmt: skip
    assert proc.returncode == 75, proc.stdout + proc.stderr
    assert "host busy: MemAvailable 100 MB < 6144 MB; retry later" in proc.stdout + proc.stderr


# ------------------------------------------------------------------- the run's CBM_RUNTIME_DIR


@pytest.fixture
def short_dir():
    """Make short dirs under /tmp (as the gate's own CBM runtime dir is); remove each after."""
    made: list[Path] = []

    def make(tag: str) -> Path:
        path = Path(tempfile.mkdtemp(prefix=f"hacf-hg-{tag}.", dir="/tmp"))
        made.append(path)
        return path

    yield make
    for path in made:
        shutil.rmtree(path, ignore_errors=True)


def long_dir(tmp_path: Path, tag: str) -> Path:
    """An existing absolute dir whose path is over the socket budget (dir + 48 > 107 bytes)."""
    path = tmp_path / tag / ("x" * 80)
    path.mkdir(parents=True)
    assert len(os.fsencode(str(path))) + 48 > 107
    return path


def snapshot(path: Path) -> tuple[str, list[tuple[str, bytes]]]:
    """A dir's mode and its files' names and bytes."""
    return oct(path.stat().st_mode), sorted((p.name, p.read_bytes()) for p in path.iterdir())


def runtime_run(tmp_path: Path, caller: str | None, **env: str):
    """Run the tiny suite with the lock off; return (process, the CBM_RUNTIME_DIR it saw)."""
    record = tmp_path / "cbm-runtime.txt"
    suite = tiny_suite(tmp_path, record)
    child = child_env(HACF_HEAVY_LOCK="0", **env)
    child.pop("CBM_RUNTIME_DIR", None)
    if caller is not None:
        child["CBM_RUNTIME_DIR"] = caller
    proc = subprocess.run(pytest_argv(suite), env=child, capture_output=True, text=True,
                          timeout=120, check=False)  # fmt: skip
    return proc, (record.read_text() if record.exists() else None)


def test_c1_a_short_caller_runtime_dir_is_kept(tmp_path: Path, short_dir) -> None:
    """(c1) R65, the gate's step 10: a long XDG_RUNTIME_DIR and a short caller CBM_RUNTIME_DIR.

    The run keeps the caller's dir: it never creates, chmods or removes it, and makes nothing
    under XDG_RUNTIME_DIR. Before R65 the hook overwrote the value and exited 4.
    """
    xdg = long_dir(tmp_path, "xdg")
    caller = short_dir("c1")
    caller.chmod(0o750)
    (caller / "kept.txt").write_text("the caller's own file\n")
    before = snapshot(caller)
    proc, seen = runtime_run(tmp_path, str(caller), XDG_RUNTIME_DIR=str(xdg))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "3 passed" in proc.stdout
    assert seen == str(caller)
    assert list(xdg.iterdir()) == []
    assert snapshot(caller) == before


def test_c2_no_caller_value_uses_xdg_and_removes_it(tmp_path: Path, short_dir) -> None:
    """(c2) No caller value: the run's own `hacf-test-<pid>` under XDG_RUNTIME_DIR, gone after."""
    xdg = short_dir("c2")
    proc, seen = runtime_run(tmp_path, None, XDG_RUNTIME_DIR=str(xdg))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert seen is not None
    assert Path(seen).parent == xdg and Path(seen).name.startswith("hacf-test-")
    assert not Path(seen).exists()
    assert list(xdg.iterdir()) == []


@pytest.mark.parametrize("kind", ["over budget", "relative"])
def test_c3_an_unfit_caller_value_is_passed_over(tmp_path: Path, short_dir, kind: str) -> None:
    """(c3) A caller value over the budget, or a relative one: the run uses (ii).

    The caller's dir is left as it was.
    """
    xdg = short_dir("c3")
    caller_dir = long_dir(tmp_path, "caller")
    (caller_dir / "kept.txt").write_text("the caller's own file\n")
    before = snapshot(caller_dir)
    caller = str(caller_dir) if kind == "over budget" else "relative/cbm-runtime"
    proc, seen = runtime_run(tmp_path, caller, XDG_RUNTIME_DIR=str(xdg))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert seen is not None
    assert Path(seen).parent == xdg and Path(seen).name.startswith("hacf-test-")
    assert list(xdg.iterdir()) == []
    assert snapshot(caller_dir) == before


def test_c4_a_long_xdg_falls_to_the_temp_dir(tmp_path: Path, short_dir) -> None:
    """(c4) XDG_RUNTIME_DIR over the budget and no caller value: (iii) under a short TMPDIR."""
    xdg = long_dir(tmp_path, "xdg")
    tmp = short_dir("c4")
    proc, seen = runtime_run(tmp_path, None, XDG_RUNTIME_DIR=str(xdg), TMPDIR=str(tmp))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert seen is not None
    assert Path(seen).parent == tmp and Path(seen).name.startswith("hacf-test-")
    assert not Path(seen).exists()
    assert list(xdg.iterdir()) == []


def test_c5_no_candidate_fits_exits_4_naming_each(tmp_path: Path) -> None:
    """(c5) Every candidate over the budget: exit 4, and the message names each with its bytes."""
    caller = long_dir(tmp_path, "caller")
    xdg = long_dir(tmp_path, "xdg")
    tmp = long_dir(tmp_path, "tmp")
    proc, seen = runtime_run(tmp_path, str(caller), XDG_RUNTIME_DIR=str(xdg), TMPDIR=str(tmp))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 4, out
    assert seen is None
    assert f"{caller} ({len(os.fsencode(str(caller)))} bytes)" in out, out
    for base in (xdg, tmp):
        found = re.search(rf"({re.escape(str(base))}/hacf-test-\d+) \((\d+) bytes\)", out)
        assert found, out
        assert int(found[2]) == len(os.fsencode(found[1]))
    assert list(xdg.iterdir()) == [] and list(tmp.iterdir()) == []


# ------------------------------------------------------------------------------- forged tokens


def test_valid_and_forged_tokens(lock: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A token is valid only with the record's value, a live pid and an ancestor pid.

    HACF_HEAVY_LOCK_HOLDER_PID may name the holder instead of the ancestor walk; a forged token
    is ignored and the caller acquires.
    """
    token = "a" * 32
    other = subprocess.Popen(["sleep", "30"])
    try:
        cases = {
            "valid": (os.getpid(), token, {}),
            "wrong value": (os.getpid(), "b" * 32, {}),
            "dead pid": (dead_pid(), token, {}),
            "non-ancestor pid": (other.pid, token, {}),
            "named holder pid": (other.pid, token, {"HACF_HEAVY_LOCK_HOLDER_PID": str(other.pid)}),
            "wrong named pid": (other.pid, token, {"HACF_HEAVY_LOCK_HOLDER_PID": str(os.getpid())}),
        }
        verdicts = {}
        for name, (pid, record_token, extra) in cases.items():
            write_record(lock, pid, record_token)
            env = {"HACF_HEAVY_LOCK_HELD": token, **extra}
            verdicts[name] = hg.token_valid(env)
        assert verdicts == {
            "valid": True,
            "wrong value": False,
            "dead pid": False,
            "non-ancestor pid": False,
            "named holder pid": True,
            "wrong named pid": False,
        }
        write_record(lock, other.pid, token)  # a forged token: the caller acquires for real
        monkeypatch.setenv("HACF_HEAVY_LOCK_HELD", token)
        with hg.acquire("caller", 2) as lease:
            assert not lease.reentrant and lease.token != token
            assert json.loads(lock.with_suffix(".json").read_text())["pid"] == os.getpid()
        write_record(lock, os.getpid(), token)  # a valid one: re-entrant, nothing acquired
        with hg.acquire("nested", 2) as lease:
            assert lease.reentrant
    finally:
        other.kill()
        other.wait()
    assert log_events(lock) == [("acquire", "caller"), ("release", "caller")]


def test_a_token_for_another_lock_file_is_cleared_by_the_override(
    lock: Path, tmp_path: Path
) -> None:
    """HACF_HEAVY_LOCK_PATH: a token inherited from another lock never validates under it."""
    real = tmp_path / "real" / "heavy-job.lock"
    write_record(real, os.getpid(), "c" * 32)
    assert not hg.token_valid({"HACF_HEAVY_LOCK_HELD": "c" * 32})


# ------------------------------------------------------------------------------- crash, signals


def test_a_killed_holder_keeps_the_lock_until_its_child_exits(lock: Path, tmp_path: Path) -> None:
    """SIGKILL host_gate while its child sleeps: the child's inherited fd keeps the lock held."""
    pidfile = tmp_path / "child.pid"
    holder = gate("run", "--job", "crash", "--", "sh", "-c", f'echo $$ > "{pidfile}"; exec sleep 2')
    wait_for(lambda: pidfile.exists() and pidfile.read_text().strip())
    child = int(pidfile.read_text())
    holder.send_signal(signal.SIGKILL)
    holder.wait()
    assert locked_now(lock), "the lock was freed while the job still ran"
    status = subprocess.run([PY, str(TOOL), "status", "--json"], env=child_env(),
                            capture_output=True, text=True, check=True)  # fmt: skip
    data = json.loads(status.stdout)
    assert (data["holder_state"], data["holder"], data["locked"]) == ("stale", None, True)
    wait_for(lambda: not hg.pid_alive(child), timeout=10)
    wait_for(lambda: not locked_now(lock), timeout=5)


def test_sigterm_to_the_holder_kills_the_childs_group(lock: Path, tmp_path: Path) -> None:
    """SIGTERM host_gate: SIGTERM goes to the child's process group, then the lock is released."""
    pidfile = tmp_path / "child.pid"
    holder = gate("run", "--job", "term", "--", "sh", "-c",
                  f'echo $$ > "{pidfile}"; sleep 30 & sleep 30; wait')  # fmt: skip
    wait_for(lambda: pidfile.exists() and pidfile.read_text().strip())
    group = int(pidfile.read_text())
    assert os.getpgid(group) == group  # the child leads its own process group
    holder.send_signal(signal.SIGTERM)
    code, _, _ = finish(holder, 20)
    assert code == 128 + signal.SIGTERM

    def group_gone() -> bool:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return True
        return False

    wait_for(group_gone, timeout=5)
    assert not locked_now(lock)
    assert log_events(lock) == [("acquire", "term"), ("release", "term")]


# -------------------------------------------------------------------------------------- status


def test_status_reports_a_dead_holder_as_stale(lock: Path) -> None:
    """A record whose pid is dead is `stale` and never printed as the holder."""
    pid = dead_pid()
    write_record(lock, pid, "d" * 32)
    data = json.loads(
        subprocess.run(
            [PY, str(TOOL), "status", "--json"],
            env=child_env(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout  # fmt: skip
    )
    assert data["holder_state"] == "stale" and data["holder"] is None
    assert data["stale_record"]["pid"] == pid
    assert (
        data["lock"] == str(lock) and data["min_mb"] == 6144 and data["mem_available_mb"] == 16384
    )
    text = subprocess.run([PY, str(TOOL), "status"], env=child_env(), capture_output=True,
                          text=True, check=True).stdout  # fmt: skip
    assert f"holder: stale (pid {pid} is not running)" in text
    assert "forged" not in text
    with hg.acquire("next", 2):  # the next acquirer overwrites the stale record
        assert json.loads(lock.with_suffix(".json").read_text())["job"] == "next"


# ------------------------------------------------------------------- heavy processes, workers


def test_heavy_process_match_ignores_mere_mentions() -> None:
    """Only real pytest/check.sh processes count as a busy host, not an argv that mentions them."""
    heavy = [
        "101 /usr/bin/python3 -m pytest -q dev/tests",
        "102 " + "/" + "home/u/proj/.venv/bin/pytest -m not live -n 4",
        "103 bash dev/scripts/check.sh",
        "104 /usr/bin/bash /repo/dev/scripts/check.sh",
        "105 uv run --locked pytest -q",
    ]
    heavy += [
        "106 /usr/bin/python3 -mpytest -q dev/tests",
        "107 /usr/bin/python3.12 /repo/.venv/bin/pytest -q",
        "108 uv run --locked python -m pytest -q",
    ]
    idle = [
        '201 /x/bin/claude --settings {"permissions":{"allow":["Bash(uv run pytest*)"]}}',
        "202 vim dev/tests/test_pytest_helpers.py",
        "203 grep -E dev/scripts/check.sh|pytest",
        "204 grep pytest pyproject.toml",
        "205 vim /repo/.venv/bin/pytest",
        "206 less dev/scripts/check.sh",
        "207 /usr/bin/python3 -c print('pytest')",
    ]
    assert all(hg.is_heavy_process(line) for line in heavy)
    assert not any(hg.is_heavy_process(line) for line in idle)


def test_xdist_worker_and_lock_waiter_lines() -> None:
    """An xdist worker runs pytest's code (heavy); a run waiting in host_gate.py is not."""
    assert hg.is_heavy_process("301 /repo/.venv/bin/python -m pytest -n 4 -p xdist.looponfail")
    assert not hg.is_heavy_process(
        "302 python3 dev/scripts/host_gate.py run --job check.sh -- bash dev/scripts/check.sh"
    )


def test_ancestors_are_never_another_run() -> None:
    """The gate's own process and its parent shell are excluded from the busy check."""
    pids = hg.ancestor_pids()
    assert str(os.getpid()) in pids and str(os.getppid()) in pids


def test_workers_hint(lock: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-cooperating heavy process lowers the workers to 4 (or the setting); none: None.

    This process and its ancestors, the live holder and the holder's descendants never count.
    """
    stranger = "999999 /usr/bin/python3 -m pytest -q"
    assert hg.workers_hint([stranger]) == 4
    assert hg.workers_hint([]) is None
    assert hg.workers_hint([f"{os.getpid()} /usr/bin/python3 -m pytest -q"]) is None
    monkeypatch.setenv("HACF_TEST_WORKERS_SHARED", "3")
    assert hg.workers_hint([stranger]) == 3
    holder = subprocess.Popen(["sleep", "30"])
    try:
        write_record(lock, holder.pid, "e" * 32)
        assert hg.workers_hint([f"{holder.pid} pytest -q"]) is None
    finally:
        holder.kill()
        holder.wait()


# --------------------------------------------------------------------------------- the probe


def test_the_probe_script_runs_under_the_lock(lock: Path, tmp_path: Path) -> None:
    """openrig_probe_record.sh holds the lock (job openrig_probe) past its TMPDIR checks."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "npm").write_text("#!/bin/sh\nexit 1\n")
    (stubs / "npm").chmod(0o755)
    ondisk = tmp_path / "ondisk"
    ondisk.mkdir()
    (tmp_path / "home").mkdir()
    env = child_env(PATH=f"{stubs}:/usr/bin:/bin", TMPDIR=str(ondisk), HOME=str(tmp_path / "home"))
    proc = subprocess.run(["bash", str(PROBE_SCRIPT), "0.7.21"], env=env, capture_output=True,
                          text=True, timeout=60, check=False)  # fmt: skip
    assert proc.returncode == 2, proc.stderr
    assert "npm i @openrig/cli@0.7.21 failed" in proc.stderr
    assert log_events(lock) == [("acquire", "openrig_probe"), ("release", "openrig_probe")]
