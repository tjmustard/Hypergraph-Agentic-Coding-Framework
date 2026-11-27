"""The shipped seat presets (OPR.99.0.14.4): six files, each saying what was tested.

Every run reads a copy of openrig/hacf/ in tmp_path; nothing is written inside the HACF checkout
and no test calls the host's `rig`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
HACF = REPO_ROOT / "openrig" / "hacf"
PRESETS = HACF / "presets"
TOOL = HACF / "bin" / "seat-config.py"
AGENT = "agents/hacf/agent.yaml"
ROLES = ("lead", "architect", "red-team", "resolve", "execute", "audit", "scribe")
SINGLE = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
# MR 1: the exact text; a preset claims only a test that ran.
TESTED = {
    "claude-opus-5-5": (
        "B2 baseline: 20 slices with every seat on claude-opus-5-5 (dev/telemetry/h5-v066.md)"
    ),
    "claude-sonnet-5-5": (
        "partial: H5 v0.6.6, execute held and lead not held on claude-sonnet-5-5 medium "
        "(dev/telemetry/h5-v066.md); the other seats not tested on HACF"
    ),
    "claude-haiku-5-5": (
        "partial: H5 v0.6.6, scribe held on claude-haiku-5-5 (dev/telemetry/h5-v066.md); the "
        "other seats not tested on HACF"
    ),
    "tiered": "H5 v0.6.6 (dev/telemetry/h5-v066.md)",
    "codex": "not tested on HACF",
    "agy": (
        "stub smoke only (compat harness step, OPR.99.0.13.1); no live run; harness not "
        "appliable in v0.6.7 (R161)"
    ),
}
NAMES = sorted(TESTED)


def load(name: str) -> dict:
    """One shipped preset, parsed."""
    return yaml.safe_load((PRESETS / f"{name}.yml").read_text())


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    """The kit files seat-config.py reads, copied out of openrig/hacf/."""
    for rel in ("roles.yml", "caps.yml", "seat-config.yml", AGENT):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(HACF / rel, tmp_path / rel)
    shutil.copytree(PRESETS, tmp_path / "presets")
    return tmp_path


def select(kit: Path, name: str) -> None:
    """Name a preset in the copy's seat-config.yml."""
    path = kit / "seat-config.yml"
    text = path.read_text()
    assert "\npreset: null\n" in text
    path.write_text(text.replace("\npreset: null\n", f"\npreset: {name}\n"))


