"""Tests of the kit hacf-host.py reads a rig's routing from (OPR.99.0.16.14).

An installed project holds no `openrig/`: its rig's kit is the OpenRig live spec that `rig specs
ls --json` names. The HACF repository holds its kit at `<root>/openrig/hacf`. Every test runs on
the isolated host of conftest.py; the live specs are dirs under tmp_path.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from .test_autopark import ADVISOR, LOW, RIG, Fleet, question, work
from .test_pause_resume import up_result

LISTING = "specs ls --json"
FYI_TO = "scribe-desk@kernel"


@pytest.fixture
def fleet(host, tool, monkeypatch: pytest.MonkeyPatch) -> Fleet:
    """One mapped rig, its question and update rows routed to two seats."""
    fleet = Fleet(host, tool, monkeypatch, (RIG,))
    fleet.routing(RIG, update={"session": FYI_TO})
    return fleet


@pytest.fixture
def two(host, tool, monkeypatch: pytest.MonkeyPatch) -> Fleet:
    """Two mapped rigs: RIG above LOW in priority."""
    return Fleet(host, tool, monkeypatch, (RIG, LOW))


def live_dir(fleet: Fleet, name: str = RIG) -> Path:
    """Where the rig's live spec is."""
    return fleet.host.base / "specs" / name


def live_spec(fleet: Fleet, name: str = RIG) -> Path:
    """A live spec dir holding rig.yaml and no kit script."""
    spec = live_dir(fleet, name)
    spec.mkdir(parents=True, exist_ok=True)
    (spec / "rig.yaml").write_text(f"name: {name}\n")
    return spec


def install(fleet: Fleet, name: str = RIG) -> Path:
    """The installed layout: the kit moves to the live spec and the project has no openrig/."""
    spec = live_spec(fleet, name)
    shutil.move(fleet.roots[name] / "openrig" / "hacf" / "bin", spec / "bin")
    shutil.rmtree(fleet.roots[name] / "openrig")
    return spec


def drop_project_kit(fleet: Fleet, name: str = RIG) -> None:
    """The project holds no kit."""
    shutil.rmtree(fleet.roots[name] / "openrig")


def entry(fleet: Fleet, name: str = RIG, kind: str = "rig", **extra: object) -> dict:
    """One entry of `rig specs ls --json`."""
    path = str(live_dir(fleet, name) / "rig.yaml")
    found = {"kind": kind, "name": name, "sourcePath": path, "resolvedSourcePath": path}
    return {**found, **extra}


def listing(fleet: Fleet, *entries: dict) -> None:
    """Answer `rig specs ls --json`."""
    fleet.rig.reply(LISTING, list(entries))


def kit_dirs_asked(fleet: Fleet) -> set[str]:
    """The --dir of every seat-config.py call."""
    lines = (fleet.host.base / "seat-config.log").read_text().splitlines()
    return {argv[argv.index("--dir") + 1] for argv in map(json.loads, lines)}


def project_kit(fleet: Fleet, name: str = RIG) -> Path:
    """The kit of the HACF repository's layout."""
    return fleet.roots[name] / "openrig" / "hacf"


def failed_restore(fleet: Fleet, capsys) -> None:
    """One tick whose restore of the parked rig fails, which escalates."""
    fleet.parked()
    fleet.set_queue(work())
    argv = f"up {RIG} --existing --json --timeout 900"
    fleet.rig.reply(argv, up_result("failed", f"snap-d-{RIG}"), rc=1)
    assert fleet.tick(capsys) == 1, fleet.output


# ------------------------------------------------------------- (a) the installed layout


def test_an_installed_rig_parks_and_sends_its_fyi_from_the_live_spec(fleet, capsys) -> None:
    """MR 3a: P3 is known, and the park FYI goes to the routed destination."""
    spec = install(fleet)
    listing(fleet, entry(fleet))
    fleet.set_queue(question())
    fleet.hold(capsys)
    assert fleet.record()["state"] == "parked-waiting", fleet.log()
    assert not fleet.logged("P3: unknown")
    (row,) = fleet.creates()
    assert row["--destination"] == FYI_TO
    assert fleet.logged(f"fyi {RIG} sent to {FYI_TO}")
    assert kit_dirs_asked(fleet) == {str(spec)}


def test_an_installed_rig_with_a_row_to_act_on_has_a_known_p3(fleet, capsys) -> None:
    """MR 3a: P3 does not hold, by the rows, not by an unknown."""
    install(fleet)
    listing(fleet, entry(fleet))
    fleet.set_queue(question(), work())
    fleet.hold(capsys)
    assert fleet.mutations() == [] and not fleet.logged("P3: unknown")
    assert fleet.logged("P3: actionable rows for the rig: w-1")


