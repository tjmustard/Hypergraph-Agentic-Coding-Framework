"""Tests for hyper_engine.py and hyper_failures.py (S09a1, the engine core).

Covered: DAG order, overlap, gate stops, resume, the failure taxonomy, the rate-limit pause, the
run budget, preflight, config tightening and run.json.

Every test is deterministic and runs under both interpreters. Repositories are throwaway git repos
under tmp_path. Slices run through a fake SliceRunner (never an agent); the DAG and the validators
can be canned through the injectable subprocess probe; the executor, `which`, the platform and the
clock are fakes, so nothing needs claude, bwrap or a network, and nothing really sleeps.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import threading
import time
from pathlib import Path

import _runtime
import hyper_engine as he
import hyper_executors as hx
import hyper_failures as hf
import pytest
import yaml
from conftest import REPO_ROOT, SCRIPTS_DIR, GitRepo

F = hf.FailureType
CONFIG = REPO_ROOT / ".agents" / "config" / "orchestration.yml"
SECURITY_KEYS = [
    "executors.*.sandbox",
    "executors.*.verified",
    "executors.*.argv",
    "executors.*.preflight",
    "executors.*.prompts",
    "oracle.sandbox",
    "oracle.allowed_executables",
    "autonomy.env_passthrough",
    "autonomy.env_passthrough_prefixes",
    "autonomy.auth_passthrough",
    "autonomy.max_budget_usd_per_phase",
    "autonomy.max_budget_usd_per_run",
    "autonomy.scope_policy",
    "autonomy.credential_scan",
    "protected_paths",
    "git.ref_guard",
]
DIAMOND = (
    ("A", [], ["src/a/**"], "a"),
    ("B", ["A"], ["src/b/**"], "b"),
    ("C", ["A"], ["src/c/**"], "c"),
    ("D", ["B", "C"], ["src/d/**"], "d"),
)


@pytest.fixture(autouse=True)
def _no_bytecode(monkeypatch: pytest.MonkeyPatch) -> None:
    """The validator subprocesses import repository scripts; keep them from writing bytecode."""
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")


@pytest.fixture
def repo(tmp_git_repo: GitRepo, run_script) -> GitRepo:
    """Return a temp repository whose HEAD holds an empty hacf.ledger/v2 ledger."""
    proc = run_script(
        ".agents/scripts/hyper_ledger.py", "init", cwd=tmp_git_repo.path, env=tmp_git_repo.env
    )
    assert proc.returncode == 0, proc.stderr
    tmp_git_repo.commit("ledger")
    return tmp_git_repo


class FakeClock:
    """A monotonic clock that moves only when sleep() is called; on_sleep(n) runs after each."""

    def __init__(self, on_sleep=None) -> None:
        """Start at 0."""
        self.now = 0.0
        self.sleeps: list[float] = []
        self.on_sleep = on_sleep

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Record the sleep and advance the clock."""
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


class FakeExecutor:
    """An executor whose preflight passes without running claude."""

    def preflight(self) -> dict:
        """Return a fixed version."""
        return {"version": "2.1.300", "verified": False, "warnings": []}


def make_probes(*, clock=None, which=None, platform="linux", canned=None) -> he.Probes:
    """Return probes with a fake executor; canned maps a script name to (rc, stdout, stderr).

    Every other argv (git, the validators not canned) really runs.
    """
    canned = canned or {}

    def run(argv, *, cwd, env=None):
        script = Path(argv[1]).name if len(argv) > 1 else ""
        if script in canned:
            return subprocess.CompletedProcess(argv, *canned[script])
        return he._run_argv(argv, cwd=cwd, env=env)

    clock = clock or FakeClock()
    return he.Probes(
        which=which or (lambda tool: f"/usr/bin/{tool}"),
        platform=platform,
        run=run,
        clock=clock,
        sleep=clock.sleep,
        executor=lambda name, config, allow_unsandboxed=False: FakeExecutor(),
    )


