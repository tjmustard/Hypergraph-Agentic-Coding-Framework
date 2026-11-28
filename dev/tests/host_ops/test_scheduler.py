"""Tests of the heavy-job scheduler (OPR.99.0.16.3): `job run`, `job status`, host_gate's client.

Every test runs on the isolated host of conftest.py (state dir, config, lock and fake `rig` under
tmp_path), reads a fake meminfo, polls fast and runs only `true`, `sleep` and short Python
children. `nvidia-smi` is always a fake on a private PATH; the host's is never called.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import REPO_ROOT, clean_env

from .conftest import TOOL

GATE = REPO_ROOT / "dev" / "scripts" / "host_gate.py"
PY = sys.executable
SKEW_LINE = "host state is schema v2; this hacf-host is v1: newer HACF owns it; waiting"


class Sched:
    """The isolated host plus the scheduler's seams: meminfo, a marker file and started jobs."""

    def __init__(self, host, monkeypatch: pytest.MonkeyPatch) -> None:
        """A fake meminfo with 16 GB available, fast polls and no inherited token."""
        self.host = host
        self.lock = Path(os.environ["HACF_HEAVY_LOCK_PATH"])
        self.queue = host.state / "queue"
        self.meminfo = host.base / "meminfo"
        self.marker = host.base / "ran.txt"
        self.procs: list[subprocess.Popen] = []
        self.monkeypatch = monkeypatch
        self.memory(16384)
        monkeypatch.setenv("HACF_HOST_MEMINFO", str(self.meminfo))
        monkeypatch.setenv("HACF_HEAVY_POLL_S", "0.05")
        monkeypatch.setenv("HACF_HEAVY_WAIT_S", "30")
        for name in ("HACF_HEAVY_LOCK_HELD", "HACF_HEAVY_LOCK_HOLDER_PID", "HACF_HOST_MIN_MB"):
            monkeypatch.delenv(name, raising=False)

    def memory(self, mb: int) -> None:
        """Set the fake MemAvailable, atomically so a poll never reads half a file."""
        tmp = self.meminfo.with_suffix(".tmp")
        tmp.write_text(f"MemTotal: 134217728 kB\nMemAvailable: {mb * 1024} kB\n")
        os.replace(tmp, self.meminfo)

    def start(self, argv: list[str], **env: str) -> subprocess.Popen:
        """Start a process with the test's environment; it is killed at teardown if still alive."""
        child = clean_env(**env)
        for name in [k for k in child if k.startswith("PYTEST_")]:
            child.pop(name)
        proc = subprocess.Popen(
            argv, env=child, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.procs.append(proc)
        return proc

    def job(self, name: str, *flags: str, command: list[str] | None = None, **env: str):
        """Start `job run --job <name> <flags> -- <command>`; the default records the name."""
        command = command or self.record(name)
        return self.start(
            [PY, str(TOOL), "job", "run", "--job", name, *flags, "--", *command], **env
        )

    def record(self, name: str) -> list[str]:
        """A command appending `name` to the marker file."""
        code = f"open({str(self.marker)!r}, 'a').write({name!r} + '\\n')"
        return [PY, "-c", code]

    def ran(self) -> list[str]:
        """The names recorded so far, in order."""
        return self.marker.read_text().split() if self.marker.exists() else []

    def blocker(self, name: str = "blocker", *flags: str) -> tuple[subprocess.Popen, Path]:
        """A job holding the lock until the returned file exists (at most 60 s)."""
        done = self.host.base / f"{name}.done"
        code = (
            "import pathlib, time\n"
            f"p = pathlib.Path({str(done)!r})\n"
            "end = time.monotonic() + 60\n"
            "while not p.exists() and time.monotonic() < end: time.sleep(0.02)\n"
        )
        proc = self.job(name, *flags, command=[PY, "-c", code])
        wait_for(lambda: self.holder().get("job") == name)
        return proc, done

    def holder(self) -> dict:
        """The holder record, or {}."""
        try:
            return json.loads(self.lock.with_suffix(".json").read_text())
        except (OSError, ValueError):
            return {}

    def tickets(self) -> dict[str, dict]:
        """Each job name → ticket content, with the file name under `file`."""
        found = {}
        for path in sorted(self.queue.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            found[data["job"]] = {**data, "file": path}
        return found

    def queued(self, *names: str) -> None:
        """Wait until each named job has a ticket whose first evaluation is written."""
        wait_for(lambda: all("blocked" in self.tickets().get(n, {}) for n in names))

    def backdate(self, name: str, minutes: float) -> None:
        """Move a ticket's enqueued_at into the past, keeping every other key."""
        ticket = self.tickets()[name]
        path = ticket.pop("file")
        then = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        ticket["enqueued_at"] = then.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        tmp = path.with_name(f".{path.name}.test.tmp")
        tmp.write_text(json.dumps(ticket))
        os.replace(tmp, path)

    def put_state(self, name: str, data: dict) -> Path:
        """Write one state file."""
        path = self.host.state / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return path

    def status(self) -> dict:
        """`job status --json`."""
        done = finish(self.start([PY, str(TOOL), "job", "status", "--json"]))
        assert done[0] == 0, done[2]
        return json.loads(done[1])


@pytest.fixture
def sched(host, monkeypatch: pytest.MonkeyPatch) -> Iterator[Sched]:
    """The scheduler seams; leftover processes are stopped (SIGTERM reaches a job's group)."""
    state = Sched(host, monkeypatch)
    yield state
    for proc in state.procs:
        if proc.poll() is None:
            proc.terminate()
        proc.communicate(timeout=30)


def finish(proc: subprocess.Popen, timeout: float = 30) -> tuple[int, str, str]:
    """Wait for a started process; its exit code, stdout and stderr."""
    out, err = proc.communicate(timeout=timeout)
    return proc.returncode, out, err


def wait_for(predicate: Callable[[], object], timeout: float = 20.0) -> None:
    """Poll `predicate` until it is truthy."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting")
        time.sleep(0.02)


def still_waiting(proc: subprocess.Popen, seconds: float = 0.6) -> None:
    """The process is still running after many polls."""
    time.sleep(seconds)
    assert proc.poll() is None, proc.communicate()


def dead_pid() -> int:
    """A reaped child's pid."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def tree(root: Path) -> dict[str, bytes]:
    """Every file under `root` with its bytes."""
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


PRIORITY = "priority: [hi, lo]\n"


# ------------------------------------------------------------------------------- order, tickets


def test_a_lower_tier_enqueued_first_is_served_after_a_higher_one(sched) -> None:
    """MR 2(a): the head is the minimum (tier, enqueued_at)."""
    sched.host.write_config(PRIORITY)
    holder, done = sched.blocker()
    low = sched.job("low", "--rig", "lo")
    sched.queued("low")
    high = sched.job("high", "--rig", "hi")
    sched.queued("high")
    assert (sched.tickets()["low"]["tier"], sched.tickets()["high"]["tier"]) == (1, 0)
    done.touch()
    assert [finish(p)[0] for p in (holder, low, high)] == [0, 0, 0]
    assert sched.ran() == ["high", "low"]


def test_fifo_within_a_tier(sched) -> None:
    """Equal tiers are served in enqueue order."""
    holder, done = sched.blocker()
    names = ["one", "two", "three"]
    jobs = []
    for name in names:
        jobs.append(sched.job(name, "--rig", "same"))
        sched.queued(name)
    done.touch()
    assert [finish(p)[0] for p in (holder, *jobs)] == [0] * 4
    assert sched.ran() == names


def test_a_ticket_holds_the_contract_fields_and_goes_with_the_job(sched) -> None:
    """§5.4: queue/<ticket>.json, and the holder record's rig, needs and ticket."""
    holder, done = sched.blocker("held", "--rig", "alpha", "--ram-mb", "300")
    record = sched.holder()
    [path] = sched.queue.glob("*.json")
    ticket = json.loads(path.read_text())
    assert ticket["schema"] == "hacf.host/v1"
    assert (ticket["job"], ticket["rig"], ticket["pid"]) == ("held", "alpha", holder.pid)
    assert ticket["needs"] == {"ram_mb": 300, "gpu": False}
    assert ticket["tier"] == 0 and ticket["token"] == record["token"]
    assert ticket["enqueued_at"].endswith("Z")
    assert record["schema"] == "hacf.heavy-job/v1"
    assert (record["rig"], record["ticket"]) == ("alpha", path.name)
    assert record["needs"] == {"ram_mb": 300, "gpu": False}
    done.touch()
    assert finish(holder)[0] == 0
    assert list(sched.queue.glob("*.json")) == [] and sched.holder() == {}
    lines = sched.lock.with_suffix(".log").read_text().splitlines()
    assert len(lines) == 2 and all(ln.endswith(" rig=alpha needs=300/0") for ln in lines)


def test_the_rig_comes_from_the_session_name(sched) -> None:
    """R184: no --rig takes the part after `@`; neither gives a rig-less ticket."""
    holder, done = sched.blocker()
    named = sched.job("named", OPENRIG_SESSION_NAME="build-execute@alpha")
    bare = sched.job("bare")
    sched.queued("named", "bare")
    assert sched.tickets()["named"]["rig"] == "alpha"
    assert sched.tickets()["bare"]["rig"] is None
    done.touch()
    assert [finish(p)[0] for p in (holder, named, bare)] == [0, 0, 0]


def test_a_dead_waiter_is_dropped(sched) -> None:
    """A ticket whose pid is dead is deleted by whoever sees it, and never blocks."""
    ghost = sched.put_state(
        "queue/ghost.json",
        {"schema": "hacf.host/v1", "job": "ghost", "rig": None, "pid": dead_pid(), "tier": 0,
         "needs": {"ram_mb": 0, "gpu": False}, "enqueued_at": "2020-01-01T00:00:00.000000Z"},
    )  # fmt: skip
    code, _, err = finish(sched.job("next", "--wait-s", "5"))
    assert code == 0, err
    assert not ghost.exists()


def test_job_status_drops_a_dead_waiter(sched) -> None:
    """`job status` sees it too."""
    ghost = sched.put_state(
        "queue/ghost.json",
        {"schema": "hacf.host/v1", "job": "ghost", "rig": None, "pid": dead_pid(), "tier": 0,
         "needs": {"ram_mb": 0, "gpu": False}, "enqueued_at": "2020-01-01T00:00:00.000000Z"},
    )  # fmt: skip
    assert sched.status()["queue"] == []
    assert not ghost.exists()


# ------------------------------------------------------------------------- backfill, reservation


def big_head(sched, *flags: str) -> subprocess.Popen:
    """A head needing 50 GB on a 10 GB host, in the top tier."""
    sched.host.write_config(PRIORITY + "reserve_after_minutes: 1\n")
    sched.memory(10240)
    big = sched.job("big", "--rig", "hi", "--ram-mb", "51200", *flags)
    sched.queued("big")
    return big


def test_backfill_before_the_reservation(sched) -> None:
    """The head does not fit, so a job that fits takes the lock."""
    big = big_head(sched)
    code, _, err = finish(sched.job("small", "--rig", "lo", "--wait-s", "10"))
    assert code == 0, err
    assert sched.ran() == ["small"]
    assert big.poll() is None


def test_the_head_goes_first_when_it_fits(sched) -> None:
    """A head whose needs fit is never backfilled past."""
    sched.host.write_config(PRIORITY)
    holder, done = sched.blocker()
    small = sched.job("small", "--rig", "lo")
    sched.queued("small")
    head = sched.job("head", "--rig", "hi", "--ram-mb", "2000", HACF_HEAVY_POLL_S="0.5")
    sched.queued("head")
    done.touch()
    assert [finish(p)[0] for p in (holder, small, head)] == [0, 0, 0]
    assert sched.ran() == ["head", "small"]


def test_no_backfill_after_the_reservation_then_the_head_runs_first(sched) -> None:
    """P48: the host is reserved for the head; when memory frees it runs before the small job."""
    big = big_head(sched)
    sched.backdate("big", 5)
    small = sched.job("small", "--rig", "lo", "--wait-s", "30")
    sched.queued("small")
    still_waiting(small)
    assert sched.ran() == []
    reserved = sched.status()["reserved"]
    assert reserved.startswith("reserved for: big (hi, tier 0) since ")
    text = finish(sched.start([PY, str(TOOL), "job", "status"]))[1]
    assert reserved in text.splitlines()
    sched.memory(65536)
    assert [finish(p)[0] for p in (big, small)] == [0, 0]
    assert sched.ran() == ["big", "small"]


def test_the_reservation_ends_when_the_head_gives_up(sched) -> None:
    """P48: a head that exits 75 releases the reservation."""
    big = big_head(sched, "--wait-s", "2")
    sched.backdate("big", 5)
    small = sched.job("small", "--rig", "lo", "--wait-s", "30")
    sched.queued("small")
    still_waiting(small)
    code, _, err = finish(big)
    assert code == 75
    assert "host busy: MemAvailable 10240 MB < 53248 MB; retry later (exit 75)" in err
    assert finish(small)[0] == 0
    assert sched.ran() == ["small"]


def test_status_shows_a_blocked_head_before_the_reservation(sched) -> None:
    """R194: `blocked head: <job> needs <N> MB`."""
    big_head(sched)
    data = sched.status()
    assert data["blocked_head"] == "blocked head: big needs 51200 MB" and data["reserved"] is None


# ------------------------------------------------------------------------------------ RAM, GPU


@pytest.mark.parametrize(
    ("available", "ram", "floor"), [(5000, 0, 6144), (9000, 8000, 10048), (5000, 1000, 6144)]
)
def test_the_floor_and_the_reserve(sched, available: int, ram: int, floor: int) -> None:
    """R189: MemAvailable >= max(6144, ram_mb + reserve_mb), with ram_mb 0 still 6144."""
    sched.memory(available)
    code, _, err = finish(sched.job("hungry", "--ram-mb", str(ram), "--wait-s", "0"))
    assert code == 75
    assert f"host busy: MemAvailable {available} MB < {floor} MB; retry later (exit 75)" in err
    assert sched.ran() == [] and list(sched.queue.glob("*.json")) == []


def test_unreadable_memory_is_not_checked(sched) -> None:
    """With no MemAvailable the lock alone serialises."""
    sched.meminfo.write_text("nothing here\n")
    assert finish(sched.job("any", "--ram-mb", "999999", "--wait-s", "5"))[0] == 0


def private_path(sched, *tools: str) -> Path:
    """A bin dir holding only links to `tools`, used as the whole PATH."""
    bin_dir = sched.host.base / "bin"
    bin_dir.mkdir()
    for tool_name in tools:
        found = shutil.which(tool_name)
        assert found, tool_name
        (bin_dir / tool_name).symlink_to(found)
    sched.monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def fake_smi(bin_dir: Path, apps: Path, rc: int = 0) -> None:
    """A fake nvidia-smi printing the file `apps` for the compute-apps query only."""
    script = bin_dir / "nvidia-smi"
    script.write_text(
        f"#!{PY}\nimport sys\n"
        "assert sys.argv[1:] == ['--query-compute-apps=pid', '--format=csv,noheader']\n"
        f"sys.stdout.write(open({str(apps)!r}).read())\nsys.exit({rc})\n"
    )
    script.chmod(0o755)


def test_a_gpu_job_waits_on_nvidia_smi(sched) -> None:
    """MR 2(c): a listed compute app keeps a --gpu job waiting; a CPU job is not held."""
    bin_dir = private_path(sched, "ps")
    apps = sched.host.base / "apps"
    apps.write_text("424242\n")
    fake_smi(bin_dir, apps)
    gpu = sched.job("gpu", "--gpu")
    sched.queued("gpu")
    still_waiting(gpu)
    assert finish(sched.job("cpu", "--wait-s", "10"))[0] == 0
    apps.write_text("")
    assert finish(gpu)[0] == 0
    assert sched.ran() == ["cpu", "gpu"]


@pytest.mark.parametrize(("rc", "reason"), [(0, "gpu busy: 424242"), (9, "gpu busy: nvidia-smi")])
def test_a_busy_or_broken_gpu_exits_75(sched, rc: int, reason: str) -> None:
    """The reason names the pid; a failing nvidia-smi is never a free GPU."""
    bin_dir = private_path(sched, "ps")
    apps = sched.host.base / "apps"
    apps.write_text("424242\n")
    fake_smi(bin_dir, apps, rc)
    code, _, err = finish(sched.job("gpu", "--gpu", "--wait-s", "0"))
    assert code == 75 and f"host busy: {reason}" in err, err


def test_a_gpu_job_waits_on_a_foreign_pattern(sched) -> None:
    """D3: a process matching foreign_heavy_patterns blocks a --gpu job, and status lists it."""
    bin_dir = private_path(sched, "ps")
    apps = sched.host.base / "apps"
    apps.write_text("")  # nvidia-smi lists nothing: the pattern alone holds the job
    fake_smi(bin_dir, apps)
    marker = f"hacf-foreign-{os.getpid()}-{time.monotonic_ns()}"
    sched.host.write_config(f"foreign_heavy_patterns: ['{marker}']\n")
    foreign = sched.start([PY, "-c", "import time; time.sleep(30)", marker])
    assert finish(sched.job("cpu", "--wait-s", "10"))[0] == 0
    gpu = sched.job("gpu", "--gpu")
    sched.queued("gpu")
    still_waiting(gpu)
    assert sched.tickets()["gpu"]["blocked"]["reason"].startswith(f"gpu busy: {foreign.pid} ")
    listed = sched.status()["foreign_heavy"]
    assert [ln.split()[0] for ln in listed] == [str(foreign.pid)]
    foreign.kill()
    foreign.wait()
    assert finish(gpu)[0] == 0


def test_a_gpu_job_whose_own_argv_matches_the_pattern_runs(sched) -> None:
    """Waiting HACF jobs are never foreign, or two --gpu waiters would block each other."""
    bin_dir = private_path(sched, "ps")
    apps = sched.host.base / "apps"
    apps.write_text("")
    fake_smi(bin_dir, apps)
    marker = f"hacf-own-{os.getpid()}-{time.monotonic_ns()}"
    sched.host.write_config(f"foreign_heavy_patterns: ['{marker}']\n")
    jobs = [
        sched.job(n, "--gpu", "--wait-s", "20", command=[*sched.record(n), marker])
        for n in ("first", "second")
    ]
    assert [finish(p)[0] for p in jobs] == [0, 0]


def test_a_foreign_pattern_holds_a_gpu_job_without_nvidia_smi(sched) -> None:
    """MR 2(c), P70: with no nvidia-smi, a matching foreign process still holds a --gpu job."""
    private_path(sched, "ps")
    marker = f"hacf-nosmi-{os.getpid()}-{time.monotonic_ns()}"
    sched.host.write_config(f"foreign_heavy_patterns: ['{marker}']\n")
    foreign = sched.start([PY, "-c", "import time; time.sleep(30)", marker])
    assert finish(sched.job("cpu", "--wait-s", "10"))[0] == 0
    gpu = sched.job("gpu", "--gpu")
    sched.queued("gpu")
    still_waiting(gpu)
    assert sched.tickets()["gpu"]["blocked"]["reason"].startswith(f"gpu busy: {foreign.pid} ")
    foreign.kill()
    foreign.wait()
    assert finish(gpu)[0] == 0
    assert sched.ran() == ["cpu", "gpu"]


# --------------------------------------------------------------------------------------- drain


def drain(sched, minutes: float = 30) -> None:
    """Put an idle point in force (or, with a negative time, an expired one)."""
    now = datetime.now(timezone.utc)
    sched.put_state(
        "idle-point.json",
        {"schema": "hacf.host/v1", "purpose": "upgrade", "requested_by": "test",
         "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "expires_at": (now + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")},
    )  # fmt: skip


def rigs(sched, **states: str) -> None:
    """Map each rig to a project and answer its boundary reads: `busy` or `idle`."""
    lines = ["projects:"]
    for name, state in states.items():
        root = sched.host.project(name)
        lines.append(f"  {name}: {root}")
        node = {
            "canonicalSessionName": f"build-execute@{name}",
            "agentActivity": {"state": "idle" if state == "idle" else "working"},
            "needsAttention": False,
            "arbitratedNeedsInput": False,
            "lifecycleState": "running",
        }
        sched.host.rig.reply(f"ps --nodes --rig {name} --full --json", [node])
    sched.host.write_config("\n".join(lines) + "\n")
    sched.host.rig.reply("queue list -A --state in-progress --json --limit 10000", [])


def test_a_drain_admits_a_rig_in_flight(sched) -> None:
    """R187: a slice in flight finishes. The job's own ticket does not put its rig in flight."""
    rigs(sched, busy="busy", idle="idle")
    drain(sched)
    code, _, err = finish(sched.job("work", "--rig", "busy", "--wait-s", "10"))
    assert code == 0, err


@pytest.mark.parametrize("rig_name", ["idle", "unmapped", None])
def test_a_drain_refuses_a_rig_that_is_not_in_flight(sched, rig_name: str | None) -> None:
    """At a boundary, unmapped and rig-less tickets wait and exit 75 naming the idle point."""
    rigs(sched, busy="busy", idle="idle")
    drain(sched)
    flags = ["--rig", rig_name] if rig_name else []
    code, _, err = finish(sched.job("late", *flags, "--wait-s", "0.5"))
    assert code == 75
    assert "host busy: idle point: upgrade; retry later (exit 75)" in err


def test_a_refused_head_does_not_block_a_rig_in_flight(sched) -> None:
    """R187, no deadlock: the drain's refused tickets are not in the admission order."""
    rigs(sched, busy="busy", idle="idle")
    drain(sched)
    waiting = sched.job("late", "--rig", "idle")
    sched.queued("late")
    code, _, err = finish(sched.job("work", "--rig", "busy", "--wait-s", "10"))
    assert code == 0, err
    assert waiting.poll() is None


def test_an_expired_idle_point_is_not_in_force(sched) -> None:
    """Past expires_at, tickets run as usual."""
    drain(sched, -1)
    assert finish(sched.job("after", "--wait-s", "5"))[0] == 0


def test_an_unreadable_idle_point_is_in_force(sched) -> None:
    """Unknown never admits a ticket that a drain would hold."""
    sched.host.state.mkdir(parents=True)
    (sched.host.state / "idle-point.json").write_text("{broken")
    code, _, err = finish(sched.job("late", "--wait-s", "0"))
    assert code == 75 and "host busy: idle point: " in err, err


# ------------------------------------------------------------------------------ exit 75 reasons


def test_exit_75_queued_behind_the_head(sched) -> None:
    """The head fits but is not polling yet, so the later job names it."""
    sched.host.write_config(PRIORITY)
    holder, done = sched.blocker()
    sched.job("first", "--rig", "hi")
    sched.queued("first")
    code, _, err = finish(sched.job("later", "--rig", "lo", "--wait-s", "0.3"))
    assert code == 75
    assert "host busy: queued behind first (hi, tier 0); retry later (exit 75)" in err
    done.touch()
    assert finish(holder)[0] == 0


def test_exit_75_names_the_lock_holder(sched) -> None:
    """The head blocked only by the lock names the holder."""
    holder, done = sched.blocker()
    code, _, err = finish(sched.job("wants", "--wait-s", "0.3"))
    assert code == 75
    assert f"host busy: lock held by blocker (pid {holder.pid}, " in err, err
    assert list(sched.tickets()) == ["blocker"]
    done.touch()
    assert finish(holder)[0] == 0


# ------------------------------------------------------------- running, re-entrancy, old clients


def test_the_exit_code_and_a_signal_are_passed_on(sched) -> None:
    """As host_gate run(): the child's code, and 128 + signal when stopped."""
    assert finish(sched.job("fails", command=["sh", "-c", "exit 7"]))[0] == 7
    started = sched.host.base / "stopped.started"
    code = f"import pathlib, time; pathlib.Path({str(started)!r}).touch(); time.sleep(60)"
    holder = sched.job("stopped", command=[PY, "-c", code])
    wait_for(started.exists)  # the command runs, so the signal is the one that is forwarded
    holder.send_signal(signal.SIGTERM)
    assert finish(holder)[0] == 128 + signal.SIGTERM
    assert list(sched.queue.glob("*.json")) == [] and sched.holder() == {}


# A `take` that sends this process a stop signal at one chosen point, then run_job on a command
# that would write `marker`. The tool is compiled from its path, so no bytecode lands beside it.
HOOKED = """\
import os, signal, sys, types

tool = types.ModuleType("hacf_host")
exec(compile(open({tool!r}, encoding="utf-8").read(), {tool!r}, "exec"), tool.__dict__)
polls = []


def mem():
    polls.append(1)
    if {where!r} == "polling" and len(polls) == 2:  # the ticket is written by now
        os.kill(os.getpid(), signal.{name})
    return 100 if {where!r} == "polling" else 32768


def take():
    if {where!r} == "before-acquire":
        os.kill(os.getpid(), signal.{name})
    lease = tool.acquire("hooked", mem=mem)
    if {where!r} == "after-take":
        os.kill(os.getpid(), signal.{name})
    return lease


command = [sys.executable, "-c", "open({marker!r}, 'w').close()"]
sys.exit(tool.run_job(command, take))
"""


def hooked(sched, where: str, name: str = "SIGTERM") -> tuple[int, str, str]:
    """Run `run_job` with a signal sent from inside the window `where`."""
    marker = str(sched.marker)
    code = HOOKED.format(tool=str(TOOL), where=where, name=name, marker=marker)
    return finish(sched.start([PY, "-c", code]))


@pytest.mark.parametrize("name", ["SIGTERM", "SIGINT", "SIGHUP"])
@pytest.mark.parametrize("where", ["before-acquire", "polling"])
def test_a_signal_during_the_wait_starts_nothing(sched, where: str, name: str) -> None:
    """MR 9 (i): no child, no ticket, no holder, 128 + signal."""
    code, _, err = hooked(sched, where, name)
    assert code == 128 + getattr(signal, name), err
    assert not sched.marker.exists()
    assert list(sched.queue.glob("*.json")) == [] and sched.holder() == {}


@pytest.mark.parametrize("name", ["SIGTERM", "SIGINT", "SIGHUP"])
def test_a_signal_between_take_and_the_command_starts_nothing(sched, name: str) -> None:
    """MR 9 (ii): the lease is released, no child starts, and the lock is free at once."""
    code, _, err = hooked(sched, "after-take", name)
    assert code == 128 + getattr(signal, name), err
    assert not sched.marker.exists()
    assert list(sched.queue.glob("*.json")) == [] and sched.holder() == {}
    events = sched.lock.with_suffix(".log").read_text().split()
    assert "acquire" in events and "release" in events
    after = finish(sched.job("next", "--wait-s", "0"))
    assert after[0] == 0, after[2]
    assert sched.ran() == ["next"]


def test_a_signal_to_a_running_job_reaches_the_commands_group_once(sched) -> None:
    """MR 9 (iii): after the command starts, one SIGTERM is forwarded; a second is not."""
    seen = sched.host.base / "seen.txt"
    ready = sched.host.base / "ready"
    code = (
        "import pathlib, signal, sys, time\n"
        "def got(n, f):\n"
        f"    open({str(seen)!r}, 'a').write(signal.Signals(n).name + '\\n')\n"
        "signal.signal(signal.SIGTERM, got)\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "end = time.monotonic() + 60\n"
        f"seen = pathlib.Path({str(seen)!r})\n"
        "while not seen.exists() and time.monotonic() < end: time.sleep(0.02)\n"
        "time.sleep(0.5)\n"
    )
    job = sched.job("forwarded", command=[PY, "-c", code])
    wait_for(ready.exists)
    job.send_signal(signal.SIGTERM)
    wait_for(seen.exists)
    job.send_signal(signal.SIGINT)
    assert finish(job)[0] == 0  # the command ended by itself within the grace
    assert seen.read_text().split() == ["SIGTERM"]
    assert list(sched.queue.glob("*.json")) == [] and sched.holder() == {}


def test_the_command_keeps_a_signal_its_parent_ignored(sched) -> None:
    """A job started under nohup keeps SIGHUP ignored in its command."""
    code = "import signal; print(signal.getsignal(signal.SIGHUP) == signal.SIG_IGN)"
    argv = [PY, str(TOOL), "job", "run", "--job", "nohup", "--", PY, "-c", code]
    wrapper = (
        "import os, signal, sys\n"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN)\n"
        f"os.execv({PY!r}, {argv!r})\n"
    )
    done = finish(sched.start([PY, "-c", wrapper]))
    assert done[0] == 0, done[2]
    assert done[1].strip() == "True"


