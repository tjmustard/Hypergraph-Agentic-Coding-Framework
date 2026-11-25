"""openrig/hacf/bin/context-guard.sh restarts at most one idle non-lead seat over the cap.

Mission v063-measure-context OPR.99.0.7.4 (RedTeam F13); per-role caps, the stale-sample rule and
spec/active attribution from v064-context OPR.99.0.8.1. No real `rig`: a fake `rig` first on a
temp PATH serves canned JSON per verb from files the test writes and appends each argv to
calls.log. HOME is a temp dir, and the project roots are throwaway git repos under tmp_path.
Every run passes `--caps` with a test caps file (beside a copy of the shipped roles.yml) whose
caps stand in for the old HACF_CTX_MAX default of 150000.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD = REPO_ROOT / "openrig" / "hacf" / "bin" / "context-guard.sh"
README = REPO_ROOT / "openrig" / "hacf" / "README.md"
ROLES = REPO_ROOT / "openrig" / "hacf" / "roles.yml"
RIG = "hacf"
LEAD = "orch-lead@hacf"
SEAT = "build-execute@hacf"
OTHER = "build-audit@hacf"
HOST_LINE = (
    '"<hacf checkout>/openrig/hacf/bin/context-guard.sh" --rig <rig> '
    '--project-root "<project root>" --caps "<hacf checkout>/openrig/hacf/caps.yml" '
    "--arm-checkpoints --apply || true"
)
HOST_EXAMPLE = (
    '"<hacf checkout>/openrig/hacf/bin/context-guard.sh" --rig hacf '
    '--project-root "<hacf checkout>" --caps "<hacf checkout>/openrig/hacf/caps.yml" '
    "--arm-checkpoints --apply || true"
)
HOST_COMMENT = (
    "# HACF context guard: one line per HACF rig on this host, one tick per digest run "
    "(OPS-A, v0.6.5)"
)
# the test caps: every role at the old 150000 default, so the v0.6.3 cases keep their numbers
TEST_CAP = 150000


def caps_doc(**caps: int) -> dict:
    """A valid caps document; keyword args override a role's cap_tokens (red_team for red-team)."""
    seats = {}
    for role in yaml.safe_load(ROLES.read_text())["roles"]:
        behaviour = {"lead": "planned_handover", "execute": "checkpoint_message"}.get(
            role, "restart_at_durable_point"
        )
        scope = "slice" if role in ("execute", "audit") else "mission"
        cap = caps.get(role.replace("-", "_"), TEST_CAP)
        seats[role] = {"cap_tokens": cap, "behaviour": behaviour, "reset_scope": scope}
    return {"schema": "hacf.caps/v1", "seats": seats, "reevaluate": []}


def write_caps(where: Path, doc: dict) -> Path:
    """Write doc as caps.yml beside a copy of the shipped roles.yml; return its path."""
    where.mkdir(parents=True, exist_ok=True)
    (where / "roles.yml").write_text(ROLES.read_text())
    path = where / "caps.yml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


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
if argv[:2] == ["queue", "create"]:
    serve("queue-create", "{}")
if argv[:2] == ["seat", "launch"]:
    if "--plan" in argv:
        serve("plan", '{"launchable": true}')
    serve("launch", "{}")
if argv[:1] == ["send"]:
    serve("send", "")
if argv[:2] == ["watchdog", "list"]:
    serve("watchdog-list", "[]")
