"""openrig/hacf/bin/context-guard.sh restarts at most one idle non-lead seat over the cap.

Mission v063-measure-context OPR.99.0.7.4 (RedTeam F13). No real `rig`: a fake `rig` first on a
temp PATH serves canned JSON per verb from files the test writes and appends each argv to
calls.log. HOME is a temp dir, and the project roots are throwaway git repos under tmp_path.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD = REPO_ROOT / "openrig" / "hacf" / "bin" / "context-guard.sh"
README = REPO_ROOT / "openrig" / "hacf" / "README.md"
RIG = "hacf"
LEAD = "orch-lead@hacf"
SEAT = "build-execute@hacf"
OTHER = "build-audit@hacf"
HOST_LINE = (
    '"<hacf checkout>/openrig/hacf/bin/context-guard.sh" --rig hacf '
    '--project-root "<project root>" --apply || true'
)

FAKE_RIG = r"""#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_RIG_DIR"]
argv = sys.argv[1:]
with open(os.path.join(d, "calls.log"), "a") as f:
    f.write(json.dumps(argv) + "\n")

def serve(name, default):
    code_file = os.path.join(d, name + ".exit")
    code = int(open(code_file).read()) if os.path.exists(code_file) else 0
    path = os.path.join(d, name + ".json")
    sys.stdout.write(open(path).read() if os.path.exists(path) else default)
    sys.exit(code)

if argv[:1] == ["ps"]:
    counter = os.path.join(d, "ps.count")
    n = int(open(counter).read()) + 1 if os.path.exists(counter) else 1
    open(counter, "w").write(str(n))
    serve("ps%d" % n if os.path.exists(os.path.join(d, "ps%d.json" % n)) else "ps", "[]")
if argv[:2] == ["queue", "list"]:
    dest = argv[argv.index("--destination") + 1]
    own = os.path.exists(os.path.join(d, "queue-%s.json" % dest))
    serve("queue-" + dest if own else "queue", "[]")
if argv[:2] == ["seat", "launch"]:
    if "--plan" in argv:
        serve("plan", '{"launchable": true}')
    serve("launch", "{}")
if argv[:1] == ["send"]:
    serve("send", "")