def test_a_nested_job_runs_in_place_without_a_ticket(sched) -> None:
    """MR 4: a valid token execs in place, even for needs the host could not admit."""
    count = sched.host.base / "count"
    inner = [
        PY, str(TOOL), "job", "run", "--job", "inner", "--ram-mb", "999999", "--wait-s", "1", "--",
        PY, "-c", f"import os; open({str(count)!r}, 'w').write(str(len(os.listdir("
        f"{str(sched.queue)!r}))))",
    ]  # fmt: skip
    code, _, err = finish(sched.job("outer", "--wait-s", "5", command=inner))
    assert code == 0, err
    assert count.read_text() == "1"
    events = sched.lock.with_suffix(".log").read_text().splitlines()
    assert [ln.split()[3] for ln in events] == ["job=outer", "job=outer"]


def test_an_old_client_holding_the_lock_excludes_a_new_ticket(sched) -> None:
    """R190: a raw flock with no ticket holds; the new job starts only after it is released."""
    stamps = sched.host.base / "old.json"
    go = sched.host.base / "old.go"
    old = sched.start(
        [PY, "-c",
         "import fcntl, json, os, pathlib, time\n"
         f"lock = pathlib.Path({str(sched.lock)!r})\n"
         "lock.parent.mkdir(parents=True, exist_ok=True)\n"
         "fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)\n"
         "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
         "start = time.time()\n"
         f"pathlib.Path({str(go)!r}).touch()\n"
         "time.sleep(1.5)\n"
         "end = time.time()\n"
         f"pathlib.Path({str(stamps)!r}).write_text(json.dumps([start, end]))\n"
         "fcntl.flock(fd, fcntl.LOCK_UN)\n"]
    )  # fmt: skip
    wait_for(go.exists)
    began = sched.host.base / "new.start"
    new = sched.job(
        "new", command=[PY, "-c", f"import time; open({str(began)!r},'w').write(str(time.time()))"]
    )
    sched.queued("new")
    assert finish(old)[0] == 0
    code, _, err = finish(new)
    assert code == 0, err
    start, end = json.loads(stamps.read_text())
    started = float(began.read_text())
    print(f"old client held {start:.3f} .. {end:.3f}; new job started {started:.3f}")
    assert started > end > start
    assert "lock is held" in err