if argv[:2] == ["watchdog", "register"]:
    serve("watchdog-register", '{"jobId": "wd-new"}')
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
    profile: str | None = None,
    **extra,
) -> dict:
    """One `rig ps --nodes --full --json` entry; a candidate unless told otherwise.

    The profile defaults to the member part of the logical id (build.execute -> execute).
    """
    n = {
        "logicalId": logical,
        "profile": profile if profile is not None else logical.split(".", 1)[1],
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
        self.caps = write_caps(tmp_path / "caps", caps_doc())
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

    def run(self, *extra: str, apply: bool = False, env: dict | None = None, caps: bool = True):
        """Run the guard on the world's roots with the world's caps file."""
        args = ["bash", str(GUARD), "--rig", RIG]
        for root in self.roots:
            args += ["--project-root", str(root)]
        if caps:
            args += ["--caps", str(self.caps)]
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


@pytest.fixture(autouse=True)
def never_stops_a_watchdog(tmp_path: Path):
    """OPR.99.0.8.2: no test run ever calls `rig watchdog stop` (or update)."""
    yield
    log = tmp_path / "fake" / "calls.log"
    if log.exists():
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        assert not [c for c in calls if c[:2] in (["watchdog", "stop"], ["watchdog", "update"])]


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
    parts = line.split(" ", 7)
    assert parts[0].endswith("Z") and "T" in parts[0]
    assert parts[1:7] == [
        "rig=hacf",
        "mode=dry-run",
        f"seat={LEAD}",
        "tokens=50000",
        f"max={TEST_CAP}",
        "action=skip",
    ]
    assert parts[7] == "reason=orch.lead is exempt"
    log_dir = world.home / ".hacf" / RIG
    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700


def test_custom_cap_and_log(world: World) -> None:
    """The role cap in --caps (was HACF_CTX_MAX, OPR.99.0.8.1 MR 10) and --log are honoured."""
    world.caps = write_caps(world.tmp / "caps250", caps_doc(execute=250000))
    log = world.tmp / "logs" / "guard.log"
    result = world.run("--log", str(log), apply=True)
    assert result.returncode == 0
    assert world.launches() == []
    assert "under the cap (200000 <= 250000)" in log.read_text()
    assert "max=250000" in log.read_text()


# --- refusals: each leaves every seat alone ------------------------------------------------


@pytest.mark.parametrize(
    ("nodes", "reason"),
    [
        ([lead(), seat(state="running")], "busy (agentActivity.state=running)"),
        ([lead(), seat(state="unknown")], "unknown: agentActivity.state=unknown"),
        ([lead(), seat(stale=True)], "unknown: agentActivity is stale"),
        # OPR.99.0.8.1 MR 5: a sample that is not fresh now needs its transcript; none -> unknown
        ([lead(), seat(fresh=False)], "transcript unknown (no transcriptPath)"),
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
    assert "seat=- tokens=- max=- action=skip" in world.log_lines[0]


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


SPEC_SEAT = "spec-resolve@hacf"


def spec_seat(tokens: int = 200000, **kw) -> dict:
    """The spec-resolve entry."""
    return node("spec.resolve", SPEC_SEAT, tokens, **kw)


def test_ignored_file_in_spec_active(world: World) -> None:
    """An ignored file deep under spec/active/ blocks a spec seat (R8: spec seats only)."""
    (world.root / "spec" / "active" / "deep").mkdir()
    (world.root / "spec" / "active" / "deep" / "Draft_PRD.md").write_text("x\n")
    world.ps([lead(), spec_seat()])
    assert_no_restart(
        world,
        world.run(apply=True),
        f"spec/active work in {world.root} (spec seats only)",
        SPEC_SEAT,
    )


def test_ignored_file_in_spec_active_spares_build_seats(world: World) -> None:
    """The same spec/active/ file does not block a build seat (R8)."""
    (world.root / "spec" / "active" / "deep").mkdir()
    (world.root / "spec" / "active" / "deep" / "Draft_PRD.md").write_text("x\n")
    result = world.run(apply=True)
    assert result.returncode == 0, result.stderr
    assert len(world.launches()) == 1 and world.launches()[0][2] == SEAT


def _second_worktree(world: World) -> Path:
    wt = world.tmp / "wt2"
    _git(world.root, "worktree", "add", "-q", "-b", "side", str(wt))
    return wt


@pytest.mark.parametrize("where", ["tracked", "untracked"])
def test_dirty_second_worktree(world: World, where: str) -> None:
    """A dirty second worktree of a root blocks every seat."""
    wt = _second_worktree(world)
    if where == "tracked":
        (wt / "README").write_text("changed\n")
    else:
        (wt / "new.txt").write_text("x\n")
    world.ps([lead(), seat(), spec_seat(tokens=190000)])
    result = world.run(apply=True)
    assert_no_restart(world, result, f"uncommitted work in {wt}")
    assert f"reason=uncommitted work in {wt}" in world.line_for(SPEC_SEAT)


def test_spec_active_in_second_worktree(world: World) -> None:
    """spec/active/ work in a second worktree blocks the spec seats only (R8)."""
    wt = _second_worktree(world)
    (wt / "spec" / "active").mkdir(parents=True, exist_ok=True)
    (wt / "spec" / "active" / "SuperPRD.md").write_text("x\n")
    world.ps([lead(), seat(), spec_seat(tokens=300000)])
    result = world.run(apply=True)
    assert result.returncode == 0, result.stderr
    assert len(world.launches()) == 1 and world.launches()[0][2] == SEAT
    assert f"reason=spec/active work in {wt} (spec seats only)" in world.line_for(SPEC_SEAT)


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
        (["--project-root", ".", "--caps", "CAPS"], {}),
        (["--rig", RIG, "--caps", "CAPS"], {}),
        (["--rig", RIG, "--project-root", "."], {}),
        (["--rig", RIG, "--project-root", ".", "--apply"], {"HACF_CTX_MAX": "250000"}),
    ],
    ids=["no-rig", "no-root", "no-caps", "apply-no-caps"],
)
def test_usage_exit_2(world: World, args: list[str], env: dict) -> None:
    """Usage errors, a missing --caps above all, exit 2 before any rig call.

    The v0.6.3 max-abc and max-zero cases left with HACF_CTX_MAX (OPR.99.0.8.1 MR 3): the
    variable is no longer read, and test_hacf_ctx_max_is_ignored covers it being set.
    """
    args = [str(world.caps) if a == "CAPS" else a for a in args]
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
    """The README carries the per-rig host line template once, then the hacf worked example."""
    text = README.read_text()
    assert text.count(HOST_LINE) == 1
    line = next(ln for ln in text.splitlines() if 'context-guard.sh" --rig' in ln)
    assert line.strip() == HOST_LINE
    assert line.count("--apply") == 1 and line.count("--rig <rig>") == 1
    assert text.count(HOST_EXAMPLE) == 1
    assert line.count("--caps") == 1
    assert HOST_COMMENT in text
    files = text.split("## Files", 1)[1]
    assert "bin/context-guard.sh" in files and "caps.yml" in files


def test_guard_is_executable() -> None:
    """The shipped script has the owner execute bit."""
    assert GUARD.stat().st_mode & stat.S_IXUSR


# --- v0.6.4: per-role caps, the stale-sample rule, root attribution (OPR.99.0.8.1) ---------

SAMPLED_AT = "2026-11-24T03:00:00.250Z"
SAMPLED_EPOCH = 1795489200.25  # SAMPLED_AT as epoch seconds
FAILED_HEADER = "# Failed Workflows\n\nThe workflows that failed, newest first.\n"


def stale_node(
    w: World,
    logical: str,
    session: str,
    tokens: int,
    *,
    offset: float = -10.0,
    sid: str = "11111111-2222-3333-4444-555555555555",
    name: str | None = None,
    create: bool = True,
    sampled_at: str | None = SAMPLED_AT,
    **kw,
) -> dict:
    """An idle seat whose sample is not fresh; its transcript's mtime is sampledAt + offset."""
    tdir = w.tmp / "transcripts" / session
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / (name or sid + ".jsonl")
    if create:
        path.write_text('{"type": "user"}\n')
        os.utime(path, (SAMPLED_EPOCH + offset, SAMPLED_EPOCH + offset))
    usage = {
        "availability": "known",
        "fresh": False,
        "totalInputTokens": tokens,
        "transcriptPath": str(path),
        "sessionId": sid,
    }
    if sampled_at is not None:
        usage["sampledAt"] = sampled_at
    return node(logical, session, tokens, fresh=False, contextUsage=usage, **kw)


def real_layout(w: World) -> Path:
    """Replace the world's root with one laid out like HACF's main checkout."""
    root = w.tmp / "hacf-main"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "README").write_text("x\n")
    (root / ".gitignore").write_text("/spec/\n")
    _git(root, "add", "README", ".gitignore")
    _git(root, "commit", "-q", "-m", "init")
    (root / "spec" / "active").mkdir(parents=True)
    (root / "spec" / "active" / "FAILED_WORKFLOWS.md").write_text(FAILED_HEADER)
    w.root = root
    w.roots = [root]
    return root


