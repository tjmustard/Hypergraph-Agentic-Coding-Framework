"""Tests of the specs-dir fallback and the old-kit reason of hacf-host.py (OPR.99.0.16.17).

A rig `rig specs ls` does not list yet is looked for in OpenRig's specs dir,
`$OPENRIG_HOME/specs/<rig>/` (default `~/.openrig`). A kit older than v0.6.7 has no routing
section and is named as such. Every test runs on the isolated host of conftest.py, whose
OPENRIG_HOME and HOME are under tmp_path.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from .test_autopark import RIG, Fleet, question
from .test_kit_path import (
    FYI_TO,
    LISTING,
    drop_project_kit,
    entry,
    failed_restore,
    kit_dirs_asked,
    listing,
    live_spec,
    project_kit,
)

OLD_KIT_REASON = "kit older than v0.6.7 (no routing section); upgrade the rig's HACF kit"

# A kit from before v0.6.7: its --section has no `routing` choice, so argparse refuses it.
OLD_KIT = """\
import argparse

parser = argparse.ArgumentParser(prog="seat-config.py resolve")
parser.add_argument("verb")
parser.add_argument("--dir")
parser.add_argument("--section", choices=["context", "seats"])
parser.add_argument("--json", action="store_true")
parser.parse_args()
"""


@pytest.fixture
def fleet(host, tool, monkeypatch: pytest.MonkeyPatch) -> Fleet:
    """One mapped rig whose library lists no spec, its update rows routed to a second seat."""
    fleet = Fleet(host, tool, monkeypatch, (RIG,))
    fleet.routing(RIG, update={"session": FYI_TO})
    listing(fleet)
    return fleet


def openrig_home(fleet: Fleet) -> Path:
    """The fixture's OPENRIG_HOME."""
    return fleet.host.base / "openrig-home"


def specs_dir(fleet: Fleet, home: Path | None = None) -> Path:
    """Where the rig's spec is in an OpenRig home."""
    return (home or openrig_home(fleet)) / "specs" / RIG


def add_specs_dir(fleet: Fleet, home: Path | None = None) -> Path:
    """The rig's spec in the specs dir, with a copy of the project's kit; the project keeps it."""
    spec = specs_dir(fleet, home)
    shutil.copytree(project_kit(fleet) / "bin", spec / "bin")
    (spec / "rig.yaml").write_text(f'version: "0.2"\nname: {RIG}\n')
    return spec


def found(fleet: Fleet) -> tuple[Path | None, str]:
    """`kit_dir` of the rig."""
    return fleet.tool.kit_dir(RIG, str(fleet.roots[RIG]))


def unlisted(fleet: Fleet) -> str:
    """The library's finding when it has no entry."""
    return f"no rig spec named {RIG} in rig specs ls"


# ------------------------------------------------------------- (a) the specs dir


def test_an_unlisted_rig_is_parked_by_the_routing_of_its_specs_dir(fleet, capsys) -> None:
    """MR 5a: P3 is known, and only the specs dir's kit is run."""
    spec = add_specs_dir(fleet)
    drop_project_kit(fleet)
    assert found(fleet) == (spec, "")
    fleet.set_queue(question())
    fleet.hold(capsys)
    assert fleet.record()["state"] == "parked-waiting", fleet.log()
    assert not fleet.logged("P3: unknown")
    (row,) = fleet.creates()
    assert row["--destination"] == FYI_TO
    assert kit_dirs_asked(fleet) == {str(spec)}


def test_the_specs_dir_comes_before_the_project_kit(fleet) -> None:
    """MR 1: step 2 before step 3."""
    spec = add_specs_dir(fleet)
    assert found(fleet) == (spec, "")


def test_no_specs_dir_leaves_the_project_kit(fleet) -> None:
    """MR 1.3."""
    assert found(fleet) == (project_kit(fleet), "")


# ------------------------------------------------------------- (b) the library wins