def test_a_new_holder_excludes_an_old_client(sched) -> None:
    """The other direction: the old LOCK_NB take fails while a ticketed job holds."""
    holder, done = sched.blocker()
    with open(sched.lock) as handle, pytest.raises(BlockingIOError):
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    done.touch()
    assert finish(holder)[0] == 0


# --------------------------------------------------------------------------------- schema skew


@pytest.mark.parametrize("name", ["status.json", "queue/other.json", "rigs/a.json"])
def test_job_run_against_a_newer_state_writes_nothing_and_exits_75(sched, name: str) -> None:
    """R193: the command never runs, and neither state nor lock changes."""
    sched.put_state(name, {"schema": "hacf.host/v2", "job": "n", "pid": os.getpid()})
    before = tree(sched.host.state)
    code, _, err = finish(sched.job("blocked", "--wait-s", "5"))
    assert code == 75 and SKEW_LINE in err, err
    assert sched.ran() == [] and tree(sched.host.state) == before
    assert not sched.lock.exists() and not sched.lock.with_suffix(".log").exists()


def test_a_state_turning_newer_while_waiting_ends_the_wait_with_75(sched) -> None:
    """A waiter writes nothing more and never takes the lock."""
    holder, done = sched.blocker()
    waiter = sched.job("waiter")
    sched.queued("waiter")
    sched.put_state("status.json", {"schema": "hacf.host/v2"})
    code, _, err = finish(waiter)
    assert code == 75 and SKEW_LINE in err, err
    done.touch()
    assert finish(holder)[0] == 0
    assert sched.ran() == []