def audit_stale(w: World, tokens: int = 300000, **kw) -> dict:
    """build-audit, idle, stale sample, transcript 10 s older than the sample."""
    return stale_node(w, "build.audit", OTHER, tokens, **kw)


def test_epoch_constant() -> None:
    """SAMPLED_EPOCH is SAMPLED_AT."""
    from datetime import datetime

    when = datetime.fromisoformat(SAMPLED_AT.replace("Z", "+00:00"))
    assert when.timestamp() == SAMPLED_EPOCH


def test_real_layout_stale_unchanged_would_restart(world: World) -> None:
    """Gitignored /spec/ + FAILED_WORKFLOWS.md, idle over-cap audit, stale-but-unchanged sample."""
    real_layout(world)
    world.caps = write_caps(world.tmp / "caps-real", caps_doc(audit=250000))
    world.ps([lead(), audit_stale(world)])
    result = world.run()
    assert result.returncode == 0, result.stderr
    line = world.line_for(OTHER)
    assert "action=would-restart" in line and "max=250000" in line
    assert f"stale sample, transcript unchanged since {SAMPLED_AT}" in line
    assert world.launches() == []


def test_failed_workflows_exempt_only_at_top_level(world: World) -> None:
    """A FAILED_WORKFLOWS.md deeper in spec/active/ is spec work like any other file."""
    root = real_layout(world)
    (root / "spec" / "active" / "old").mkdir()
    (root / "spec" / "active" / "old" / "FAILED_WORKFLOWS.md").write_text(FAILED_HEADER)
    world.ps([lead(), spec_seat()])
    reason = f"spec/active work in {root} (spec seats only)"
    assert_no_restart(world, world.run(apply=True), reason, SPEC_SEAT)


def test_real_layout_draft_blocks_spec_seats_only(world: World) -> None:
    """A Phase-1 draft in spec/active/: build-audit would restart, spec-resolve skips."""
    root = real_layout(world)
    (root / "spec" / "active" / "Draft_PRD.md").write_text("# Draft\n")
    resolve = stale_node(world, "spec.resolve", SPEC_SEAT, 310000, profile="resolve")
    world.ps([lead(), audit_stale(world), resolve])
    result = world.run()
    assert result.returncode == 0, result.stderr
    assert "action=would-restart" in world.line_for(OTHER)
    line = world.line_for(SPEC_SEAT)
    assert f"action=skip reason=spec/active work in {root} (spec seats only)" in line