@pytest.mark.parametrize("has_project_kit", [True, False])
def test_one_library_entry_decides_and_the_specs_dir_is_not_read(
    fleet, has_project_kit: bool
) -> None:
    """MR 5b."""
    live = live_spec(fleet)
    add_specs_dir(fleet)
    listing(fleet, entry(fleet))
    if has_project_kit:
        assert found(fleet) == (project_kit(fleet), "")
    else:
        drop_project_kit(fleet)
        assert found(fleet) == (
            None,
            f"no kit for {RIG}: live spec {live} has no bin/seat-config.py; "
            f"{project_kit(fleet)} has none",
        )


# ------------------------------------------------------------- (c) no fallback


def failed_listing(fleet: Fleet) -> str:
    """`rig specs ls` exits 1."""
    fleet.rig.reply(LISTING, "", rc=1, stderr="daemon down")
    return "rig specs ls failed: exit 1: daemon down"


def two_entries(fleet: Fleet) -> str:
    """Two rig specs of the name."""
    listing(fleet, entry(fleet), entry(fleet))
    return f"2 rig specs named {RIG} in rig specs ls"


def pathless_entry(fleet: Fleet) -> str:
    """One entry that names no rig.yaml."""
    listing(fleet, entry(fleet, sourcePath=None, resolvedSourcePath=None))
    return f"the rig spec {RIG} in rig specs ls names no rig.yaml path"


@pytest.mark.parametrize("library", [failed_listing, two_entries, pathless_entry])
def test_only_a_listing_with_no_entry_allows_the_fallback(fleet, library) -> None:
    """MR 5c: a failed, doubled or unusable listing never reads the specs dir."""
    add_specs_dir(fleet)
    why = library(fleet)
    assert found(fleet) == (project_kit(fleet), "")
    drop_project_kit(fleet)
    assert found(fleet) == (None, f"no kit for {RIG}: {why}; {project_kit(fleet)} has none")


# ------------------------------------------------------------- (d) the guards


def rewrite(spec: Path, text: str) -> None:
    """Replace rig.yaml."""
    (spec / "rig.yaml").write_text(text)


def move_and_link(path: Path) -> None:
    """Replace `path` by a symlink to its moved self."""
    target = path.parents[1] / f"moved-{path.name}"
    path.rename(target)
    path.symlink_to(target)


def other_name(spec: Path) -> str:
    """rig.yaml names another rig."""
    rewrite(spec, "name: x\n")
    return 'rig.yaml name is "x"'


def no_name(spec: Path) -> str:
    """rig.yaml has no name."""
    rewrite(spec, "version: 1\n")
    return "rig.yaml name is null"


def not_a_mapping(spec: Path) -> str:
    """rig.yaml is a list."""
    rewrite(spec, "- name\n")
    return "rig.yaml is not a mapping"


def unparsable(spec: Path) -> str:
    """rig.yaml cannot be parsed."""
    rewrite(spec, "name: [\n")
    return "rig.yaml is not valid YAML"


def impossible_date(spec: Path) -> str:
    """PyYAML raises a ValueError here, not a YAMLError."""
    rewrite(spec, "made: 2026-02-30\n")
    return "rig.yaml is not valid YAML"


def not_utf8(spec: Path) -> str:
    """rig.yaml is not text."""
    (spec / "rig.yaml").write_bytes(b"name: \xff\n")
    return "rig.yaml is not valid YAML"


def too_big(spec: Path) -> str:
    """rig.yaml is over the limit, with the right name at its start."""
    rewrite(spec, f"name: {RIG}\n#" + "x" * (1024 * 1024))
    return "rig.yaml is over 1048576 bytes"


def linked_dir(spec: Path) -> str:
    """The dir is a symlink."""
    move_and_link(spec)
    return "not a real dir"


def a_file_for_the_dir(spec: Path) -> str:
    """A file stands where the dir should."""
    shutil.rmtree(spec)
    spec.write_text("")
    return "not a real dir"