def test_job_status_against_a_newer_state_prints_and_writes_nothing(sched) -> None:
    """A dead ticket stays: nothing is deleted under a newer schema."""
    sched.put_state("status.json", {"schema": "hacf.host/v2"})
    sched.put_state(
        "queue/ghost.json",
        {"schema": "hacf.host/v1", "job": "ghost", "pid": dead_pid(), "tier": 0,
         "needs": {"ram_mb": 0, "gpu": False}, "enqueued_at": "2020-01-01T00:00:00.000000Z"},
    )  # fmt: skip
    before = tree(sched.host.state)
    assert sched.status()["queue"] == []
    assert tree(sched.host.state) == before


# ------------------------------------------------------------------------------------ job status


def test_job_status_shows_the_holder_the_queue_and_the_host(sched) -> None:
    """MR 5, and no token is printed."""
    sched.host.write_config(PRIORITY)
    holder, done = sched.blocker("held", "--rig", "hi", "--ram-mb", "100")
    waiting = [sched.job("b", "--rig", "lo"), sched.job("a", "--rig", "hi")]
    sched.queued("a", "b")
    drain(sched, -1)
    data = sched.status()
    assert data["schema"] == "hacf.host-job-status/v1"
    assert data["holder"]["job"] == "held" and data["holder"]["rig"] == "hi"
    assert data["holder"]["pid"] == holder.pid and data["holder"]["since"].endswith("Z")
    assert data["holder"]["needs"] == {"ram_mb": 100, "gpu": False}
    assert [(t["job"], t["tier"]) for t in data["queue"]] == [("a", 0), ("b", 1)]
    assert all(t["waiting_s"] >= 0 for t in data["queue"])
    assert (data["mem_available_mb"], data["min_mb"]) == (16384, 6144)
    assert data["idle_point"] is None and isinstance(data["non_cooperating"], list)
    assert "token" not in json.dumps(data)
    text = finish(sched.start([PY, str(TOOL), "job", "status"]))[1]
    for part in ("holder: held (hi, pid ", "queue: 2", "MemAvailable: 16384 MB (floor 6144 MB)",
                 "idle point: none", "foreign heavy processes: 0",
                 "non-cooperating heavy processes: "):  # fmt: skip
        assert part in text, text
    done.touch()
    assert [finish(p)[0] for p in (holder, *waiting)] == [0, 0, 0]