def test_real_layout_untracked_blocks_everyone(world: World) -> None:
    """An untracked, non-ignored notes.txt blocks every candidate."""
    root = real_layout(world)
    (root / "notes.txt").write_text("x\n")
    resolve = stale_node(world, "spec.resolve", SPEC_SEAT, 310000, profile="resolve")
    world.ps([lead(), audit_stale(world), resolve])
    result = world.run(apply=True)
    assert_no_restart(world, result, f"uncommitted work in {root}", OTHER)
    assert f"reason=uncommitted work in {root}" in world.line_for(SPEC_SEAT)


def test_reread_applies_attribution(world: World) -> None:
    """A draft that appears before the re-read blocks a spec seat chosen on the first read."""
    root = real_layout(world)
    world.ps([lead(), stale_node(world, "spec.resolve", SPEC_SEAT, 310000)])
    # the fake rig writes the draft on its second ps answer: serve ps2 only after it exists
    draft = root / "spec" / "active" / "Draft_PRD.md"
    hook = world.tmp / "bin" / "rig"
    hook.write_text(
        FAKE_RIG.replace(
            'if argv[:1] == ["ps"]:\n',
            'if argv[:1] == ["ps"]:\n'
            "    if os.path.exists(os.path.join(d, 'ps.count')):\n"
            f"        open({str(draft)!r}, 'w').write('x')\n",
            1,
        )
    )
    result = world.run(apply=True)
    assert_no_restart(world, result, "changed on re-read (spec/active work in", SPEC_SEAT)


def test_transcript_grew(world: World) -> None:
    """A transcript written 10 s after the sample blocks the seat."""
    world.ps([lead(), audit_stale(world, offset=10.0)])
    assert_no_restart(world, world.run(apply=True), "transcript grew since sample", OTHER)


def test_transcript_within_slack(world: World) -> None:
    """An mtime 1.5 s after sampledAt is inside the 2 s tolerance."""
    world.ps([lead(), audit_stale(world, offset=1.5)])
    world.run()
    assert "action=would-restart" in world.line_for(OTHER)


@pytest.mark.parametrize(
    "kw",
    [
        {"name": "other-session.jsonl"},
        {"create": False},
        {"sampled_at": None},
        {"sampled_at": "2026-11-24 03:00:00"},
        {"sid": ""},
    ],
    ids=["basename", "missing-file", "no-sampledAt", "bad-sampledAt", "no-sessionId"],
)
def test_transcript_unknown(world: World, kw: dict) -> None:
    """An unidentifiable transcript or sample time blocks the seat."""
    world.ps([lead(), audit_stale(world, **kw)])
    assert_no_restart(world, world.run(apply=True), "transcript unknown", OTHER)


def test_stale_sample_needs_idle_and_known(world: World) -> None:
    """The stale rule does not lift the idle, activity-stale or availability rules."""
    busy = audit_stale(world, state="running")
    world.ps([lead(), busy])
    assert_no_restart(world, world.run(apply=True), "busy (agentActivity.state=running)", OTHER)


@pytest.mark.parametrize(
    ("logical", "session", "tokens", "restarts"),
    [
        ("build.execute", SEAT, 300000, False),
        ("build.execute", SEAT, 300001, True),
        ("desk.scribe", "desk-scribe@hacf", 150000, False),
        ("desk.scribe", "desk-scribe@hacf", 150001, True),
    ],
    ids=["execute-at", "execute-over", "scribe-at", "scribe-over"],
)
def test_role_caps(world: World, logical: str, session: str, tokens: int, restarts: bool) -> None:
    """Each seat is held to its own role's cap from --caps (synthetic caps, OPR.99.0.10.1)."""
    world.caps = write_caps(world.tmp / "rolecaps", caps_doc(execute=300000, scribe=150000))
    world.ps([lead(), node(logical, session, tokens)])
    result = world.run()
    assert result.returncode == 0, result.stderr
    line = world.line_for(session)
    cap = tokens if not restarts else tokens - 1
    assert f"max={cap}" in line
    if restarts:
        assert "action=would-restart" in line
        assert f"{tokens} input tokens > {cap}" in line
    else:
        assert f"action=skip reason=under the cap ({tokens} <= {cap})" in line


def test_planned_handover_profile(world: World) -> None:
    """A lead-profile seat that is not orch.lead is never restarted."""
    world.ps([lead(), node("orch.second", "orch-second@hacf", 900000, profile="lead")])
    result = world.run(apply=True)
    assert_no_restart(world, result, "planned handover seat (never restarted)", "orch-second@hacf")


@pytest.mark.parametrize("profile", ["janitor", ""])
def test_unknown_profile(world: World, profile: str) -> None:
    """A profile with no cap is left alone."""
    world.ps([lead(), seat(profile=profile)])
    assert_no_restart(world, world.run(apply=True), "unknown: no cap for profile", SEAT)