def linked_yaml(spec: Path) -> str:
    """rig.yaml is a symlink."""
    move_and_link(spec / "rig.yaml")
    return "rig.yaml is not a regular file"


def no_yaml(spec: Path) -> str:
    """rig.yaml is missing."""
    (spec / "rig.yaml").unlink()
    return "rig.yaml is not there"


def fifo_yaml(spec: Path) -> str:
    """rig.yaml is a fifo, which a read would block on."""
    (spec / "rig.yaml").unlink()
    os.mkfifo(spec / "rig.yaml")
    return "rig.yaml is not a regular file"


def linked_script(spec: Path) -> str:
    """The script is a symlink."""
    move_and_link(spec / "bin" / "seat-config.py")
    return "bin/seat-config.py is not a regular file"


def no_script(spec: Path) -> str:
    """The script is missing."""
    (spec / "bin" / "seat-config.py").unlink()
    return "bin/seat-config.py is not there"


def linked_bin(spec: Path) -> str:
    """A linked bin/ would run a script from outside the dir."""
    move_and_link(spec / "bin")
    return "bin is not a real dir"


GUARDS = [
    other_name,
    no_name,
    not_a_mapping,
    unparsable,
    impossible_date,
    not_utf8,
    too_big,
    linked_dir,
    a_file_for_the_dir,
    linked_yaml,
    no_yaml,
    fifo_yaml,
    linked_script,
    no_script,
    linked_bin,
]


@pytest.mark.parametrize("break_it", GUARDS)
def test_a_failed_guard_refuses_the_specs_dir_and_names_the_check(fleet, break_it) -> None:
    """MR 5d: no kit, with the check that failed."""
    spec = add_specs_dir(fleet)
    drop_project_kit(fleet)
    failed = break_it(spec)
    assert found(fleet) == (
        None,
        f"no kit for {RIG}: {unlisted(fleet)}; specs dir {spec}: {failed}; "
        f"{project_kit(fleet)} has none",
    )


def test_a_refused_specs_dir_still_leaves_the_project_kit(fleet) -> None:
    """MR 1.3: step 3 follows a refused step 2."""
    other_name(add_specs_dir(fleet))
    assert found(fleet) == (project_kit(fleet), "")


def test_no_specs_dir_and_no_project_kit_says_both(fleet) -> None:
    """MR 1.4."""
    drop_project_kit(fleet)
    assert found(fleet) == (
        None,
        f"no kit for {RIG}: {unlisted(fleet)}; specs dir {specs_dir(fleet)}: not there; "
        f"{project_kit(fleet)} has none",
    )


@pytest.mark.parametrize("owned", ["", "rig.yaml", "bin", os.path.join("bin", "seat-config.py")])
def test_another_users_file_refuses_the_specs_dir(fleet, monkeypatch, owned: str) -> None:
    """MR 1.2: the dir and the files in it belong to this uid."""
    spec = add_specs_dir(fleet)
    drop_project_kit(fleet)
    target = str(spec / owned) if owned else str(spec)
    real = os.lstat

    def foreign(path, *args, **kwargs):
        result = real(path, *args, **kwargs)
        if str(path) != target:
            return result
        fields = list(result)
        fields[4] = os.getuid() + 1  # st_uid
        return os.stat_result(fields)

    monkeypatch.setattr(fleet.tool.os, "lstat", foreign)
    kit, why = found(fleet)
    name = f"{owned} is " if owned else ""
    assert kit is None and f"specs dir {spec}: {name}not owned by this user; " in why, why


@pytest.mark.parametrize("name", ["../x", "a/b", ".", "..", "-x", "x.yaml", ""])
def test_a_name_the_name_rule_refuses_is_never_joined(fleet, name: str) -> None:
    """MR 5d: the rig name rule, so a name cannot leave the specs dir."""
    spec = openrig_home(fleet) / "specs" / "x.yaml"
    shutil.copytree(project_kit(fleet) / "bin", spec / "bin")
    (spec / "rig.yaml").write_text("name: x.yaml\n")
    drop_project_kit(fleet)
    kit, why = fleet.tool.kit_dir(name, str(fleet.roots[RIG]))
    assert kit is None
    assert f"; specs dir not read: {name!r} is not a rig name; " in why, why