def test_an_installed_rigs_failed_restore_escalates_from_the_live_spec(fleet, capsys) -> None:
    """MR 3a: the escalation reaches the routed destination."""
    spec = install(fleet)
    listing(fleet, entry(fleet))
    failed_restore(fleet, capsys)
    (row,) = fleet.creates()
    assert row["--destination"] == ADVISOR
    assert fleet.logged(f"escalate {RIG} sent to {ADVISOR}")
    assert kit_dirs_asked(fleet) == {str(spec)}


def test_the_live_spec_wins_over_the_project_kit(fleet) -> None:
    """MR 1: (a) before (b), and only one kit is run."""
    spec = live_spec(fleet)
    shutil.copytree(project_kit(fleet) / "bin", spec / "bin")
    listing(fleet, entry(fleet))
    root = str(fleet.roots[RIG])
    assert fleet.tool.kit_dir(RIG, root) == (spec, "")
    fleet.tool.question_routing(RIG, root)
    assert kit_dirs_asked(fleet) == {str(spec)}


# ------------------------------------------------------------- (b) the HACF repository


def test_a_live_spec_without_the_script_leaves_the_project_kit(fleet, capsys) -> None:
    """MR 3b."""
    live_spec(fleet)
    listing(fleet, entry(fleet))
    fleet.set_queue(question())
    fleet.hold(capsys)
    assert fleet.record()["state"] == "parked-waiting", fleet.log()
    assert kit_dirs_asked(fleet) == {str(project_kit(fleet))}


# ------------------------------------------------------------- (c) neither


def no_entry(fleet: Fleet) -> str:
    """No rig spec of that name."""
    listing(fleet, entry(fleet, "other-hacf"))
    spec = fleet.host.base / "openrig-home" / "specs" / RIG
    return f"no rig spec named {RIG} in rig specs ls; specs dir {spec}: not there"


def bare_spec(fleet: Fleet) -> str:
    """A live spec without the script."""
    spec = live_spec(fleet)
    listing(fleet, entry(fleet))
    return f"live spec {spec} has no bin/seat-config.py"


def script_is_a_dir(fleet: Fleet) -> str:
    """bin/seat-config.py is not a regular file."""
    spec = live_spec(fleet)
    (spec / "bin" / "seat-config.py").mkdir(parents=True)
    listing(fleet, entry(fleet))
    return f"live spec {spec} has no bin/seat-config.py"


@pytest.mark.parametrize("break_it", [no_entry, bare_spec, script_is_a_dir])
def test_no_kit_makes_p3_unknown_and_says_what_was_found(fleet, capsys, break_it) -> None:
    """MR 3c: unknown never parks."""
    drop_project_kit(fleet)
    found = break_it(fleet)
    fleet.set_queue(question())
    fleet.hold(capsys)
    assert fleet.mutations() == [] and "eligible_since" not in fleet.record()
    reason = f"no kit for {RIG}: {found}; {project_kit(fleet)} has none"
    assert fleet.logged(f"skip {RIG}: P3: unknown: {reason}"), fleet.log()


def test_no_kit_sends_neither_the_escalation_nor_the_fyi(fleet, capsys) -> None:
    """MR 3c: both are logged `not sent`, and no row is made."""
    drop_project_kit(fleet)
    no_entry(fleet)
    failed_restore(fleet, capsys)
    assert fleet.logged(f"escalate {RIG} not sent: no kit for {RIG}: ")
    fleet.tool._send_park_fyi(RIG, str(fleet.roots[RIG]), 1, "resume.md")
    assert fleet.logged(f"fyi {RIG} not sent: no kit for {RIG}: ")
    assert fleet.creates() == []


# ------------------------------------------------------------- (d) what is not a live spec


def two_rig_entries(fleet: Fleet) -> list[dict]:
    """Two rig specs of one name."""
    return [entry(fleet), entry(fleet)]


def agent_entry(fleet: Fleet) -> list[dict]:
    """Only an agent spec of that name."""
    return [entry(fleet, kind="agent")]


def no_path(fleet: Fleet) -> list[dict]:
    """No path string."""
    return [entry(fleet, sourcePath=None, resolvedSourcePath=None)]


def not_a_rig_yaml(fleet: Fleet) -> list[dict]:
    """A path that names another file."""
    return [entry(fleet, sourcePath=str(live_dir(fleet) / "agent.yaml"))]


def relative_path(fleet: Fleet) -> list[dict]:
    """A relative path, which only the caller's directory could resolve."""
    return [entry(fleet, sourcePath=f"specs/{RIG}/rig.yaml")]


