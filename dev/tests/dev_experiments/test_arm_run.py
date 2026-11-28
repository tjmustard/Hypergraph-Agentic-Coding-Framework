"""A full fake arm run of every arm: the copy, the seats, the checks, the stops and the guard."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from .conftest import REPO_ROOT, World, full_script

# The advisor slash command as a token; the kit's own path names are not it.
COMMAND = re.compile(r"(?<![\w.~}\-])/" + r"advisor(?![\w\-/])")
SUBSTRING = "/" + "advisor"
LEAD = "claude-opus-5-5"


def drive(world: World, arm: int, run: int = 1, **state) -> object:
    """Run one arm with the full script."""
    world.probes_passed()
    if arm:
        world.baseline()
    state.setdefault("script", full_script())
    if arm in (1, 3):
        state.setdefault("advisor_calls", {"build.execute": True})
    world.set(**state)
    return world.run("run", "--arm", str(arm), "--run", str(run))


def last(world: World) -> dict:
    """The newest run record."""
    return world.runs()[-1]


def copy_of(world: World) -> Path:
    """The test rig's copy the fake made."""
    return Path(world.state()["copy"])


@pytest.mark.parametrize("arm", [0, 1, 2, 3])
def test_full_arm(world: World, arm: int) -> None:
    """Each arm: done, the copy's edits per arm, execute moved before any work, no command."""
    done = drive(world, arm)
    assert done.returncode == 0, done.stdout + done.stderr
    record = last(world)
    assert record["status"] == "done", record
    assert record["mixed"] == [] and record["advisor_unused"] is False
    assert record["rounds"] == {"01-small": 0, "02-medium": 0}
    assert record["first_pass"] == 2 and record["usd"] == pytest.approx(1.6)
    copy = copy_of(world)
    settings = json.loads((copy / ".hacf-exec/.claude/settings.local.json").read_text())
    assert settings["permissions"]["additionalDirectories"] == [str(copy.resolve())]
    assert settings.get("advisorModel") == (LEAD if arm in (1, 3) else None)
    assert (copy / ".claude/agents/hacf-scout.md").exists() == (arm in (2, 3))
    assert not (copy / ".hacf-exec/.claude/agents/hacf-scout.md").exists()
    assert ".hacf-exec/" in (copy / ".git/info/exclude").read_text().splitlines()
    spec = (copy / "openrig/hacf/rig.yaml").read_text()
    for key in ("HACF_HEAVY_LOCK_PATH", "HACF_HOST_STATE_DIR", "ENABLE_CLAUDEAI_MCP_SERVERS"):
        assert key in spec
    for role in ("lead", "audit"):
        text = (copy / f"openrig/hacf/agents/hacf/guidance/{role}.md").read_text()
        assert ("`hacf-scout` subagent" in text) == (arm in (2, 3))
    assert "hacf-scout" not in (copy / "openrig/hacf/agents/hacf/guidance/execute.md").read_text()

    calls = world.calls("rig")
    verbs = [" ".join(c["argv"][:2]) for c in calls]
    moved, launched = verbs.index("seat set-cwd"), verbs.index("seat launch")
    assert moved < launched < verbs.index("queue create")
    cwd = calls[moved]["argv"]
    assert cwd[cwd.index("--cwd") + 1] == f"{copy.resolve()}/.hacf-exec"
    assert "--fresh" in calls[launched]["argv"]
    everything = world.calls("rig") + world.calls("claude")
    for call in everything:
        words = [*call["argv"], call["stdin"]]
        assert not any(SUBSTRING in word for word in words), call
    for call in calls:
        if "/clear" in call["argv"]:
            assert "--raw" in call["argv"]
    entry = json.loads((world.evidence / "spend.json").read_text())["runs"][-1]
    assert entry["usd"] == pytest.approx(1.6) and entry["arm"] == arm
    run_dir = world.evidence / "runs" / entry["id"]
    assert (run_dir / "tool_paths-build.audit.json").is_file()
    assert list((run_dir / "telemetry").glob("*.json"))
    assert record["advisor_model_field_records"]["orch.lead"] >= 1


def test_source_untouched_by_drive(world: World) -> None:
    """P92: `_drive` writes the copy only; the source project's tree is as it was."""
    assert drive(world, 1).returncode == 0
    state = world.state()
    assert state["project_hash"] == state["project_hash_after"]
    assert state["copy"] != state["project"]


@pytest.mark.parametrize("verb", ["_drive", "_replay"])
@pytest.mark.parametrize("copy", [None, "", "/nonexistent-copy"])
def test_no_copy_no_call(world: World, verb: str, copy: str | None) -> None:
    """Without HACF_TEST_RIG_COPY the inner verbs exit 2 and call no rig."""
    extra = {"HACF_TEST_RIG": "hacf-test-x"}
    if copy is not None:
        extra["HACF_TEST_RIG_COPY"] = copy
    done = world.run(verb, "--arm", "0", "--run", "1", "--evidence", str(world.tmp), **extra)
    assert done.returncode == 2
    assert world.calls() == []