def mission(repo: GitRepo, *slices) -> dict:
    """Commit a v6 mission of (id, depends_on, touches, node_id) slices; return the canned DAG.

    The validators are canned too: these stub SPEC.md files are not lint-clean slices.
    """
    repo.write("missions/m/SPEC.md", "# m\n")
    records = []
    for number, (sid, deps, touches, node) in enumerate(slices, 1):
        rel = f"missions/m/slices/{number:02d}-{sid.lower()}"
        front = {"id": sid, "hacf_status": "compiled", "hacf_touches": touches, "node_id": node}
        repo.write(f"{rel}/SPEC.md", f"---\n{yaml.safe_dump(front)}---\n# {sid}\n")
        records.append({"id": sid, "path": rel, "depends_on": deps})
    repo.commit("mission")
    report = {"order": [r["id"] for r in records], "slices": records, "cycle": None, "findings": []}
    return {
        "hyper_scope.py": (0, json.dumps(report), ""),
        "hyper_spec.py": (0, "lint: ok\n", ""),
        "hyper_ledger.py": (0, "validate: ok\n", ""),
    }


class FakeRunner:
    """A SliceRunner that logs each attempt and plays scripted outcomes (default: success)."""

    def __init__(self, outcomes=None, hooks=None, clock=None) -> None:
        """outcomes: slice id -> SliceOutcome list, one per attempt; hooks run inside run()."""
        self.outcomes = {sid: list(queue) for sid, queue in (outcomes or {}).items()}
        self.hooks = hooks or {}
        self.clock = clock
        self.log: list[tuple[str, str, float | None]] = []  # (start|end, slice id, fake time)
        self.together: list[set[str]] = []  # the running slices at each start
        self.active: set[str] = set()
        self.lock = threading.Lock()

    def run(self, item: he.Slice, ctx: dict) -> he.SliceOutcome:
        """Record the attempt, run the slice's hook, return its next scripted outcome."""
        with self.lock:
            self.active.add(item.id)
            self.together.append(set(self.active))
            self.log.append(("start", item.id, self.clock() if self.clock else None))
        try:
            if item.id in self.hooks:
                self.hooks[item.id](ctx)
            queue = self.outcomes.get(item.id)
            return queue.pop(0) if queue else he.SliceOutcome()
        finally:
            with self.lock:
                self.active.discard(item.id)
                self.log.append(("end", item.id, None))

    def calls(self) -> list[str]:
        """Return the slice id of every attempt, in start order."""
        return [sid for kind, sid, _ in self.log if kind == "start"]

    def index(self, kind: str, sid: str) -> int:
        """Return the log position of a slice's first start or end."""
        return next(i for i, entry in enumerate(self.log) if entry[:2] == (kind, sid))


def meet(barrier: threading.Barrier):
    """Return a hook that waits until every party of barrier runs at once."""
    return lambda ctx: barrier.wait(timeout=10)


def meet_then_wait_for(barrier: threading.Barrier, sid: str):
    """Return a hook that meets barrier, then returns only once sid is settled as audited.

    The scheduler logs `audited` when it applies a slice's outcome, so the slice that runs
    this hook always finishes after sid's cost counts.
    """

    def hook(ctx: dict) -> None:
        barrier.wait(timeout=10)
        path, deadline = ctx["event"].path, time.monotonic() + 10
        while not any(
            (e["slice"], e["event"]) == (sid, "audited")
            for e in map(json.loads, path.read_text(encoding="utf-8").splitlines())
        ):
            assert time.monotonic() < deadline, f"{sid} was never settled"
            time.sleep(0.01)

    return hook


def run(repo: GitRepo, runner, probes, resume=None, **flags) -> int:
    """Run the engine on repo; executors.claude-cli.verified is false, so allow it by default."""
    flags.setdefault("unverified_executor", True)
    return he.run_engine(repo.path, flags, runner, probes=probes, resume=resume)


def run_path(repo: GitRepo) -> Path:
    """Return the only run directory."""
    (directory,) = (repo.path / ".agents" / "state" / "runs").iterdir()
    return directory


def state(repo: GitRepo) -> dict:
    """Return the only run's run.json."""
    return json.loads((run_path(repo) / "run.json").read_text(encoding="utf-8"))