def test_no_profile(world: World) -> None:
    """A node without a profile is left alone."""
    n = seat()
    del n["profile"]
    world.ps([lead(), n])
    assert_no_restart(world, world.run(apply=True), "unknown: no cap for profile None", SEAT)


def test_caps_missing_a_role(world: World) -> None:
    """A caps file missing scribe exits 2 with one stderr line, before any rig call."""
    doc = caps_doc()
    del doc["seats"]["scribe"]
    world.caps = write_caps(world.tmp / "caps-noscribe", doc)
    result = world.run(apply=True)
    assert result.returncode == 2
    err = result.stderr.strip().splitlines()
    assert len(err) == 1 and "scribe" in err[0]
    assert world.calls == []


def test_hacf_ctx_max_is_ignored(world: World) -> None:
    """HACF_CTX_MAX set: one note line per tick, the caps still decide, the run continues."""
    world.caps = write_caps(world.tmp / "caps250", caps_doc(execute=250000))
    result = world.run(env={"HACF_CTX_MAX": "abc"})
    assert result.returncode == 0, result.stderr
    notes = [
        ln for ln in world.log_lines if "note=HACF_CTX_MAX ignored; caps come from --caps" in ln
    ]
    assert len(notes) == 1
    assert "under the cap (200000 <= 250000)" in world.line_for(SEAT)


def test_shipped_guard_needs_caps() -> None:
    """The shipped script names --caps in its usage and never reads HACF_CTX_MAX for a cap."""
    text = GUARD.read_text()
    assert "--caps <file>" in text
    assert 'environ.get("HACF_CTX_MAX"' not in text


# --- v0.6.4: checkpoint watchdogs (OPR.99.0.8.2) ----------------------------------------------

SHIPPED_CAPS = REPO_ROOT / "openrig" / "hacf" / "caps.yml"
EXEC_MSG = (
    "context-guard checkpoint: your context passed 300000 tokens (openrig/hacf/caps.yml). "
    "Finish the step you are on. Do not commit and do not stash. Write a checkpoint note: "
    "python3 .agents/scripts/hyper_lore.py note --mission <your mission dir> with the tasks done, "
    "the next task, the files touched and <base>. Tell orch-lead in one line that you are ready "
    "for a fresh launch, then end your turn. If you already wrote the note for this slice, "
    "ignore this."
)
LEAD_MSG = (
    "context-guard handover: your context passed 350000 tokens (openrig/hacf/caps.yml). At your "
    "next durable point, plan your handover (openrig-core retiring-and-inheriting-a-seat). "
    "The guard never restarts you."
)


def registers(w: World) -> list[list[str]]:
    """The watchdog register calls."""
    return [c for c in w.calls if c[:2] == ["watchdog", "register"]]


def arg(argv: list[str], flag: str) -> str:
    """The value after a flag."""
    return argv[argv.index(flag) + 1]


def job(session: str, job_id: str, state: str = "active", policy: str = "context-usage-threshold"):
    """One compact `rig watchdog list --json` entry."""
    return {
        "jobId": job_id,
        "policy": policy,
        "targetSession": session,
        "intervalSeconds": 300,
        "state": state,
        "bindingState": "bound",
    }


@pytest.fixture
def armw(world: World) -> World:
    """The world with the shipped caps and a full team: lead, execute (under its cap), the rest."""
    world.caps = SHIPPED_CAPS
    world.ps(
        [
            lead(),
            seat(tokens=100000),
            node("build.audit", OTHER, 100000),
            node("desk.scribe", "desk-scribe@hacf", 100000),
            node("spec.architect", "spec-architect@hacf", 100000),
            node("spec.red-team", "spec-red-team@hacf", 100000),
            node("spec.resolve", "spec-resolve@hacf", 100000),
        ]
    )
    return world


def test_arm_registers_one_per_checkpoint_seat(armw: World) -> None:
    """No jobs + --apply: exactly two registers, thresholds and messages from caps.yml."""
    # synthetic caps (OPR.99.0.10.1): the shipped values are pinned by test_caps.py alone
    armw.caps = write_caps(armw.tmp / "armcaps", caps_doc(execute=300000, lead=350000))
    result = armw.run("--arm-checkpoints", apply=True)
    assert result.returncode == 0, result.stderr
    regs = registers(armw)
    assert len(regs) == 2
    by_target = {arg(r, "--target-session"): r for r in regs}
    assert set(by_target) == {SEAT, LEAD}
    for target, cap, msg in ((SEAT, "300000", EXEC_MSG), (LEAD, "350000", LEAD_MSG)):
        r = by_target[target]
        assert arg(r, "--policy") == "context-usage-threshold"
        assert arg(r, "--threshold-tokens") == cap
        assert arg(r, "--interval-seconds") == "300"
        assert arg(r, "--registered-by") == LEAD
        assert arg(r, "--message") == msg
        assert r[-1] == "--json"
    arms = [ln for ln in armw.log_lines if "action=arm " in ln]
    assert len(arms) == 2
    assert any(f"seat={SEAT} " in ln and "reason=registered at 300000" in ln for ln in arms)
    assert any(f"seat={LEAD} " in ln and "reason=registered at 350000" in ln for ln in arms)
    # arming comes before the restart logic, and nothing is launched from it
    first_reg = next(i for i, c in enumerate(armw.calls) if c[:2] == ["watchdog", "register"])
    assert not any(c[:2] == ["queue", "list"] for c in armw.calls[:first_reg])
    assert armw.launches() == [] and armw.sends() == []