def test_job_status_on_an_idle_host_writes_nothing(sched) -> None:
    """No holder, no queue, no state dir created."""
    data = sched.status()
    assert data["holder"] is None and data["queue"] == []
    assert not sched.host.state.exists()


def test_job_status_shows_the_idle_point(sched) -> None:
    """The purpose of the drain in force."""
    drain(sched)
    assert sched.status()["idle_point"]["purpose"] == "upgrade"


@pytest.mark.parametrize(
    "argv", [["--job", "x"], ["--", "true"], ["--job", "x", "--ram-mb", "-1", "--", "true"]]
)
def test_job_run_usage(sched, argv: list[str]) -> None:
    """A missing job name or command, or a negative need, is exit 2."""
    code, _, err = finish(sched.start([PY, str(TOOL), "job", "run", *argv]))
    assert code == 2, err
    assert "not in this build" not in err and not sched.host.state.exists()


# ------------------------------------------------------------------------------ host_gate client


def gate(sched, *args: str, **env: str) -> subprocess.Popen:
    """Start `host_gate.py <args>`."""
    return sched.start([PY, str(GATE), *args], **env)


def test_host_gate_takes_a_ticket_with_the_sessions_rig(sched) -> None:
    """MR 6: ram 0, no GPU, the rig of OPENRIG_SESSION_NAME; status() adds the queue."""
    holder, done = sched.blocker()
    client = gate(
        sched, "run", "--job", "client", "--", *sched.record("client"),
        OPENRIG_SESSION_NAME="build-audit@alpha",
    )  # fmt: skip
    sched.queued("client")
    ticket = sched.tickets()["client"]
    assert (ticket["rig"], ticket["needs"]) == ("alpha", {"ram_mb": 0, "gpu": False})
    code, out, _ = finish(gate(sched, "status", "--json"))
    data = json.loads(out)
    assert code == 0 and data["schema"] == "hacf.heavy-job-status/v1"
    assert [t["job"] for t in data["queue"]] == ["client"]
    assert data["holder"]["job"] == "blocker"
    done.touch()
    assert [finish(p)[0] for p in (holder, client)] == [0, 0]