def events(repo: GitRepo) -> list[dict]:
    """Return the only run's events; every stored timestamp parses."""
    lines = (run_path(repo) / "events.jsonl").read_text(encoding="utf-8").splitlines()
    found = [json.loads(line) for line in lines]
    for event in found:
        _runtime.parse_ts(event["at"])
    return found


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def test_dag_diamond_order(repo: GitRepo, frozen_clock) -> None:
    """A, then B and C together, then D once both are audited and integrated."""
    canned = mission(repo, *DIAMOND)
    both = threading.Barrier(2)  # B and C must run at the same time (max_parallel 3)
    runner = FakeRunner(hooks={"B": meet(both), "C": meet(both)})
    assert run(repo, runner, make_probes(canned=canned)) == 0
    calls = runner.calls()
    assert calls[0] == "A" and set(calls[1:3]) == {"B", "C"} and calls[3] == "D"
    end_a = runner.index("end", "A")
    assert runner.index("start", "B") > end_a and runner.index("start", "C") > end_a
    assert runner.index("start", "D") > max(runner.index("end", "B"), runner.index("end", "C"))
    data = state(repo)
    assert re.match(r"^20261021T120000Z-[0-9a-f]{6}$", data["run_id"])
    assert data["created_at"] == "2026-10-21T12:00:00+00:00"
    statuses = {sid: record["status"] for sid, record in data["slices"].items()}
    assert statuses == dict.fromkeys("ABCD", "audited")
    assert data["status"] == "finished" and data["failure_type"] is None


def test_overlap_serialized(repo: GitRepo) -> None:
    """Overlapping touches or a shared node_id never run at once; disjoint ready slices do."""
    assert he.globs_overlap("src/a/**", "src/a/x.py")
    assert he.globs_overlap("src/*/x.py", "src/b/y.py")  # conservative past a wildcard
    assert not he.globs_overlap("src/a/**", "docs/**")
    assert not he.globs_overlap("src/a/x.py", "src/a/y.py")
    assert he.conflicts(he.Slice("x", "x"), he.Slice("y", "y", touches=[]))  # unknown touches
    canned = mission(
        repo,
        ("B", [], ["src/a/**"], "b"),
        ("C", [], ["src/a/x.py"], "c"),
        ("E", [], ["docs/**"], "e"),
        ("F", [], ["f/**"], "shared"),
        ("G", [], ["g/**"], "shared"),
    )
    pair = threading.Barrier(2)  # B and E are disjoint, so they must run at the same time
    runner = FakeRunner(hooks={"B": meet(pair), "E": meet(pair)})
    assert run(repo, runner, make_probes(canned=canned)) == 0
    assert sorted(runner.calls()) == ["B", "C", "E", "F", "G"]
    assert not any({"B", "C"} <= running or {"F", "G"} <= running for running in runner.together)
    assert runner.index("start", "C") > runner.index("end", "B")
    assert runner.index("start", "G") > runner.index("end", "F")


def test_gate_stop_upstream_blocked(repo: GitRepo) -> None:
    """A gate stop blocks A; B, C and D become upstream_blocked and never run."""
    canned = mission(repo, *DIAMOND)
    runner = FakeRunner(outcomes={"A": [he.SliceOutcome(F.gate_stopped, detail="low_confidence")]})
    assert run(repo, runner, make_probes(canned=canned)) == 1
    assert runner.calls() == ["A"]
    slices = state(repo)["slices"]
    assert (slices["A"]["status"], slices["A"]["failure_type"]) == ("blocked", "gate_stopped")
    for sid in "BCD":
        assert (slices[sid]["status"], slices[sid]["failure_type"]) == (
            "upstream_blocked",
            "upstream_blocked",
        )
    found = events(repo)
    assert {e["slice"] for e in found if e["event"] == "upstream_blocked"} == set("BCD")
    assert [e["event"] for e in found if e["slice"] == "A"][-1] == "blocked"