def test_arm_dry_run_would_arm(armw: World) -> None:
    """Without --apply: no register, two would-arm lines."""
    result = armw.run("--arm-checkpoints")
    assert result.returncode == 0, result.stderr
    assert registers(armw) == []
    lines = [ln for ln in armw.log_lines if "action=would-arm" in ln]
    assert len(lines) == 2
    assert {ln.split(" seat=", 1)[1].split(" ", 1)[0] for ln in lines} == {SEAT, LEAD}


def test_arm_is_idempotent(armw: World) -> None:
    """One active job per seat: no register, two checkpoint-armed lines."""
    armw.put("watchdog-list.json", json.dumps([job(SEAT, "wd-1"), job(LEAD, "wd-2")]))
    result = armw.run("--arm-checkpoints", apply=True)
    assert result.returncode == 0, result.stderr
    assert registers(armw) == []
    assert any(
        f"seat={SEAT} " in ln and "reason=checkpoint armed wd-1" in ln for ln in armw.log_lines
    )
    assert any(
        f"seat={LEAD} " in ln and "reason=checkpoint armed wd-2" in ln for ln in armw.log_lines
    )


def test_arm_reports_duplicates(armw: World) -> None:
    """Two active jobs for execute: reported, none added for it; the lead still gets its one."""
    armw.put("watchdog-list.json", json.dumps([job(SEAT, "wd-1"), job(SEAT, "wd-3")]))
    result = armw.run("--arm-checkpoints", apply=True)
    assert result.returncode == 0, result.stderr
    regs = registers(armw)
    assert [arg(r, "--target-session") for r in regs] == [LEAD]
    assert any(
        f"seat={SEAT} " in ln and "reason=duplicate checkpoint jobs wd-1,wd-3" in ln
        for ln in armw.log_lines
    )


@pytest.mark.parametrize(
    "jobs",
    [
        [job(SEAT, "wd-old", state="stopped"), job(LEAD, "wd-2")],
        [job(SEAT, "wd-p", policy="periodic-reminder"), job(LEAD, "wd-2")],
        [job("other@hacf", "wd-o"), job(LEAD, "wd-2")],
    ],
    ids=["stopped", "other-policy", "other-target"],
)
def test_arm_ignores_non_matching_jobs(armw: World, jobs: list) -> None:
    """A stopped job, another policy or another target does not count: execute registers."""
    armw.put("watchdog-list.json", json.dumps(jobs))
    result = armw.run("--arm-checkpoints", apply=True)
    assert result.returncode == 0, result.stderr
    assert [arg(r, "--target-session") for r in registers(armw)] == [SEAT]


@pytest.mark.parametrize(("text", "code"), [("[]", "1"), ("not json", "0"), ('{"a": 1}', "0")])
def test_arm_list_unreadable(world: World, text: str, code: str) -> None:
    """An unreadable list arms nothing, logs unknown, and the restart logic still runs."""
    world.put("watchdog-list.json", text)
    world.put("watchdog-list.exit", code)
    # the default world: execute at 200000 over the 150000 test cap
    result = world.run("--arm-checkpoints", apply=True)
    assert result.returncode == 0, result.stderr
    assert registers(world) == []
    assert any(
        "seat=- tokens=- max=- action=skip reason=unknown: watchdog list unreadable" in ln
        for ln in world.log_lines
    )
    assert len(world.launches()) == 1


def test_arm_register_fails(armw: World) -> None:
    """A failed register logs failed and the run exits 1."""
    armw.put("watchdog-register.exit", "1")
    result = armw.run("--arm-checkpoints", apply=True)
    assert result.returncode == 1
    failed = [
        ln for ln in armw.log_lines if "action=failed reason=watchdog register exited 1" in ln
    ]
    assert len(failed) == 2


def test_arm_register_fails_restart_still_runs(world: World) -> None:
    """A failed register does not stop the tick's restart; the exit is still 1."""
    world.put("watchdog-register.exit", "1")
    result = world.run("--arm-checkpoints", apply=True)
    assert result.returncode == 1
    assert len(world.launches()) == 1


def test_arm_only_checkpoint_seats(armw: World) -> None:
    """audit, scribe and the spec seats never get a register."""
    armw.run("--arm-checkpoints", apply=True)
    targets = {arg(r, "--target-session") for r in registers(armw)}
    assert targets == {SEAT, LEAD}