def test_host_gate_waits_its_turn_behind_a_higher_tier(sched) -> None:
    """The client is ordered like any ticket."""
    sched.host.write_config(PRIORITY)
    holder, done = sched.blocker()
    low = gate(
        sched, "run", "--job", "low", "--", *sched.record("low"),
        OPENRIG_SESSION_NAME="a@lo",
    )  # fmt: skip
    sched.queued("low")
    high = sched.job("high", "--rig", "hi")
    sched.queued("high")
    done.touch()
    assert [finish(p)[0] for p in (holder, low, high)] == [0, 0, 0]
    assert sched.ran() == ["high", "low"]


def test_host_gate_against_a_newer_state_exits_75(sched) -> None:
    """The client mutates the state too, so it waits for the newer HACF."""
    sched.put_state("status.json", {"schema": "hacf.host/v2"})
    before = tree(sched.host.state)
    code, _, err = finish(gate(sched, "run", "--job", "old", "--", *sched.record("old")))
    assert code == 75 and f"host busy: {SKEW_LINE}; retry later (exit 75)" in err, err
    assert sched.ran() == [] and tree(sched.host.state) == before


def test_host_gate_loads_the_shipped_script_and_writes_no_bytecode(sched) -> None:
    """MR 6: the logic is hacf-host.py's, and loading it leaves no __pycache__ in the kit."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import host_gate as h; "
        "print(h.HostBusy.__module__, h.acquire.__module__, sys.modules[h.HostBusy.__module__]"
        ".__file__)"
    )
    env = {k: v for k, v in clean_env().items() if k != "PYTHONDONTWRITEBYTECODE"}
    done = subprocess.run(
        [PY, "-c", code, str(GATE.parent)], env=env, capture_output=True, text=True, check=True
    )
    module, gate_module, path = done.stdout.split()
    assert (module, gate_module) == ("hacf_host", "host_gate")
    assert Path(path) == TOOL
    assert not list(TOOL.parent.glob("__pycache__/hacf-host*"))
