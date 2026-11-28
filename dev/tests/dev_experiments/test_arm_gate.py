"""The P54 start gate (R214): every condition alone makes it wait, with its reason."""

from __future__ import annotations

import json

import pytest

from .conftest import World, stamp


def gate(world: World, arm: int = 0) -> tuple[int, dict]:
    """Run `gate --json` and return its exit and reading."""
    done = world.run("gate", "--arm", str(arm), "--json")
    return done.returncode, json.loads(done.stdout)


def test_go(world: World) -> None:
    """Everything at rest and enough headroom: exit 0, and nothing changes on the host."""
    code, reading = gate(world)
    assert (code, reading["go"], reading["reasons"]) == (0, True, []), reading
    assert {c["argv"][0] for c in world.calls("rig")} <= {"provider", "usage"}


def waits(world: World, needle: str, arm: int = 0) -> dict:
    """Assert exit 75 with the needle among the reasons."""
    code, reading = gate(world, arm)
    assert code == 75 and reading["go"] is False
    assert any(needle in reason for reason in reading["reasons"]), reading["reasons"]
    return reading


@pytest.mark.parametrize("status", ["executing", "executed", "audit_failed", "blocked", "odd"])
def test_slice_in_flight_in_a_mapped_project(world: World, status: str) -> None:
    """A build slice in flight in a mapped project makes the gate wait."""
    World.slice(world.project, "m/slices/02-two", status)
    waits(world, "in flight")


def test_slice_in_flight_in_the_main_checkout(world: World) -> None:
    """So does one in the HACF checkout."""
    World.slice(world.main, "v068-host-ops/slices/11-live-trial", "executing")
    waits(world, "in flight")


def test_the_run_slice_alone_is_excluded(world: World) -> None:
    """Slice 13 executing alone is go; another mission's dir of that name is not excluded."""
    assert gate(world)[0] == 0
    World.slice(world.project, "v068-host-ops/slices/13-advisor-run", "executing")
    waits(world, "in flight")


def test_unreadable_projects_wait(world: World) -> None:
    """No mapped project, or a missing one, is unknown."""
    config = world.home / ".config" / "hacf" / "host.yml"
    config.write_text("projects:\n  some-rig: /nonexistent-root\n")
    waits(world, "unknown")
    config.write_text("projects: {}\n")
    waits(world, "no mapped project")


def test_headroom_short(world: World) -> None:
    """Rate 12 % per 24 h x 10 h = 5; arm 0 needs 8.70/1.70 x 1.15 = 5.89 %."""
    series = world.state()["series"]
    for row, used in zip(series, (77, 83, 89)):
        row["windowUsedPercent"] = used
    world.set(weekly=89, series=series)
    assert gate(world)[0] == 0
    for row in series:
        row["windowUsedPercent"] += 1
    world.set(weekly=90, series=series)
    waits(world, "headroom")


def test_rate_counts_only_since_a_drop(world: World) -> None:
    """After a mid-window drop only the later samples count; under 6 h of them is unknown."""
    series = world.state()["series"]
    series[0]["windowUsedPercent"] = 94
    series[1]["sampledAt"] = stamp(-3)
    world.set(series=series)
    waits(world, "under 6 h")


def test_five_hour(world: World) -> None:
    """The five-hour window over 50 % waits; 50 % goes."""
    world.set(five_hour=50)
    assert gate(world)[0] == 0
    world.set(five_hour=51)
    waits(world, "five_hour")


@pytest.mark.parametrize("window", ["weekly", "five_hour"])
def test_missing_signal(world: World, window: str) -> None:
    """A missing signal is unknown, so wait."""
    world.set(**{window: None})
    waits(world, f"{window}: unknown")


def test_missing_series(world: World) -> None:
    """No samples is an unknown rate."""
    world.set(series=[])
    waits(world, "rate: unknown")


def test_halted(world: World) -> None:
    """halted.json holds every arm until the lead removes it."""
    world.evidence.mkdir(parents=True)
    (world.evidence / "halted.json").write_text("{}")
    waits(world, "halted")


@pytest.mark.parametrize(("spent", "go"), [(79.9, True), (80.1, False), (None, False)])
def test_cap(world: World, spent: float | None, go: bool) -> None:
    """Spent + 8.70 x 1.15 = 10.0 must fit in $90; an unknown past cost waits."""
    world.evidence.mkdir(parents=True)
    run = {"id": "r", "kind": "arm", "arm": 0, "run": 1, "usd": spent}
    (world.evidence / "spend.json").write_text(json.dumps({"runs": [run]}))
    if go:
        # a measured cost above the table raises the estimate, so keep this run on another arm
        assert gate(world, arm=2)[0] in (0, 75)
        run["arm"] = 3
        (world.evidence / "spend.json").write_text(json.dumps({"runs": [run]}))
        assert gate(world)[0] == 0
    else:
        waits(world, "cap")


def test_cap_stops_a_run_before_anything(world: World) -> None:
    """`run` over the cap exits 75 and prepares nothing."""
    done = world.run("run", "--arm", "0", "--run", "1", "--cap-usd", "5")
    assert done.returncode == 75 and "cap" in done.stdout + done.stderr
    assert world.calls("test_rig") == [] and not (world.evidence / "runs").exists()