NOT_LIVE = [two_rig_entries, agent_entry, no_path, not_a_rig_yaml, relative_path]


@pytest.mark.parametrize("entries", NOT_LIVE)
@pytest.mark.parametrize("has_project_kit", [True, False])
def test_what_is_not_one_live_spec_falls_through(
    fleet, monkeypatch, entries, has_project_kit: bool
) -> None:
    """MR 3d: the result is the project kit, or none."""
    spec = live_spec(fleet)
    shutil.copytree(project_kit(fleet) / "bin", spec / "bin")
    monkeypatch.chdir(fleet.host.base)
    listing(fleet, *entries(fleet))
    if not has_project_kit:
        drop_project_kit(fleet)
    kit, why = fleet.tool.kit_dir(RIG, str(fleet.roots[RIG]))
    if has_project_kit:
        assert (kit, why) == (project_kit(fleet), "")
    else:
        assert kit is None and why.startswith(f"no kit for {RIG}: "), why


def test_the_resolved_path_serves_when_there_is_no_source_path(fleet) -> None:
    """MR 1a: `sourcePath`, else `resolvedSourcePath`."""
    spec = install(fleet)
    found = entry(fleet)
    del found["sourcePath"]
    listing(fleet, found)
    assert fleet.tool.kit_dir(RIG, str(fleet.roots[RIG])) == (spec, "")


# ------------------------------------------------------------- (e) a failed listing


@pytest.mark.parametrize(
    ("reply", "words"),
    [
        ({"stdout": "", "rc": 1, "stderr": "daemon down"}, "exit 1: daemon down"),
        ({"stdout": "not json"}, "its output is not a JSON list of objects"),
    ],
)
def test_a_failed_listing_still_takes_the_project_kit(fleet, reply: dict, words: str) -> None:
    """MR 3e: (b) still applies, and (c)'s reason names the failure."""
    fleet.rig.reply(LISTING, **reply)
    root = str(fleet.roots[RIG])
    assert fleet.tool.kit_dir(RIG, root) == (project_kit(fleet), "")
    drop_project_kit(fleet)
    kit, why = fleet.tool.kit_dir(RIG, root)
    assert kit is None
    assert why.startswith(f"no kit for {RIG}: rig specs ls failed: {words}"), why


# ------------------------------------------------------------- (f) one listing per tick


def mixed_fleet(two: Fleet) -> None:
    """RIG installed, LOW in the HACF repository's layout."""
    install(two, RIG)
    live_spec(two, LOW)
    listing(two, entry(two, RIG), entry(two, LOW))
    two.set_queue(question(), question("q-2", rig=LOW))


def test_one_dry_run_tick_lists_the_specs_once_for_both_layouts(two, capsys) -> None:
    """MR 3f: one `rig specs ls`, P3 known for both, and a fresh tick lists again."""
    mixed_fleet(two)
    assert two.tick(capsys, "--dry-run") == 0, two.output
    assert two.calls(LISTING) == [LISTING]
    assert two.logged(f"eligible {RIG}:") and two.logged(f"eligible {LOW}:"), two.log()
    assert not two.logged("P3: unknown")
    assert two.tick(capsys, "--dry-run", after=120) == 0, two.output
    assert two.calls(LISTING) == [LISTING] * 2


def test_a_parking_tick_lists_the_specs_once(two, capsys) -> None:
    """MR 1: the judgments, the re-check and the FYI of one tick share one listing."""
    mixed_fleet(two)
    two.hold(capsys)
    ticks = len(two.logged("supervise - tick start"))
    assert two.logged(f"park {LOW} done") and two.logged(f"fyi {LOW} sent to"), two.log()
    assert two.calls(LISTING) == [LISTING] * ticks


def test_a_tick_reads_a_changed_listing(fleet, capsys) -> None:
    """MR 1: nothing is kept from one tick to the next, or after the last."""
    spec = live_spec(fleet)
    shutil.move(project_kit(fleet) / "bin", spec / "bin")
    no_entry(fleet)
    fleet.set_queue(question())
    fleet.tick(capsys)
    assert fleet.logged(f"P3: unknown: no kit for {RIG}"), fleet.log()
    listing(fleet, entry(fleet))
    fleet.tick(capsys, after=120)
    assert fleet.logged(f"eligible {RIG}:"), fleet.log()
    for _ in range(2):  # outside a tick every call reads the listing
        assert fleet.tool.kit_dir(RIG, str(fleet.roots[RIG])) == (spec, "")
    assert fleet.calls(LISTING) == [LISTING] * 4