def test_arm_skips_a_stopped_seat(armw: World) -> None:
    """A checkpoint seat that is not running gets no register."""
    armw.ps([lead(), seat(tokens=100000, lifecycleState="stopped")])
    armw.run("--arm-checkpoints", apply=True)
    assert [arg(r, "--target-session") for r in registers(armw)] == [LEAD]


def test_no_flag_no_watchdog_call(armw: World) -> None:
    """Without --arm-checkpoints the guard makes no watchdog call at all."""
    armw.run(apply=True)
    assert not [c for c in armw.calls if c[:1] == ["watchdog"]]


GUIDANCE = REPO_ROOT / "openrig" / "hacf" / "agents" / "hacf" / "guidance"


def section(path: Path, heading: str) -> list[str]:
    """The lines of one `## ` section, heading excluded, up to the next `## `."""
    lines = path.read_text().splitlines()
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = lines[start:end]
    while body and not body[-1].strip():
        body.pop()
    return body


def test_execute_guidance_checkpoint_section() -> None:
    """execute.md: no commit or stash, the NOTES note, the one line to the lead; <= 15 lines."""
    body = section(GUIDANCE / "execute.md", "## Context checkpoint")
    assert 0 < len(body) <= 15
    text = " ".join(body)
    assert "context-guard checkpoint" in text
    assert "Do not commit, stash or `git add`" in text
    assert "`Start..<commit>`" in text
    assert "hyper_lore.py note --mission" in text
    assert "ready for a fresh launch" in text and "orch-lead" in text


def test_lead_guidance_prompts_section() -> None:
    """lead.md: wait for idle, relaunch the builder fresh, the row stays; own handover; <= 15."""
    body = section(GUIDANCE / "lead.md", "## Checkpoint and handover prompts")
    assert 0 < len(body) <= 15
    text = " ".join(body)
    assert (
        'rig seat launch build-execute@<rig> --fresh --stop --reason "context checkpoint"' in text
    )
    assert "idle" in text and "in-progress" in text and "NOTES.md" in text
    assert "retiring-and-inheriting-a-seat" in text and "never restarts you" in text


def test_guard_never_names_watchdog_stop() -> None:
    """The shipped guard has no code path that stops or updates a watchdog."""
    code = GUARD.read_text()
    assert '"watchdog", "stop"' not in code and '"watchdog", "update"' not in code


# --- OPR.99.0.10.8: the guard per rig (R40) ----------------------------------------------------

DEMO = "demo-hacf"  # a stand-in rig name


def test_readme_host_line_is_per_rig() -> None:
    """MR 1: one line per HACF rig, named as rig whoami reports it; arming is per rig."""
    flat = " ".join(README.read_text().split())
    assert "one line per HACF rig on the host" in flat
    assert "as `rig whoami --json` reports it" in flat
    assert "`--arm-checkpoints` registers **per rig**" in flat
    assert "arms jobs for that rig's seats only" in flat
    assert "the operator's step, under the standing policy" in flat


