"""Shared fixtures of the progress report tests: the in-process tool and one run of it.

This directory is a package, so this module does not shadow the rootdir `conftest`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from . import progress_fixture as fx


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix the clock and give every run a throwaway HOME and git config."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SOURCE_DATE_EPOCH", fx.EPOCH)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def tool():
    """The tool, imported in-process."""
    return fx.load_tool()


@pytest.fixture
def run(tool, tmp_path: Path):
    """Run the tool on one built project and return its Report; the run must exit 0."""

    def go(omit: str | None = None, *extra: str) -> fx.Report:
        root = fx.build(tmp_path, omit)
        out = tmp_path / "out"
        out.mkdir()
        code = tool.main(["--project", str(root), "--out-dir", str(out), *extra])
        assert code == 0
        return fx.Report(out)

    return go