# ------------------------------------------------------------- (e) the home


@pytest.mark.parametrize("value", [None, ""])
def test_without_openrig_home_the_specs_dir_is_under_home(
    fleet, monkeypatch, value: str | None
) -> None:
    """MR 5e: unset and empty both mean ~/.openrig."""
    if value is None:
        monkeypatch.delenv("OPENRIG_HOME")
    else:
        monkeypatch.setenv("OPENRIG_HOME", value)
    spec = add_specs_dir(fleet, fleet.host.base / "home" / ".openrig")
    assert found(fleet) == (spec, "")


def test_every_test_has_an_openrig_home_of_its_own(host) -> None:
    """MR 5: no host_ops test resolves the real OpenRig home."""
    assert os.environ["OPENRIG_HOME"] == str(host.base / "openrig-home")
    assert os.environ["HOME"] == str(host.base / "home")


# ------------------------------------------------------------- (f) an old kit


def old_kit(fleet: Fleet) -> Path:
    """The rig's specs dir with a kit from before v0.6.7."""
    spec = add_specs_dir(fleet)
    (spec / "bin" / "seat-config.py").write_text(OLD_KIT)
    drop_project_kit(fleet)
    return spec


def test_an_old_kit_is_named_in_p3_and_never_parks(fleet, capsys) -> None:
    """MR 5f."""
    old_kit(fleet)
    fleet.set_queue(question())
    fleet.hold(capsys)
    assert fleet.mutations() == [] and "eligible_since" not in fleet.record()
    assert fleet.logged(f"skip {RIG}: P3: unknown: {OLD_KIT_REASON}"), fleet.log()


def test_an_old_kit_is_named_when_neither_row_is_sent(fleet, capsys) -> None:
    """MR 5f: the escalation and the park FYI."""
    old_kit(fleet)
    failed_restore(fleet, capsys)
    assert fleet.logged(f"escalate {RIG} not sent: {OLD_KIT_REASON}"), fleet.log()
    fleet.tool._send_park_fyi(RIG, str(fleet.roots[RIG]), 1, "resume.md")
    assert fleet.logged(f"fyi {RIG} not sent: {OLD_KIT_REASON}"), fleet.log()
    assert fleet.creates() == []


# ------------------------------------------------------------- (g) other failures


@pytest.mark.parametrize(
    ("code", "line"),
    [
        (2, "error: argument --section: invalid choice: 'seats'"),
        (2, "error: unrecognized arguments: --json"),
        (1, "error: argument --section: invalid choice: 'routing'"),
    ],
)
def test_another_failure_keeps_the_seat_config_text(fleet, code: int, line: str) -> None:
    """MR 5g: only argparse's exit 2 over `routing` means an old kit."""
    spec = add_specs_dir(fleet)
    (spec / "bin" / "seat-config.py").write_text(
        f"import sys\nsys.stderr.write({line + chr(10)!r})\nsys.exit({code})\n"
    )
    with pytest.raises(fleet.tool.Refused) as error:
        fleet.tool.question_routing(RIG, str(fleet.roots[RIG]))
    assert str(error.value) == f"seat-config.py: exit {code}: {line}"


def test_a_tick_that_falls_back_runs_no_other_rig_verb(fleet, capsys) -> None:
    """MR 1: the fallback only reads: one `rig specs ls`, never a sync."""
    add_specs_dir(fleet)
    fleet.set_queue(question())
    assert fleet.tick(capsys, "--dry-run") == 0, fleet.output
    assert fleet.calls("specs ") == [LISTING]
    assert kit_dirs_asked(fleet) == {str(specs_dir(fleet))}
