"""The code-graph adapter chooses only a codebase-memory-mcp that reports the pinned version.

OPR.99.0.6.5. Every candidate here is a fake: a tiny shell script in the test's temp directory
that logs its argv and prints a version (or fails). PATH is replaced with a directory holding git
and the fakes only, so no real codebase-memory-mcp or uvx can ever be found or started. This file
reads no recorded fixture.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import hyper_codegraph
import pytest
from conftest import REPO_ROOT

CBM = hyper_codegraph.BACKEND_CBM
EXE = hyper_codegraph.EXE_NAME
PINNED = hyper_codegraph.PINNED_VERSION
UVX = ["uvx", f"{EXE}@{PINNED}"]
DEFAULTS = REPO_ROOT / hyper_codegraph.DEFAULTS_REL


def _fake(path: Path, version: str | None) -> Path:
    """Write an executable that logs its argv to <path>.log and prints version (None: exit 1)."""
    body = f'echo "$*" >> "{path}.log"\n'
    body += f"echo '{EXE} {version}'\n" if version is not None else "exit 1\n"
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _calls(path: Path) -> list[str]:
    """Return each argv line the fake at path logged."""
    log = Path(f"{path}.log")
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@pytest.fixture
def bindir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A PATH directory holding git only; tests add fakes to it."""
    directory = tmp_path / "bin"
    directory.mkdir()
    git = shutil.which("git")
    assert git is not None
    (directory / "git").symlink_to(git)
    monkeypatch.setenv("PATH", str(directory))
    return directory


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A project with the shipped defaults; hacf.yml is written per test."""
    project = tmp_path / "project"
    (project / ".agents" / "config").mkdir(parents=True)
    (project / ".agents" / "local").mkdir()
    shutil.copyfile(DEFAULTS, project / hyper_codegraph.DEFAULTS_REL)
    return project


def _config(root: Path, install_method: str | None = None, recorded: list[str] | None = None):
    lines = ["code_graph:", f"  backend: {CBM}"]
    if install_method is not None:
        lines.append(f"  install_method: {install_method}")
    (root / hyper_codegraph.LOCAL_REL).write_text("\n".join(lines) + "\n", encoding="utf-8")
    state = root / hyper_codegraph.STATE_REL
    if recorded is not None:
        hyper_codegraph.write_state(root, {"exe_argv_prefix": recorded})
    elif state.exists():
        state.unlink()
    return hyper_codegraph.load_config(root)


def _pin_line(found: str) -> str:
    return (
        f"{EXE} {PINNED} is required; found: {found}. Install it with: uv tool install "
        f"{EXE}=={PINNED}, or record consent for uvx (install_method: uvx)."
    )


def test_recorded_prefix_at_pin_is_chosen(root: Path, bindir: Path, tmp_path: Path) -> None:
    """A recorded prefix reporting 0.12.1 is chosen after one --version probe."""
    recorded = _fake(tmp_path / "recorded-cbm", PINNED)
    prefix, lines = hyper_codegraph.resolution_chain(_config(root, recorded=[str(recorded)]))
    assert prefix == [str(recorded)]
    assert lines == [f"state exe_argv_prefix: {json.dumps([str(recorded)])} (chosen)"]
    assert _calls(recorded) == ["--version"]


def test_recorded_mismatch_skipped_path_at_pin_chosen(
    root: Path, bindir: Path, tmp_path: Path
) -> None:
    """A recorded 0.12.2 is skipped with its version named; a 0.12.1 on PATH is chosen."""
    recorded = _fake(tmp_path / "recorded-cbm", "0.12.2")
    on_path = _fake(bindir / EXE, PINNED)
    prefix, lines = hyper_codegraph.resolution_chain(_config(root, recorded=[str(recorded)]))
    assert prefix == [str(on_path)]
    assert lines == [
        f"state exe_argv_prefix {json.dumps([str(recorded)])}: version 0.12.2, not {PINNED} "
        "(skipped)",
        f"PATH {EXE}: {on_path} (chosen)",
    ]


def test_path_mismatch_without_consent_unavailable(
    root: Path, bindir: Path, tmp_path: Path
) -> None:
    """A 0.12.2 on PATH and no consent: unavailable, naming the version found and the fix."""
    on_path = _fake(bindir / EXE, "0.12.2")
    prefix, lines = hyper_codegraph.resolution_chain(_config(root))
    assert prefix is None
    assert lines == [
        "state exe_argv_prefix: not recorded",
        f"PATH {EXE} {on_path}: version 0.12.2, not {PINNED} (skipped)",
        "uvx: no recorded consent (hacf.yml code_graph.install_method is not uvx)",
    ]
    result = hyper_codegraph.run_capability("list_projects", repo_root=root)
    assert result["status"] == "unavailable"
    assert result["error"] == (
        f"{EXE} is not installed or not on PATH\n" + _pin_line(f"PATH {EXE} {on_path}: 0.12.2")
    )
    assert hyper_codegraph.fallback_line("unavailable") in result["warnings"]
    report = hyper_codegraph.doctor(root)
    assert report["prefix"] is None
    assert _pin_line(f"PATH {EXE} {on_path}: 0.12.2") in report["warnings"]


def test_nothing_found_unavailable_names_none(root: Path, bindir: Path) -> None:
    """No candidate at all: the pin line says found: none."""
    result = hyper_codegraph.run_capability("list_projects", repo_root=_config(root).repo_root)
    assert result["status"] == "unavailable"
    assert result["error"].endswith("\n" + _pin_line("none"))


def test_path_mismatch_with_consent_uvx_not_run(root: Path, bindir: Path, tmp_path: Path) -> None:
    """With consent a mismatched PATH binary gives way to the uvx prefix, which is never run."""
    _fake(bindir / EXE, "0.12.2")
    uvx = _fake(bindir / "uvx", PINNED)
    prefix, lines = hyper_codegraph.resolution_chain(_config(root, install_method="uvx"))
    assert prefix == UVX
    consent = "uvx with recorded consent (install_method uvx)"
    assert lines[-1] == f"{consent}: {json.dumps(UVX)} (chosen)"
    assert _calls(uvx) == []  # resolution started no uvx process, so nothing downloaded


def test_recorded_uvx_prefix_not_probed(root: Path, bindir: Path) -> None:
    """A recorded prefix equal to the uvx argv is the uvx candidate: chosen, never run."""
    uvx = _fake(bindir / "uvx", "9.9.9")
    prefix, lines = hyper_codegraph.resolution_chain(_config(root, recorded=UVX))
    assert prefix == UVX
    assert lines == [f"state exe_argv_prefix: {json.dumps(UVX)} (chosen)"]
    assert _calls(uvx) == []


def test_version_failure_is_unknown_and_skipped(root: Path, bindir: Path, tmp_path: Path) -> None:
    """A --version that fails reports unknown and the candidate is skipped."""
    broken = _fake(tmp_path / "broken-cbm", None)
    on_path = _fake(bindir / EXE, None)
    prefix, lines = hyper_codegraph.resolution_chain(_config(root, recorded=[str(broken)]))
    assert prefix is None
    assert lines[:2] == [
        f"state exe_argv_prefix {json.dumps([str(broken)])}: version unknown, not {PINNED} "
        "(skipped)",
        f"PATH {EXE} {on_path}: version unknown, not {PINNED} (skipped)",
    ]
    assert _calls(broken) == ["--version"] and _calls(on_path) == ["--version"]


def test_project_name_length_refusal() -> None:
    """Over 128 characters: the refusal names the cause and the workaround; PROJECT_RE unchanged."""
    assert hyper_codegraph.PROJECT_RE.pattern == r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
    assert hyper_codegraph.validate_placeholders({"project": "p" * 128}) == {"project": "p" * 128}
    with pytest.raises(hyper_codegraph.PlaceholderRefused) as long_name:
        hyper_codegraph.validate_placeholders({"project": "p" * 129})
    assert str(long_name.value) == (
        "refused: project name is 129 characters; the limit is 128 (it is derived from the "
        "repository path): use a shorter checkout path; nothing was run"
    )
    for bad in ("bad name", "p" * 128 + "!"):  # a bad character keeps today's text
        with pytest.raises(hyper_codegraph.PlaceholderRefused) as invalid:
            hyper_codegraph.validate_placeholders({"project": bad})
        assert str(invalid.value) == (
            f"refused: project value {bad!r} does not match "
            f"^{hyper_codegraph.PROJECT_RE.pattern}$; nothing was run"
        )