def test_resume_skips_audited(repo: GitRepo, capsys) -> None:
    """--resume schedules only what is not audited and keeps the recorded base."""
    canned = mission(repo, *DIAMOND)
    first = FakeRunner(outcomes={"C": [he.SliceOutcome(F.gate_stopped)]})
    assert run(repo, first, make_probes(canned=canned)) == 1
    before = state(repo)
    statuses = [before["slices"][sid]["status"] for sid in "ABCD"]
    assert statuses == ["audited", "audited", "blocked", "upstream_blocked"]
    repo.write("later.txt", "HEAD moves on\n")
    repo.commit("later")

    # engine.lock is shared through the git common dir; a held lock exits 3 naming its holder.
    common = Path(repo.git("rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    with he.engine_lock(common, "20261020T000000Z-abcdef"):
        assert run(repo, FakeRunner(), make_probes(canned=canned), before["run_id"]) == 3
    assert "held by run 20261020T000000Z-abcdef" in capsys.readouterr().err
    assert (common / "hacf" / "locks" / "engine.lock").is_file()
    assert not (repo.path / ".agents" / "state" / "locks").exists()

    again = FakeRunner()
    assert run(repo, again, make_probes(canned=canned), before["run_id"]) == 0
    assert again.calls() == ["C", "D"]
    after = state(repo)
    assert after["base_sha"] == before["base_sha"] != repo.git("rev-parse", "HEAD").strip()
    assert after["base_ledger_sha256"] == before["base_ledger_sha256"]
    assert after["created_at"] == before["created_at"]
    assert after["status"] == "finished" and len(after["resumed"]) == 1
    assert [after["slices"][sid]["attempts"] for sid in "ABCD"] == [1, 1, 2, 1]


# ---------------------------------------------------------------------------
# Failure taxonomy, rate limits, budget
# ---------------------------------------------------------------------------


def test_failure_classification(repo: GitRepo) -> None:
    """ExecResults and engine conditions map to one FailureType and one RetryPolicy action."""
    names = [
        "preflight_failed",
        "executor_unavailable",
        "executor_auth",
        "sandbox_unavailable",
        "rate_limited",
        "timeout",
        "executor_error",
        "oracle_failed",
        "audit_failed",
        "turns_exceeded",
        "budget_exceeded",
        "gate_stopped",
        "scope_violation",
        "ref_violation",
        "merge_conflict",
        "commit_rejected",
        "ledger_invalid",
        "lore_invalid",
        "upstream_blocked",
        "internal_error",
    ]
    assert [failure.value for failure in F] == names
    assert set(hf.CATEGORY) == set(F)
    policy = hf.RetryPolicy(he.DEFAULTS["autonomy"]["retries"])
    ok, bad = hx.ExecResult(True), hx.ExecResult
    retry_once = [(0, "retry", None), (1, "block", None)]
    fatal = [(0, "fatal", None)]
    table = [
        ({"result": ok}, None, []),
        (
            {"result": bad(False, "rate_limited")},
            F.rate_limited,
            [(0, "pause_all", 30.0), (1, "pause_all", 120.0), (2, "block", None)],
        ),
        ({"timed_out": True}, F.timeout, retry_once),
        ({"result": bad(False, "timeout")}, F.timeout, retry_once),
        ({"result": bad(False, "executor_error")}, F.executor_error, retry_once),
        ({"result": bad(False, "a_new_failure")}, F.executor_error, retry_once),
        ({"result": bad(False, None)}, F.executor_error, retry_once),
        ({"result": bad(False, "executor_auth")}, F.executor_auth, fatal),
        ({"result": bad(False, "sandbox_unavailable")}, F.sandbox_unavailable, fatal),
        ({"result": bad(False, "turns_exceeded")}, F.turns_exceeded, [(0, "block", None)]),
        ({"result": bad(False, "budget_exceeded")}, F.budget_exceeded, [(0, "block", None)]),
        ({"result": ok, "oracle_exit": 1}, F.oracle_failed, retry_once),
        ({"result": ok, "oracle_exit": 0, "audit_verdict": "FAIL"}, F.audit_failed, retry_once),
        ({"result": ok, "oracle_exit": 0, "audit_verdict": "pass"}, None, []),
    ]
    for kwargs, expected, actions in table:
        assert hf.classify(**kwargs) is expected, kwargs
        for attempts, kind, delay in actions:
            assert policy.next_action(expected, attempts) == (kind, delay), (expected, attempts)
    for failure, kind in (
        (F.ledger_invalid, "rollback_then_block"),
        (F.lore_invalid, "rollback_then_block"),
        (F.gate_stopped, "block"),
        (F.scope_violation, "block"),
        (F.ref_violation, "block"),
        (F.merge_conflict, "block"),
        (F.commit_rejected, "block"),
        (F.upstream_blocked, "block"),
        (F.preflight_failed, "fatal"),
        (F.internal_error, "abort"),
    ):
        assert policy.next_action(failure, 0).kind == kind, failure

    # internal_error (here a runner bug) aborts the run with its state saved.
    canned = mission(repo, *DIAMOND)

    def bug(ctx: dict) -> None:
        raise RuntimeError("runner bug")

    runner = FakeRunner(hooks={"A": bug})
    assert run(repo, runner, make_probes(canned=canned)) == 1
    data = state(repo)
    assert (data["status"], data["failure_type"]) == ("aborted", "internal_error")
    assert data["slices"]["A"]["status"] == "pending" and runner.calls() == ["A"]


def test_rate_limited_pauses_all_workers(repo: GitRepo) -> None:
    """A rate_limited result pauses every worker 30 s, the next one 120 s; no per-slice retry."""
    canned = mission(repo, ("A", [], ["a/**"], "a"), ("B", [], ["b/**"], "b"))
    started, b_waiting, resumed = threading.Barrier(2), threading.Event(), []
    # The engine's first pause sleep holds until B, still running, has reached the gate.
    clock = FakeClock(on_sleep=lambda n: n == 1 and b_waiting.wait(10))

    def a_hook(ctx: dict) -> None:
        if ctx["attempt"] == 1:
            started.wait(timeout=10)

    def b_hook(ctx: dict) -> None:
        started.wait(timeout=10)
        deadline = time.monotonic() + 10
        while not ctx["gate"].paused():
            assert time.monotonic() < deadline, "the pause never started"
            time.sleep(0.005)
        b_waiting.set()
        ctx["gate"].wait()  # B's next phase waits out the pause
        resumed.append(clock.now)

    limited = he.SliceOutcome(F.rate_limited)
    runner = FakeRunner(
        outcomes={"A": [limited, limited]}, hooks={"A": a_hook, "B": b_hook}, clock=clock
    )
    assert run(repo, runner, make_probes(clock=clock, canned=canned)) == 0
    assert clock.sleeps == [30.0, 120.0]
    assert resumed and resumed[0] >= 30.0
    a_starts = [at for kind, sid, at in runner.log if kind == "start" and sid == "A"]
    assert a_starts == [0.0, 30.0, 150.0]  # A runs again only after each pause ends
    data = state(repo)
    assert data["rate_limit_pauses"] == 2
    assert data["slices"]["A"]["attempts"] == 3 and data["slices"]["A"]["retries"] == {}
    assert [e["detail"]["seconds"] for e in events(repo) if e["event"] == "paused"] == [30, 120]


def test_run_budget_stops_scheduling(repo: GitRepo) -> None:
    """Reaching max_budget_usd_per_run schedules nothing more; in-flight slices finish."""
    canned = mission(repo, *((s, [], [f"{s.lower()}/**"], s.lower()) for s in "ABCD"))
    local = {"orchestration": {"autonomy": {"max_parallel": 2}}}
    repo.write(".agents/local/hacf.yml", yaml.safe_dump(local))
    pair = threading.Barrier(2)
    runner = FakeRunner(
        outcomes={"A": [he.SliceOutcome(cost_usd=40.0)], "B": [he.SliceOutcome(cost_usd=1.5)]},
        hooks={"A": meet(pair), "B": meet_then_wait_for(pair, "A")},  # A's cost lands first
    )
    assert run(repo, runner, make_probes(canned=canned), max_budget_usd_per_run=30.0) == 1
    assert sorted(runner.calls()) == ["A", "B"]
    data = state(repo)
    assert (data["status"], data["failure_type"]) == ("stopped", "budget_exceeded")
    assert data["cost_usd"] == 41.5
    assert data["security"]["autonomy.max_budget_usd_per_run"] == 30.0
    statuses = [data["slices"][sid]["status"] for sid in "ABCD"]
    assert statuses == ["audited", "audited", "pending", "pending"]
    assert [e["slice"] for e in events(repo) if e["event"] == "budget_exceeded"] == [None]


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def test_preflight_missing_ledger_names_init(tmp_git_repo: GitRepo, monkeypatch, capsys) -> None:
    """No ledger: exit 5, naming hyper_ledger.py init and /hyper-discover (R21)."""
    monkeypatch.chdir(tmp_git_repo.path)
    assert he.main(["preflight", "--unverified-executor"], probes=make_probes()) == 5
    failed = json.loads(capsys.readouterr().out)["checks"][-1]
    assert (failed["name"], failed["failure_type"]) == ("ledger", "preflight_failed")
    assert "python3 .agents/scripts/hyper_ledger.py init" in failed["message"]
    assert "/hyper-discover" in failed["message"]


def test_preflight_unverified_executor_refused(repo: GitRepo, monkeypatch, capsys) -> None:
    """verified: false refuses; --unverified-executor passes and run.json records it (R20)."""
    monkeypatch.chdir(repo.path)
    assert he.main(["preflight"], probes=make_probes()) == 5
    failed = json.loads(capsys.readouterr().out)["checks"][-1]
    assert (failed["name"], failed["failure_type"]) == ("executor", "preflight_failed")
    assert "--unverified-executor" in failed["message"]

    assert he.main(["preflight", "--unverified-executor"], probes=make_probes()) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and [check["name"] for check in report["checks"]] == [
        "git_worktree",
        "git_clean",
        "git_base",
        "executor",
        "agent_sandbox",
        "oracle_sandbox",
        "oracle_argv",
        "ledger",
        "lint",
        "ledger_validate",
        "dag",
    ]

    assert run(repo, FakeRunner(), make_probes(), unverified_executor=True) == 0
    data = state(repo)
    assert data["flags"]["unverified_executor"] is True
    capsys.readouterr()
    assert he.main(["status", data["run_id"]], probes=make_probes()) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == data["run_id"]
    assert he.main(["status", "../../etc"], probes=make_probes()) == 2  # not a run id


def test_preflight_bwrap_missing_is_fatal(repo: GitRepo, monkeypatch, capsys) -> None:
    """No bwrap on Linux: sandbox_unavailable, exit 5; the flag passes with a warning (R2)."""
    no_bwrap = lambda tool: None if tool == "bwrap" else f"/usr/bin/{tool}"  # noqa: E731

    def check(platform: str, which=no_bwrap, **flags) -> he.Preflight:
        probes = make_probes(which=which, platform=platform)
        flags = {"unverified_executor": True, **flags}
        config, _ = he.resolve_config(repo.path, flags)
        return he.preflight(he.discover(repo.path, probes), config, flags, probes)

    def last(report: he.Preflight) -> tuple:
        return report.checks[-1]["name"], report.checks[-1]["failure_type"]

    report = check("linux")  # bwrap also backs the agent sandbox on Linux
    assert not report.ok and last(report) == ("agent_sandbox", "sandbox_unavailable")
    report = check("linux", allow_unsandboxed=True)
    assert not report.ok and last(report) == ("oracle_sandbox", "sandbox_unavailable")
    assert "bwrap is not on PATH" in report.checks[-1]["message"]
    monkeypatch.chdir(repo.path)
    probes = make_probes(which=no_bwrap)
    argv = ["preflight", "--unverified-executor", "--allow-unsandboxed"]
    assert he.main(argv, probes=probes) == 5
    capsys.readouterr()

    report = check("linux", allow_unsandboxed=True, allow_unsandboxed_oracle=True)
    assert report.ok
    assert any("--allow-unsandboxed-oracle" in warning for warning in report.warnings)
    assert "WITHOUT BWRAP" in capsys.readouterr().err

    present = lambda tool: f"/usr/bin/{tool}"  # noqa: E731
    report = check("darwin", which=present)
    assert not report.ok and last(report) == ("oracle_sandbox", "sandbox_unavailable")
    assert "macOS" in report.checks[-1]["message"]
    assert check("darwin", which=present, allow_unsandboxed_oracle=True).ok


# ---------------------------------------------------------------------------
# Config and run.json
# ---------------------------------------------------------------------------


def test_config_tracked_override_cannot_loosen(tmp_path: Path, capsys) -> None:
    """Tracked files only tighten security keys; a loosening value warns and is ignored (R4)."""
    config, ignored = he.resolve_config(tmp_path)
    assert config == he.DEFAULTS and ignored == []
    assert "run HACF-install.sh to complete the upgrade" in capsys.readouterr().err

    shipped = {
        "oracle": {"allowed_executables": ["uv", "pytest", "bash"]},
        "autonomy": {"max_budget_usd_per_run": 20.0},
    }
    local = {
        "orchestration": {
            "oracle": {"sandbox": "none", "allowed_executables": ["uv", "pytest"]},
            "autonomy": {
                "max_budget_usd_per_run": 10.0,
                "max_budget_usd_per_phase": 99.0,
                "max_parallel": 1,
                "env_passthrough": ["PATH", "AWS_SECRET_ACCESS_KEY"],
                "scope_policy": "strict",
            },
            "executors": {
                "claude-cli": {"verified": True, "sandbox": "off"},
                "codex-cli": {"status": "stable"},
                "new-cli": {"argv": ["new-cli", "{prompt}"], "sandbox": "off"},
            },
            "git": "off",
        }
    }
    (tmp_path / ".agents" / "config").mkdir(parents=True)
    (tmp_path / ".agents" / "local").mkdir()
    (tmp_path / he.CONFIG_REL).write_text(yaml.safe_dump(shipped))
    (tmp_path / he.LOCAL_REL).write_text(yaml.safe_dump(local))
    config, ignored = he.resolve_config(tmp_path)
    assert config["oracle"]["sandbox"] == "bwrap"
    assert config["oracle"]["allowed_executables"] == ["uv", "pytest"]  # a subset is honored
    autonomy = config["autonomy"]
    assert autonomy["max_budget_usd_per_run"] == 10.0  # 20, then 10: each one only lower
    assert autonomy["max_budget_usd_per_phase"] == 5.0
    assert autonomy["max_parallel"] == 1  # not a security key
    assert autonomy["env_passthrough"] == he.DEFAULTS["autonomy"]["env_passthrough"]
    assert autonomy["scope_policy"] == "strict"
    claude = config["executors"]["claude-cli"]
    assert (claude["verified"], claude["sandbox"]) == (False, "required")
    assert config["executors"]["codex-cli"]["status"] == "experimental"
    new = config["executors"]["new-cli"]
    assert new["status"] == "experimental" and "sandbox" not in new
    assert config["git"] == he.DEFAULTS["git"]
    err = capsys.readouterr().err
    assert ".agents/local/hacf.yml: oracle.sandbox cannot loosen a security key; ignored" in err
    assert ".agents/config/orchestration.yml: oracle.allowed_executables cannot loosen" in err
    for key in (
        "autonomy.env_passthrough",
        "autonomy.max_budget_usd_per_phase",
        "executors.claude-cli.verified",
        "executors.claude-cli.sandbox",
        "executors.codex-cli.status",
        "executors.new-cli.sandbox",
        "git must be a mapping",
    ):
        assert any(key in message for message in ignored), key

    # Only CLI flags loosen.
    flags = {"allow_unsandboxed": True, "allow_unsandboxed_oracle": True}
    config, _ = he.resolve_config(tmp_path, {**flags, "max_budget_usd_per_run": 99.0})
    assert config["oracle"]["sandbox"] == "none"
    assert config["autonomy"]["max_budget_usd_per_run"] == 99.0
    assert config["executors"]["claude-cli"]["sandbox"] == "off"


def test_config_resolved_from_repo_top(repo: GitRepo, monkeypatch, capsys) -> None:
    """Run from a subdirectory, preflight and run still read the repo top's config files."""
    repo.write(".agents/config/orchestration.yml", CONFIG.read_text(encoding="utf-8"))
    local = {"orchestration": {"autonomy": {"max_budget_usd_per_run": 10.0}}}
    repo.write(".agents/local/hacf.yml", yaml.safe_dump(local))
    seen = []
    real = he.preflight
    spy = lambda r, config, *a: seen.append(config) or real(r, config, *a)  # noqa: E731
    monkeypatch.setattr(he, "preflight", spy)
    monkeypatch.setattr(he, "WorktreeRunner", FakeRunner)  # the CLI run's runner; no slices
    (repo.path / "sub").mkdir()
    monkeypatch.chdir(repo.path / "sub")
    assert he.main(["preflight", "--unverified-executor"], probes=make_probes()) == 0
    assert he.main(["run", "--unverified-executor"], probes=make_probes()) == 0
    assert [config["autonomy"]["max_budget_usd_per_run"] for config in seen] == [10.0, 10.0]
    assert "run HACF-install.sh" not in capsys.readouterr().err


def test_run_json_records_base_ledger_sha(repo: GitRepo) -> None:
    """run.json holds the main checkout's ledger sha256 at preflight and the base (R5)."""
    for name, node in (("02_Beta", "beta"), ("01_Alpha", "alpha"), ("Extra", "extra")):
        text = f"# MiniPRD: {name}\n\n**Hypergraph Node ID:** {node}\n"
        repo.write(f"spec/compiled/MiniPRD_{name}.md", text)
    repo.commit("legacy")
    ledger = (repo.path / "spec" / "compiled" / "architecture.yml").read_bytes()
    probes = make_probes(canned={"hyper_spec.py": (0, "lint: ok\n", "")})  # stub MiniPRDs
    runner = FakeRunner()
    assert run(repo, runner, probes) == 0
    # The legacy NN chain: by NN, then lexically, each after its predecessor.
    assert runner.calls() == ["MiniPRD_01_Alpha", "MiniPRD_02_Beta", "MiniPRD_Extra"]
    plan = he.build_plan(he.discover(repo.path, probes), he.DEFAULTS, probes)
    assert [(item.depends_on, item.node_id, item.touches) for item in plan] == [
        ([], "alpha", None),
        (["MiniPRD_01_Alpha"], "beta", None),
        (["MiniPRD_02_Beta"], "extra", None),
    ]
    data = state(repo)
    assert data["base_ledger_sha256"] == hashlib.sha256(ledger).hexdigest()
    assert data["base_sha"] == repo.git("rev-parse", "HEAD").strip()
    assert data["base_ref"] == "HEAD" and data["claude_version"] == "2.1.300"
    assert data["security"] == he.security_values(he.DEFAULTS)
    assert data["security"]["oracle.sandbox"] == "bwrap"
    assert (repo.path / ".agents" / "state" / ".gitignore").read_text() == "*\n"
    names = sorted(path.name for path in run_path(repo).iterdir())
    assert names == ["events.jsonl", "ledger", "run.json"]
    assert (run_path(repo) / "ledger" / "base.yml").read_bytes() == ledger  # land's --base-file
    assert repo.git("status", "--porcelain") == ""  # .agents/state ignores itself


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------


def test_engine_no_anthropic_import() -> None:
    """Neither new script imports anthropic; both import _runtime."""
    for name in ("hyper_engine.py", "hyper_failures.py"):
        tree = ast.parse((SCRIPTS_DIR / name).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        assert "anthropic" not in imported, name
        assert "_runtime" in imported, name


def test_embedded_defaults_match_shipped_config() -> None:
    """The embedded defaults equal orchestration.yml, security_keys and their values included."""
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert data == he.DEFAULTS
    assert data["security_keys"] == SECURITY_KEYS == he.DEFAULTS["security_keys"]
    assert he.security_values(data) == he.security_values(he.DEFAULTS)
