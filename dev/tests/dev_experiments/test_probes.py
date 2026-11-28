"""Slice 13's two probes (R215), with fakes."""

from __future__ import annotations

import json

from .conftest import World


def read(world: World, name: str) -> dict:
    """One probe record."""
    return json.loads((world.evidence / name).read_text())


def test_probe_cli(world: World) -> None:
    """One claude call with --advisor, recording active, called, notices, usage and cost."""
    done = world.run("probe", "cli")
    assert done.returncode == 0, done.stdout + done.stderr
    (call,) = world.calls("claude")
    argv = call["argv"]
    assert argv[:3] == ["-p", "--model", "sonnet"] and argv[argv.index("--advisor") + 1] == "opus"
    got = read(world, "probe-cli.json")
    assert (got["active"], got["called"], got["credits_notice"]) == (True, True, False)
    assert got["total_cost_usd"] == 0.05 and got["iterations_kept"] is True
    spend = json.loads((world.evidence / "spend.json").read_text())["runs"]
    assert spend[-1]["kind"] == "probe" and spend[-1]["usd"] == 0.05


def test_probe_cli_credits_notice(world: World) -> None:
    """A credits notice is recorded; the advisor arms then never run."""
    world.set(probe_result="The advisor needs usage credits", probe_called=False)
    assert world.run("probe", "cli").returncode == 0
    got = read(world, "probe-cli.json")
    assert got["credits_notice"] is True and got["called"] is False


def mechanism(world: World, **state) -> dict:
    """Run both probes."""
    state.setdefault("advisor_calls", {"build.execute": True})
    state.setdefault("script", [{"commit": True}])
    world.set(**state)
    assert world.run("probe", "cli").returncode == 0
    done = world.run("probe", "mechanism", HACF_ADVISOR_WAIT_S="3")
    assert done.returncode == 0, done.stdout + done.stderr
    return read(world, "probe-mechanism.json")


def test_probe_mechanism_go(world: World) -> None:
    """Every point passes: go; two seats only."""
    got = mechanism(world)
    assert got["go"] is True, got
    assert all(got["points"].values())
    (call,) = world.calls("test_rig")
    assert call["argv"][call["argv"].index("--seats") + 1] == "build.execute,build.audit"


def test_probe_mechanism_no_go(world: World) -> None:
    """A permission prompt, no advisor call, or no commit: no go, naming the point."""
    got = mechanism(world, script=[{"needs_input": "build.execute"}, {"commit": True}])
    assert got["go"] is False and got["points"]["no_permission_prompt"] is False
    got = mechanism(world, script=[{"commit": True}], advisor_calls={}, needs_input=None)
    assert got["go"] is False and got["points"]["advisor_present_on_execute"] is False
    got = mechanism(world, script=[])
    assert got["go"] is False and got["points"]["task_finished"] is False