def test_wrong_cwd_is_void(world: World) -> None:
    """Execute not in the folder: exit 3, no work row."""
    world.probes_passed()
    world.set(script=full_script(), proc_cwd={"build.execute": "/"})
    done = world.run("run", "--arm", "0", "--run", "1", HACF_ADVISOR_WAIT_S="1")
    assert done.returncode == 3, done.stdout + done.stderr
    assert not any(c["argv"][:2] == ["queue", "create"] for c in world.calls("rig"))
    assert last(world)["status"].startswith("void")


@pytest.mark.parametrize(
    ("arm", "state"),
    [
        (0, {"argv": {"build.audit": ["--advisor", "opus"]}}),
        (2, {"argv": {"build.execute": ["--advisor=opus"]}}),
    ],
)
def test_configured_absence_seat_is_void(world: World, arm: int, state: dict) -> None:
    """P98: an advisor configured on an absence seat before slice 01 is exit 3."""
    done = drive(world, arm, **state)
    assert done.returncode == 3, done.stdout + done.stderr
    assert "advisor" in last(world)["status"]


def test_configured_unused(world: World) -> None:
    """P98: configured and never called is `advisor-unused`: not void, not mixed."""
    done = drive(world, 1, advisor_calls={})
    assert done.returncode == 0
    record = last(world)
    assert record["advisor_unused"] is True and record["mixed"] == []
    assert record["status"] == "done"


def test_activity_on_an_absence_seat_is_mixed(world: World) -> None:
    """P98: advisor activity without config marks the slice mixed."""
    script = full_script()
    script[1]["turn"] = [["build.audit", {"advisor": True}]]
    assert drive(world, 0, script=script).returncode == 0
    assert last(world)["mixed"] == ["01-small"]


def test_wrong_model_and_non_haiku_scout_are_mixed(world: World) -> None:
    """F8 and US-A04: a wrong seat model, or a scout off Haiku, marks the run mixed."""
    assert drive(world, 0, models={"build.audit": "claude-sonnet-5-5"}).returncode == 0
    assert last(world)["mixed"]
    script = full_script()
    script[1]["subagent"] = [["build.audit", "hacf-scout", "claude-sonnet-5-5"]]
    assert drive(world, 2, script=script, models={}).returncode == 0
    assert last(world)["mixed"]
    script = full_script()
    script[1]["subagent"] = [["build.audit", "hacf-scout", "claude-haiku-5-5"]]
    assert drive(world, 2, run=2, script=script).returncode == 0
    assert last(world)["mixed"] == []
    assert last(world)["helpers"][0]["agentType"] == "hacf-scout"


def stops(world: World, done, status: str) -> None:
    """A stop ends the run between steps, with scripted work left over."""
    assert done.returncode == 0, done.stdout + done.stderr
    assert last(world)["status"] == status
    assert world.state()["script"], "the run read past the stop"


def test_d6_stop(world: World) -> None:
    """A slice costing more than twice arm 0 run 1's stops the run."""
    world.probes_passed()
    world.baseline(slice_cost=0.1)
    script = full_script()
    script[1]["cost"] = {"build.execute": 5.0}
    world.set(script=script)
    done = world.run("run", "--arm", "2", "--run", "1")
    stops(world, done, "stopped: D6")


def test_d6_needs_the_baseline(world: World) -> None:
    """Without arm 0 run 1's slice costs D6 could not fire: refused."""
    done = world.run("run", "--arm", "2", "--run", "1")
    assert done.returncode == 2 and "arm 0 run 1" in done.stderr
    assert world.calls("test_rig") == []


@pytest.mark.parametrize("change", [{"weekly": 95}, dict(five_hour=90)])
def test_window_stop(world: World, change: dict) -> None:
    """The window stop voids the run."""
    script = full_script()
    script[1].update(change)
    stops(world, drive(world, 0, script=script), "stopped: window")
    assert last(world)["void"] is True


def test_wall_stop(world: World) -> None:
    """The wall cap stops the run."""
    world.set(script=full_script())
    done = world.run("run", "--arm", "0", "--run", "1", HACF_ADVISOR_WALL_S="0")
    stops(world, done, "stopped: wall")


def test_three_failed_rounds_end_a_slice(world: World) -> None:
    """Three audit FAILs exhaust a slice; the run ends done with its rounds."""
    script = []
    for _ in range(3):
        script += [{"status": [["01-small", "executed"]]}]
        script += [{"status": [["01-small", "audit_failed"]]}]
    script += full_script()[3:]
    assert drive(world, 0, script=script).returncode == 0
    record = last(world)
    assert record["rounds"] == {"01-small": 3, "02-medium": 0} and record["first_pass"] == 1