def tool(directory: Path, *args: str) -> subprocess.CompletedProcess:
    """Run seat-config.py on a directory."""
    return subprocess.run(
        [sys.executable, str(TOOL), *args, "--dir", str(directory)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def resolved(directory: Path) -> dict:
    """Return the resolved seats of a directory."""
    proc = tool(directory, "resolve", "--json")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["seats"]


# MR 1


def test_the_directory_holds_exactly_the_six_presets() -> None:
    """No more and no fewer."""
    assert sorted(p.name for p in PRESETS.iterdir()) == [f"{n}.yml" for n in NAMES]
    assert len(NAMES) == 6


@pytest.mark.parametrize("name", NAMES)
def test_each_preset_states_exactly_what_was_tested(name: str) -> None:
    """v2, the exact tested line, all seven roles, and a header comment."""
    doc = load(name)
    assert sorted(doc) == ["schema", "seats", "tested"]
    assert doc["schema"] == "hacf.seat-preset/v2"
    assert doc["tested"] == TESTED[name]
    assert sorted(doc["seats"]) == sorted(ROLES)
    text = (PRESETS / f"{name}.yml").read_text()
    header = " ".join(ln.lstrip("# ") for ln in text.splitlines() if ln.startswith("#"))
    assert text.startswith("#") and "overrides.yml" in header and "still wins" in header


@pytest.mark.parametrize("name", SINGLE)
def test_a_single_model_preset_sets_only_its_model(name: str) -> None:
    """Every seat on the file's model and nothing else, and the header says effort still applies."""
    assert load(name)["seats"] == {role: {"model": name} for role in ROLES}
    text = (PRESETS / f"{name}.yml").read_text()
    header = " ".join(ln.lstrip("# ") for ln in text.splitlines() if ln.startswith("#"))
    assert "effort" in header and "execute: medium" in header


def test_tiered_equals_the_shipped_defaults() -> None:
    """Key for key."""
    defaults = yaml.safe_load((HACF / "seat-config.yml").read_text())
    assert load("tiered")["seats"] == defaults["seats"]


@pytest.mark.parametrize("name", ["codex", "agy"])
def test_a_harness_preset_sets_only_the_harness(name: str) -> None:
    """No model: none was tested."""
    assert load(name)["seats"] == {role: {"harness": name} for role in ROLES}


# MR 2


@pytest.mark.parametrize("tested", [None, '""', "3"])
def test_a_preset_without_a_tested_line_is_refused(kit: Path, tested: str | None) -> None:
    """The same preset validates with the line and exits 1 without it."""
    select(kit, "tiered")
    assert tool(kit, "validate").returncode == 0
    path = kit / "presets" / "tiered.yml"
    lines = path.read_text().splitlines(keepends=True)
    (index,) = [i for i, ln in enumerate(lines) if ln.startswith("tested:")]
    lines[index : index + 1] = [] if tested is None else [f"tested: {tested}\n"]
    path.write_text("".join(lines))
    proc = tool(kit, "validate")
    assert proc.returncode == 1
    assert "presets/tiered.yml: tested: a preset must state what was tested" in proc.stderr
    assert tool(kit, "resolve", "--json").returncode == 1


# MR 3


@pytest.mark.parametrize("name", NAMES)
def test_each_shipped_preset_validates_and_resolves(kit: Path, name: str) -> None:
    """Every key the preset sets resolves to its value with source preset:<name>."""
    select(kit, name)
    proc = tool(kit, "validate")
    assert proc.returncode == 0, proc.stderr
    seats = resolved(kit)
    checked = 0
    for role, entry in load(name)["seats"].items():
        for key, value in entry.items():
            cell = seats[role][key]
            assert (cell["value"], cell["source"]) == (value, f"preset:{name}"), (role, key)
            checked += 1
    assert checked >= len(ROLES)


def test_agy_is_deferred_and_codex_is_not(kit: Path) -> None:
    """R161: the deferral text of slice 02 for agy only."""
    select(kit, "agy")
    for role, row in resolved(kit).items():
        assert row["harness"]["deferred"] == (
            f"harness agy for {role}: deferred in v0.6.7 (OpenRig 0.7.23 has no per-seat path "
            "for a community harness); not applied"
        )
    path = kit / "seat-config.yml"
    path.write_text(path.read_text().replace("\npreset: agy\n", "\npreset: codex\n"))
    for row in resolved(kit).values():
        assert row["harness"] == {"value": "codex", "source": "preset:codex"}


def test_an_override_beats_a_shipped_preset(kit: Path) -> None:
    """The precedence is default < preset < override."""
    select(kit, "claude-sonnet-5-5")
    (kit / "overrides.yml").write_text("seats:\n  lead: {model: claude-opus-5-5}\n")
    seats = resolved(kit)
    assert seats["lead"]["model"] == {"value": "claude-opus-5-5", "source": "override"}
    assert seats["audit"]["model"] == {
        "value": "claude-sonnet-5-5",
        "source": "preset:claude-sonnet-5-5",
    }
    assert seats["execute"]["effort"] == {"value": "medium", "source": "default"}


# MR 4


def test_shipped_presets_name_only_hacf_shipped_skills() -> None:
    """Every skills.add item is a resources.skills id; all six files are read."""
    agent = yaml.safe_load((HACF / AGENT).read_text())
    shipped = {item["id"] for item in agent["resources"]["skills"]}
    assert shipped
    files = sorted(PRESETS.glob("*.yml"))
    assert len(files) == 6
    for path in files:
        for role, entry in yaml.safe_load(path.read_text())["seats"].items():
            added = (entry.get("skills") or {}).get("add", [])
            assert set(added) <= shipped, (path.name, role)
