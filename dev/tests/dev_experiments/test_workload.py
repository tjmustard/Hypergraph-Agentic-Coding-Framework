"""The workload: it lints in a scratch install, and its oracles pass on seed plus a stub."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import run_arm

from .conftest import KIT, World
from .test_replay_report import fake_sealed


@pytest.fixture(scope="module")
def installed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The workload prepared as an arm prepares it (a real local install of this checkout)."""
    scratch = tmp_path_factory.mktemp("prepared")
    return run_arm.prepare_project(scratch, arm=0)


def test_lint(installed: Path) -> None:
    """Both slices pass the lint at confidence 9."""
    done = subprocess.run(
        [sys.executable, "-B", ".agents/scripts/hyper_spec.py", "lint"]
        + ["--mission", "missions/advisor-workload", "--min-confidence", "9"],
        cwd=installed,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def oracle_commands(project: Path, slice_dir: str) -> list[list[str]]:
    """The slice's declared oracle."""
    import yaml

    spec = project / "missions/advisor-workload/slices" / slice_dir / "SPEC.md"
    return yaml.safe_load(spec.read_text().split("---\n")[1])["hacf_oracle"]


@pytest.mark.parametrize("slice_dir", ["01-small", "02-medium"])
def test_oracles(installed: Path, tmp_path: Path, slice_dir: str) -> None:
    """On the seed plus the fake reference, each oracle passes in under 30 s.

    `uv run --locked` is replaced by this interpreter: the test's cache is a throwaway.
    """
    tree = tmp_path / "tree"
    shutil.copytree(installed, tree)
    world = World(tmp_path / "w")
    fake_sealed(world.sealed)
    stub = (world.sealed / "reference.patch").read_text().replace("fake_report", "report_stub")
    patch = tmp_path / "reference.patch"
    patch.write_text(stub)
    subprocess.run(["git", "apply", str(patch)], cwd=tree, check=True)
    with (tree / "stockroom/report.py").open("a") as out:
        out.write("\n\ndef low_stock(items):\n    '''Stub.'''\n")
    for argv in oracle_commands(tree, slice_dir):
        assert argv[:3] == ["uv", "run", "--locked"]
        words = argv[3:]
        command = [sys.executable, "-B"]
        command += (
            ["-m", "pytest", "-q", "-p", "no:cacheprovider"] if words[0] == "pytest" else words[1:]
        )
        done = subprocess.run(command, cwd=tree, capture_output=True, text=True, timeout=30)
        assert done.returncode == 0, done.stdout + done.stderr


def test_seed_is_small_and_stdlib_only() -> None:
    """The seed's package is about 150-300 lines and has no runtime dependency."""
    modules = list((KIT / "workload/seed/stockroom").glob("*.py"))
    assert 150 <= sum(len(m.read_text().splitlines()) for m in modules) <= 300
    assert "dependencies = []" in (KIT / "workload/seed/pyproject.toml").read_text()


def test_helper_files() -> None:
    """The scout is exactly D4's, and the guidance is one paragraph."""
    scout = (KIT / "hacf-scout.md").read_text()
    front = scout.split("---\n")[1]
    assert re.findall(r"(?m)^(\w+):", front) == ["name", "description", "model", "tools"]
    assert "model: haiku\n" in front and "tools: Read, Grep, Glob\n" in front
    guidance = (KIT / "scout-guidance.md").read_text()
    assert guidance.startswith("Search helper:") and len(guidance.strip().splitlines()) == 1