@pytest.mark.parametrize("arm", [1, 3])
def test_advisor_arms_need_the_probe(world: World, arm: int) -> None:
    """No passed mechanism probe, no advisor arm (R210)."""
    world.baseline()
    done = world.run("run", "--arm", str(arm), "--run", "1")
    assert done.returncode == 2 and "probe" in done.stderr
    (world.evidence / "probe-mechanism.json").write_text('{"go": false}')
    assert world.run("run", "--arm", str(arm), "--run", "1").returncode == 2
    assert world.calls("test_rig") == []


def test_guard_trips(world: World) -> None:
    """A settings change mid-run: restored byte for byte, one row, exit 4, halted."""
    settings = world.home / ".claude" / "settings.json"
    before = settings.read_bytes()
    script = full_script()
    script[1]["settings"] = '{"advisorModel": "x"}\n'
    done = drive(world, 0, script=script)
    assert done.returncode == 4, done.stdout + done.stderr
    assert settings.read_bytes() == before
    rows = [
        c
        for c in world.calls("rig")
        if c["argv"][:2] == ["queue", "create"] and "orch-lead@hacf" in c["argv"]
    ]
    assert len(rows) == 1
    argv = rows[0]["argv"]
    assert argv[argv.index("--tags") + 1] == "advisor-experiment,guard"
    assert (
        "settings.json" in rows[0]["stdin"]
        and len(re.findall(r"[0-9a-f]{64}", rows[0]["stdin"])) == 2
    )
    assert (world.evidence / "halted.json").is_file()
    assert list(world.evidence.glob("guard/*/guard-tripped.json"))
    assert world.run("gate", "--arm", "0").returncode == 75
    assert json.loads((world.evidence / "spend.json").read_text())["runs"][-1]["status"] == "guard"


def test_guard_quiet_when_unchanged(world: World) -> None:
    """An unchanged run sends no row to the lead; the before-copies are private."""
    assert drive(world, 0).returncode == 0
    assert not any("orch-lead@hacf" in c["argv"] for c in world.calls("rig"))
    copies = list(world.evidence.glob("guard/*/settings.json"))
    assert copies and copies[0].stat().st_mode & 0o777 == 0o600


def test_guard_removes_a_new_settings_file(world: World) -> None:
    """A settings.local.json that appears mid-run is a change; restoring removes it."""
    import run_arm

    home = world.home
    guard = run_arm.Guard(home, world.tmp / "guard")
    guard.before()
    new = home / ".claude" / "settings.local.json"
    new.write_text("{}")
    changed = guard.changed()
    assert [c["file"] for c in changed] == ["settings.local.json"]
    assert guard.restore(changed) and not new.exists()


def test_no_command_token_in_the_kit() -> None:
    """The advisor slash command appears in no kit, test or telemetry file."""
    roots = [REPO_ROOT / "dev" / "experiments", REPO_ROOT / "dev" / "tests" / "dev_experiments"]
    files = [p for root in roots for p in root.rglob("*") if p.is_file()]
    files.append(REPO_ROOT / "dev" / "telemetry" / "collect_metrics.py")
    for path in files:
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        found = COMMAND.search(path.read_text(encoding="utf-8", errors="replace"))
        assert not found, f"{path}: {found}"


@pytest.mark.parametrize(
    ("new", "why"),
    [({"advisor": True}, "advisor activity"), ({"model": "claude-haiku-5-5"}, "ran")],
)
def test_a_replaced_session_is_checked_too(world: World, new: dict, why: str) -> None:
    """A seat's later session counts: its activity and its model are still checked."""
    script = full_script()
    script[1]["replaced"] = [["build.audit", new]]
    script[2]["replaced"] = [["build.audit", {}]]
    done = drive(world, 0, script=script)
    assert done.returncode == 0, done.stdout + done.stderr
    record = last(world)
    assert record["mixed"], record
    assert why in done.stderr


def test_a_replaced_session_keeps_the_cost_known(world: World) -> None:
    """Every session with a turn reports a cost, so the run's total stays known."""
    script = full_script()
    script[1]["replaced"] = [["build.audit", {}]]
    assert drive(world, 0, script=script).returncode == 0
    assert last(world)["usd"] == pytest.approx(1.9)


@pytest.mark.parametrize("arm", [0, 2])
def test_a_scout_under_execute_is_mixed(world: World, arm: int) -> None:
    """The helper never serves execute; if one runs there, the run is mixed."""
    script = full_script()
    script[1]["subagent"] = [["build.execute", "hacf-scout", "claude-haiku-5-5"]]
    done = drive(world, arm, script=script)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "run" in last(world)["mixed"] and "under build.execute" in done.stderr