sys.exit(97)
"""


def node(
    logical: str,
    session: str,
    tokens: int = 200000,
    *,
    state: str = "idle",
    stale: bool = False,
    fresh: bool = True,
    **extra,
) -> dict:
    """One `rig ps --nodes --full --json` entry; a candidate unless told otherwise."""
    n = {
        "logicalId": logical,
        "canonicalSessionName": session,
        "runtime": "claude-code",
        "lifecycleState": "running",
        "agentActivity": {"state": state, "stale": stale},
        "contextUsage": {"totalInputTokens": tokens, "fresh": fresh, "availability": "known"},
        "pendingWorkCount": 0,
        "inProgressWorkCount": 0,
        "blockedWorkCount": 0,
    }
    n.update(extra)
    return n


def lead(tokens: int = 50000) -> dict:
    """The orch.lead entry."""
    return node("orch.lead", LEAD, tokens)


def seat(tokens: int = 200000, **kw) -> dict:
    """The build-execute entry."""
    return node("build.execute", SEAT, tokens, **kw)


def _git(cwd: Path, *args: str) -> None:
    env = {
        "HOME": str(cwd),
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
    }
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        env=env,
    )


def make_repo(path: Path) -> Path:
    """A committed git repo whose .gitignore hides spec/active/*."""
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "README").write_text("x\n")
    (path / ".gitignore").write_text("spec/active/*\n")
    (path / "spec" / "active").mkdir(parents=True)
    _git(path, "add", "README", ".gitignore")
    _git(path, "commit", "-q", "-m", "init")
    return path


class World:
    """A temp HOME, a fake rig and one clean project root."""

    def __init__(self, tmp_path: Path) -> None:
        """Write the fake rig and a default tick: the lead plus one candidate."""
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.fake = tmp_path / "fake"
        self.fake.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        rig = bin_dir / "rig"
        rig.write_text(FAKE_RIG)
        rig.chmod(0o755)
        self.root = make_repo(tmp_path / "project")
        self.roots = [self.root]
        self.env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
            "FAKE_RIG_DIR": str(self.fake),
            "LC_ALL": "C",
        }
        self.ps([lead(), seat()])

    def ps(self, nodes, n: int | None = None) -> None:
        """Serve this node list for every ps call, or only the n-th."""
        name = "ps.json" if n is None else "ps%d.json" % n
        (self.fake / name).write_text(json.dumps(nodes))

    def put(self, name: str, text: str) -> None:
        """Write one canned answer or exit code for the fake."""
        (self.fake / name).write_text(text)

    def run(self, *extra: str, apply: bool = False, env: dict | None = None):
        """Run the guard on the world's roots."""
        args = ["bash", str(GUARD), "--rig", RIG]
        for root in self.roots:
            args += ["--project-root", str(root)]
        if apply:
            args.append("--apply")
        args += list(extra)
        e = dict(self.env, **(env or {}))
        return subprocess.run(args, env=e, capture_output=True, text=True, timeout=120)

    @property
    def calls(self) -> list[list[str]]:
        """Each argv the fake rig received, in order."""
        log = self.fake / "calls.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    @property
    def log_lines(self) -> list[str]:
        """The guard's default log."""
        log = self.home / ".hacf" / RIG / "context-guard.log"
        return log.read_text().splitlines() if log.exists() else []

    def launches(self) -> list[list[str]]:
        """Real launches, not plans."""
        return [c for c in self.calls if c[:2] == ["seat", "launch"] and "--plan" not in c]

    def sends(self) -> list[list[str]]:
        """The notes sent."""
        return [c for c in self.calls if c[:1] == ["send"]]

    def line_for(self, session: str) -> str:
        """The one log line for a seat."""
        hits = [ln for ln in self.log_lines if f" seat={session} " in ln]
        assert len(hits) == 1, self.log_lines
        return hits[0]


@pytest.fixture
def world(tmp_path: Path) -> World:
    """A fresh world per test."""
    return World(tmp_path)


def assert_no_restart(w: World, result, reason: str, session: str | None = SEAT) -> None:
    """No launch, no note, exit 0 and the named reason logged."""
    assert result.returncode == 0, result.stderr
    assert w.launches() == []
    assert w.sends() == []
    assert not any("action=restart" in ln or "action=would-restart" in ln for ln in w.log_lines)
    lines = w.log_lines if session is None else [w.line_for(session)]
    assert any(f"reason={reason}" in ln for ln in lines), w.log_lines


# --- the restart and the dry run -----------------------------------------------------------


def test_restart_under_apply(world: World) -> None:
    """Idle, over the cap, no rows, clean roots and --apply: one restart and one note."""
    result = world.run(apply=True)
    assert result.returncode == 0, result.stderr
    launches = world.launches()
    assert len(launches) == 1
    launch = launches[0]
    assert launch[:3] == ["seat", "launch", SEAT]
    assert "--fresh" in launch and "--stop" in launch and "--json" in launch
    reason = launch[launch.index("--reason") + 1]
    assert reason == "context-guard: 200000 input tokens > 150000, idle at a durable point"
    sends = world.sends()
    assert len(sends) == 1 and sends[0][1] == LEAD
    assert f"restarted {SEAT} (200000 > 150000)" in sends[0][2]
    line = world.line_for(SEAT)
    assert "mode=apply" in line and "tokens=200000" in line and "action=restart" in line
    assert line in result.stdout
    # the plan ran right before the launch, after the re-read
    verbs = [" ".join(c[:2]) for c in world.calls]
    plan_at = next(i for i, c in enumerate(world.calls) if "--plan" in c)
    assert verbs[plan_at - 1] == "queue list" and verbs.count("ps --nodes") == 2
    assert world.calls.index(launch) == plan_at + 1


def test_dry_run_is_the_default(world: World) -> None:
    """Without --apply only read verbs run and the line says would-restart."""
    result = world.run()
    assert result.returncode == 0, result.stderr
    assert world.launches() == []
    assert world.sends() == []
    assert any("--plan" in c for c in world.calls)
    assert {c[0] for c in world.calls} <= {"ps", "queue", "seat"}
    line = world.line_for(SEAT)
    assert "mode=dry-run" in line and "action=would-restart" in line


def test_lead_line_and_log_format(world: World) -> None:
    """Each line follows the documented format; the log dir is 0700."""
    world.run()
    line = world.line_for(LEAD)
    parts = line.split(" ", 6)
    assert parts[0].endswith("Z") and "T" in parts[0]
    assert parts[1:6] == ["rig=hacf", "mode=dry-run", f"seat={LEAD}", "tokens=50000", "action=skip"]
    assert parts[6] == "reason=orch.lead is exempt"
    log_dir = world.home / ".hacf" / RIG
    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700


def test_custom_cap_and_log(world: World) -> None:
    """HACF_CTX_MAX and --log are honoured."""
    log = world.tmp / "logs" / "guard.log"
    result = world.run("--log", str(log), apply=True, env={"HACF_CTX_MAX": "250000"})
    assert result.returncode == 0
    assert world.launches() == []
    assert "under the cap (200000 <= 250000)" in log.read_text()


# --- refusals: each leaves every seat alone ------------------------------------------------


@pytest.mark.parametrize(
    ("nodes", "reason"),
    [
        ([lead(), seat(state="running")], "busy (agentActivity.state=running)"),
        ([lead(), seat(state="unknown")], "unknown: agentActivity.state=unknown"),
        ([lead(), seat(stale=True)], "unknown: agentActivity is stale"),
        ([lead(), seat(fresh=False)], "unknown: contextUsage not fresh"),
        ([lead(), seat(tokens=150000)], "under the cap (150000 <= 150000)"),
        ([lead(), seat(pendingWorkCount=1)], "holds queue rows (pendingWorkCount=1)"),
        ([lead(), seat(blockedWorkCount=1)], "holds queue rows (blockedWorkCount=1)"),
        ([lead(), seat(lifecycleState="stopped")], "not running (lifecycleState=stopped)"),
        ([lead(), seat(contextUsage={"fresh": True})], "unknown: contextUsage not known"),
    ],
    ids=[
        "running",
        "unknown",
        "stale",
        "not-fresh",
        "at-cap",
        "pending",
        "blocked",
        "stopped",
        "no-availability",
    ],
)
def test_seat_not_a_candidate(world: World, nodes, reason: str) -> None:
    """A busy, unknown, stale, unfresh, capped or row-holding seat is left alone."""
    world.ps(nodes)
    assert_no_restart(world, world.run(apply=True), reason)


def test_lead_is_never_restarted(world: World) -> None:
    """orch.lead is exempt however large its context."""
    world.ps([lead(tokens=400000)])
    result = world.run(apply=True)
    assert_no_restart(world, result, "orch.lead is exempt", LEAD)
    assert "tokens=400000" in world.line_for(LEAD)


def test_queue_row_in_progress(world: World) -> None:
    """An active row in rig queue list blocks the seat even when the counts are 0."""
    rows = [{"qitemId": "q-1", "state": "in-progress", "destinationSession": SEAT}]
    world.put(f"queue-{SEAT}.json", json.dumps(rows))
    assert_no_restart(world, world.run(apply=True), "holds queue rows (q-1)")
    q = next(c for c in world.calls if c[:2] == ["queue", "list"])
    assert q[2:] == [
        "-A",
        "--destination",
        SEAT,
        "--state",
        "pending,in-progress,blocked",
        "--json",
    ]


def test_queue_call_fails(world: World) -> None:
    """A failing queue list means busy."""
    world.put("queue.exit", "1")
    assert_no_restart(world, world.run(apply=True), "unknown: queue list unreadable")


@pytest.mark.parametrize(("text", "code"), [("[]", "1"), ("not json", "0"), ('{"a": 1}', "0")])
def test_ps_unreadable(world: World, text: str, code: str) -> None:
    """A failing or malformed ps stops the tick before any other call."""
    world.put("ps.json", text)
    world.put("ps.exit", code)
    result = world.run(apply=True)
    assert_no_restart(world, result, "unknown: ps unreadable", None)
    assert [c[0] for c in world.calls] == ["ps"]
    assert "seat=- tokens=- action=skip" in world.log_lines[0]


def test_no_lead_entry(world: World) -> None:
    """No orch.lead in ps means no restart this tick."""
    world.ps([seat()])
    assert_no_restart(world, world.run(apply=True), "no orch.lead", None)
    assert [c[0] for c in world.calls] == ["ps"]


def test_modified_tracked_file(world: World) -> None:
    """A modified tracked file blocks every seat."""
    (world.root / "README").write_text("changed\n")
    assert_no_restart(world, world.run(apply=True), f"uncommitted work in {world.root}")


def test_untracked_file(world: World) -> None:
    """An untracked file blocks every seat."""
    (world.root / "new.txt").write_text("x\n")
    assert_no_restart(world, world.run(apply=True), f"uncommitted work in {world.root}")


def test_ignored_file_in_spec_active(world: World) -> None:
    """An ignored file deep under spec/active/ blocks every seat."""
    (world.root / "spec" / "active" / "deep").mkdir()
    (world.root / "spec" / "active" / "deep" / "Draft_PRD.md").write_text("x\n")
    assert_no_restart(world, world.run(apply=True), f"uncommitted work in {world.root}")


def _second_worktree(world: World) -> Path:
    wt = world.tmp / "wt2"
    _git(world.root, "worktree", "add", "-q", "-b", "side", str(wt))
    return wt


@pytest.mark.parametrize("where", ["tracked", "untracked", "spec-active"])
def test_dirty_second_worktree(world: World, where: str) -> None:
    """A dirty second worktree of a root blocks every seat."""
    wt = _second_worktree(world)
    if where == "tracked":
        (wt / "README").write_text("changed\n")
    elif where == "untracked":
        (wt / "new.txt").write_text("x\n")
    else:
        (wt / "spec" / "active").mkdir(parents=True, exist_ok=True)
        (wt / "spec" / "active" / "SuperPRD.md").write_text("x\n")
    assert_no_restart(world, world.run(apply=True), f"uncommitted work in {wt}")


def test_clean_second_worktree_and_prunable_one(world: World) -> None:
    """A clean worktree passes and a prunable one is skipped."""
    _second_worktree(world)
    gone = world.tmp / "wt3"
    _git(world.root, "worktree", "add", "-q", "-b", "gone", str(gone))
    for p in sorted(gone.rglob("*"), reverse=True):
        p.unlink() if p.is_file() or p.is_symlink() else p.rmdir()
    gone.rmdir()  # git now marks it prunable, so the guard skips it
    result = world.run(apply=True)
    assert result.returncode == 0, result.stdout
    assert len(world.launches()) == 1


def test_second_root_not_a_repo(world: World) -> None:
    """A --project-root that is not a git repo means unknown."""
    plain = world.tmp / "plain"
    plain.mkdir()
    world.roots.append(plain)
    assert_no_restart(world, world.run(apply=True), f"unknown: {plain}")


# --- one per tick, the re-read and the plan --------------------------------------------------


def test_two_candidates_only_the_largest(world: World) -> None:
    """Only the seat with the most tokens is restarted."""
    world.ps([lead(), seat(tokens=200000), node("build.audit", OTHER, 300000)])
    result = world.run(apply=True)
    assert result.returncode == 0, result.stderr
    launches = world.launches()
    assert len(launches) == 1 and launches[0][2] == OTHER
    assert "action=restart" in world.line_for(OTHER)
    assert "action=skip reason=one restart per tick" in world.line_for(SEAT)


def test_reread_shows_running(world: World) -> None:
    """A seat that turns busy on the re-read is left alone."""
    world.ps([lead(), seat(state="running")], n=2)
    result = world.run(apply=True)
    assert_no_restart(world, result, "changed on re-read (busy")
    assert not any("--plan" in c for c in world.calls)


def test_reread_without_the_seat(world: World) -> None:
    """A seat missing from the re-read is left alone."""
    world.ps([lead()], n=2)
    assert_no_restart(world, world.run(apply=True), "changed on re-read")


@pytest.mark.parametrize("plan", ['{"launchable": false}', "{}", "not json"])
def test_plan_not_launchable(world: World, plan: str) -> None:
    """A plan without launchable: true means no launch."""
    world.put("plan.json", plan)
    assert_no_restart(world, world.run(apply=True), "not launchable")
    p = next(c for c in world.calls if "--plan" in c)
    assert p == ["seat", "launch", SEAT, "--fresh", "--stop", "--plan", "--json"]


@pytest.mark.parametrize("apply", [True, False], ids=["apply", "dry-run"])
def test_plan_always_carries_stop(world: World, apply: bool) -> None:
    """Ruling P17: the preview mirrors the real launch, so no plan argv lacks --stop."""
    world.run(apply=apply)
    plans = [c for c in world.calls if "--plan" in c]
    assert len(plans) == 1
    assert all("--stop" in c and "--fresh" in c for c in plans)


def test_launch_fails(world: World) -> None:
    """A failed launch exits 1 and sends no note."""
    world.put("launch.exit", "1")
    result = world.run(apply=True)
    assert result.returncode == 1
    assert len(world.launches()) == 1
    assert world.sends() == []
    assert "action=failed reason=launch exited 1" in world.line_for(SEAT)


def test_note_fails(world: World) -> None:
    """A failed note after a restart exits 1."""
    world.put("send.exit", "1")
    result = world.run(apply=True)
    assert result.returncode == 1
    assert any("action=restart" in ln for ln in world.log_lines)
    assert any(f"action=failed reason=note to {LEAD} exited 1" in ln for ln in world.log_lines)


def test_held_lock(world: World) -> None:
    """A held lock skips the tick without calling rig."""
    lock = world.home / ".hacf" / RIG / "context-guard.lock"
    lock.parent.mkdir(parents=True)
    with open(lock, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = world.run(apply=True)
    assert result.returncode == 0
    assert world.calls == []
    assert "reason=another guard run" in result.stdout


# --- usage, README, the shipped file ---------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "env"),
    [
        (["--project-root", "."], {}),
        (["--rig", RIG], {}),
        (["--rig", RIG, "--project-root", "."], {"HACF_CTX_MAX": "abc"}),
        (["--rig", RIG, "--project-root", "."], {"HACF_CTX_MAX": "0"}),
    ],
    ids=["no-rig", "no-root", "max-abc", "max-zero"],
)
def test_usage_exit_2(world: World, args: list[str], env: dict) -> None:
    """Usage errors and a bad cap exit 2 before any rig call."""
    result = subprocess.run(
        ["bash", str(GUARD), *args],
        env=dict(world.env, **env),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert "usage: context-guard.sh --rig" in result.stderr
    assert world.calls == []


def test_readme_documents_the_host_line() -> None:
    """The README carries the OPS-2 host line exactly once."""
    text = README.read_text()
    assert text.count(HOST_LINE) == 1
    line = next(ln for ln in text.splitlines() if 'context-guard.sh" --rig' in ln)
    assert line.strip() == HOST_LINE
    assert line.count("--apply") == 1 and line.count("--rig hacf") == 1
    assert "# HACF context guard: rig hacf only, one tick per digest run (OPS-2)" in text
    assert "bin/context-guard.sh" in text.split("## Files", 1)[1]


def test_guard_is_executable() -> None:
    """The shipped script has the owner execute bit."""
    assert GUARD.stat().st_mode & stat.S_IXUSR