def test_guard_runs_per_rig_beside_hacf(world: World) -> None:
    """--rig demo-hacf beside hacf: its own state dir, only its seats armed, hacf untouched."""
    hacf_state = world.home / ".hacf" / RIG
    hacf_state.mkdir(parents=True)
    (hacf_state / "context-guard.log").write_text("hacf line\n")
    before = {p.name: p.read_bytes() for p in hacf_state.iterdir()}
    demo_lead, demo_seat = f"orch-lead@{DEMO}", f"build-execute@{DEMO}"
    world.ps([node("orch.lead", demo_lead, 50000), node("build.execute", demo_seat, 100000)])
    # hacf's own checkpoint jobs are active; they must neither count for nor block demo-hacf
    world.put("watchdog-list.json", json.dumps([job(SEAT, "wd-h1"), job(LEAD, "wd-h2")]))
    caps = write_caps(world.tmp / "democaps", caps_doc(execute=300000, lead=350000))
    args = ["bash", str(GUARD), "--rig", DEMO, "--project-root", str(world.root)]
    args += ["--caps", str(caps), "--arm-checkpoints", "--apply"]
    result = subprocess.run(args, env=world.env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    demo_state = world.home / ".hacf" / DEMO
    assert (demo_state / "context-guard.log").is_file()
    assert any(
        f"rig={DEMO} " in ln for ln in (demo_state / "context-guard.log").read_text().splitlines()
    )
    ps_calls = [c for c in world.calls if c[:1] == ["ps"]]
    assert ps_calls and all(arg(c, "--rig") == DEMO for c in ps_calls)
    regs = registers(world)
    assert {arg(r, "--target-session") for r in regs} == {demo_seat, demo_lead}
    assert all(arg(r, "--registered-by") == demo_lead for r in regs)
    assert {p.name: p.read_bytes() for p in hacf_state.iterdir()} == before


# --- seat config: caps through seat-config.py (v0.6.6, OPR.99.0.11.6) ------------------------

SEAT_CONFIG = REPO_ROOT / "openrig" / "hacf" / "seat-config.yml"
INVALID_OVERRIDES = "seats:\n  execute:\n    modle: claude-opus-5-5\n"


def with_seat_config(w: World, overrides: str | None = None) -> None:
    """Put the shipped seat-config.yml (and an overrides.yml) beside the world's caps file."""
    (w.caps.parent / "seat-config.yml").write_text(SEAT_CONFIG.read_text())
    if overrides is not None:
        (w.caps.parent / "overrides.yml").write_text(overrides)


def alert_creates(w: World) -> list[list[str]]:
    """The `rig queue create` calls the fake received."""
    return [c for c in w.calls if c[:2] == ["queue", "create"]]


def markers(w: World) -> list[Path]:
    """The alert markers under the world's HOME."""
    where = w.home / ".hacf" / RIG / "guard-alerts"
    return sorted(where.iterdir()) if where.exists() else []


def test_f_a_cap_override_is_the_cap_the_guard_acts_on(world: World) -> None:
    """(f) With seat-config.yml, an overrides.yml cap decides: 200000 is over 150000, not 300000."""
    with_seat_config(world, "seats:\n  execute:\n    cap_tokens: 300000\n")
    result = world.run(apply=True)
    assert result.returncode == 0, result.stderr
    assert world.launches() == [] and world.sends() == []
    line = world.line_for(SEAT)
    assert "max=300000" in line and "action=skip" in line
    assert any(
        "note=caps_source=resolved non-default: execute.cap_tokens=override" in ln
        for ln in world.log_lines
    ), world.log_lines
    # the same seat without the override restarts at the default cap
    (world.tmp / "plain").mkdir()
    plain = World(world.tmp / "plain")
    with_seat_config(plain)
    assert plain.run(apply=True).returncode == 0
    assert len(plain.launches()) == 1
    assert any(ln.endswith("note=caps_source=resolved") for ln in plain.log_lines), plain.log_lines


def test_h_without_seat_config_the_caps_file_is_read_as_before(world: World) -> None:
    """(h) No seat-config.yml: no caps_source note (lead ruling P39), as before."""
    result = world.run(apply=True)
    assert result.returncode == 0, result.stderr
    assert not any("caps_source" in ln for ln in world.log_lines)
    assert len(world.launches()) == 1


def test_g_an_invalid_overrides_runs_on_defaults_and_alerts_once(world: World) -> None:
    """R70 (TJ P8 = A): defaults, a refusal line, two rows and one marker; a second run is quiet."""
    with_seat_config(world, INVALID_OVERRIDES)
    first = world.run(apply=True)
    assert first.returncode == 0, first.stderr
    assert len(world.launches()) == 1  # it ran on HACF's defaults (150000) and acted on them
    assert any(
        "note=caps_source=defaults (seat config refused: overrides.yml: seats.execute.modle" in ln
        for ln in world.log_lines
    ), world.log_lines
    creates = alert_creates(world)
    assert [c[c.index("--destination") + 1] for c in creates] == [
        f"orch-lead@{RIG}",
        "operator-agent@kernel",
    ]
    for call in creates:
        summary = call[call.index("--summary") + 1]
        assert summary == (
            "context-guard: overrides.yml invalid (overrides.yml: seats.execute.modle);"
            " running on HACF defaults"
        )
    assert len(markers(world)) == 1
    world.put("ps.json", json.dumps([lead(), seat()]))
    second = world.run(apply=True)
    assert second.returncode == 0
    assert len(alert_creates(world)) == 2 and len(markers(world)) == 1


def test_g_a_failed_alert_makes_no_marker_and_retries(world: World) -> None:
    """A failing `rig queue create` is no alert: no marker, and the next tick tries again."""
    with_seat_config(world, INVALID_OVERRIDES)
    world.put("queue-create.exit", "1")
    assert world.run(apply=True).returncode == 0
    assert len(alert_creates(world)) == 2 and markers(world) == []
    (world.fake / "queue-create.exit").unlink()
    assert world.run(apply=True).returncode == 0
    assert len(alert_creates(world)) == 4 and len(markers(world)) == 1


def test_g_dry_run_alerts_nobody(world: World) -> None:
    """Without --apply the guard prints the would-alert line and writes nothing."""
    with_seat_config(world, INVALID_OVERRIDES)
    result = world.run()
    assert result.returncode == 0, result.stderr
    assert alert_creates(world) == [] and markers(world) == []
    assert any(
        "note=would alert: context-guard: overrides.yml invalid" in ln for ln in world.log_lines
    )


def test_g_a_different_refusal_alerts_again(world: World) -> None:
    """The marker is the sha256 of the full refusal text: a new error is a new alert."""
    with_seat_config(world, INVALID_OVERRIDES)
    world.run(apply=True)
    (world.caps.parent / "overrides.yml").write_text("seats:\n  execute:\n    effort: loud\n")
    world.put("ps.json", json.dumps([lead(), seat()]))
    world.run(apply=True)
    assert len(alert_creates(world)) == 4 and len(markers(world)) == 2
