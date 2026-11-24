"""openrig/hacf/bin/brief.sh resolves its own rig and takes every run fact from its run record.

Mission release-0.6.1 R8 / US-005. No network and no real `rig`: a fake `rig` on a temp PATH
answers `whoami --json`, and HOME points at a temp dir that holds the run record.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BRIEF = REPO_ROOT / "openrig" / "hacf" / "bin" / "brief.sh"
SLICE = "missions/m1/slices/01-first/SPEC.md"
OTHER_RIG = "other-rig"  # a stand-in for another team's rig name


def _fake_rig(bin_dir: Path, rig_name: str | None, exit_code: int = 0) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    payload = '{"identity":{"rigName":"%s"}}' % rig_name if rig_name else "{}"
    script = bin_dir / "rig"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "whoami --json" ]; then\n'
        f"  echo '{payload}'\n"
        f"  exit {exit_code}\n"
        "fi\n"
        "exit 1\n"
    )
    script.chmod(0o755)


def _setup(tmp_path: Path, rig_name: str, record_lines: list[str], *, rig_exit: int = 0):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / "missions/m1/slices/01-first").mkdir(parents=True)
    (project / SLICE).write_text("# slice\n")
    (project / "spec/compiled").mkdir(parents=True)
    (project / "spec/compiled/MiniPRD_07_Legacy.md").write_text("# legacy\n")
    record = home / ".hacf" / rig_name / "current-run.md"
    record.parent.mkdir(parents=True)
    text = "\n".join(line.replace("{project}", str(project)) for line in record_lines) + "\n"
    record.write_text(text)
    bin_dir = tmp_path / "bin"
    _fake_rig(bin_dir, rig_name, rig_exit)
    env = {
        "HOME": str(home),
        # the fake rig first; system dirs only for bash, sed, awk and python3
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
        "LC_ALL": "C",
    }
    return project, env


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(BRIEF), *args], env=env, capture_output=True, text=True, timeout=30
    )


BASE_RECORD = [
    "# Current run",
    "- Project root: {project}",
    "- Rig spec: /specs/rig-copy",
    "- Feature: demo",
    "- Phase: 2.execute",
    "- Base: 1111111",
    "- Branch: feature/demo",
]


def test_brief_is_executable() -> None:
    """The shipped script has the owner execute bit."""
    assert BRIEF.stat().st_mode & stat.S_IXUSR


@pytest.mark.parametrize("kind", ["execute", "audit"])
def test_case_a_other_rig_names_only_itself(tmp_path: Path, kind: str) -> None:
    """Case A: another rig's brief names only that rig, its record and its project."""
    project, env = _setup(tmp_path, OTHER_RIG, BASE_RECORD)
    result = _run(env, kind, SLICE, "OPR.1.1", "2222222")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert f"~/.hacf/{OTHER_RIG}/current-run.md" in out
    assert f"orch-lead@{OTHER_RIG}" in out
    assert str(project) in out
    assert "/specs/rig-copy/startup/context.md" in out
    assert f"/specs/rig-copy/agents/hacf/guidance/{kind}.md" in out
    assert "none recorded" in out
    # every ~/.hacf/ path and every address belongs to this rig
    for i, chunk in enumerate(out.split("~/.hacf/")[1:]):
        assert chunk.startswith(f"{OTHER_RIG}/"), (i, chunk[:40])
    for chunk in out.split("@")[1:]:
        assert chunk.startswith(OTHER_RIG), chunk[:40]
    banned_text = ("v060_gate", "HACF_PY310", "hacf-py310", "@hacf", "copy aside")
    banned_text += ("lore_hypergraph",)
    for banned in banned_text:
        assert banned not in out, banned


def test_case_b_grants_and_environment_verbatim(tmp_path: Path) -> None:
    """Case B: the record's grants and environment lines are copied verbatim."""
    grant = "- Lore copy-aside around hypergraph_updater.py (the person's words: 'yes'; row q-123)"
    record = BASE_RECORD + ["", "## Per-run grants", grant, ""]
    record += ["## Environment", "- HACF_PY310=/x/py"]
    _, env = _setup(tmp_path, "hacf", record)
    result = _run(env, "execute", SLICE, "OPR.1.1", "2222222")
    assert result.returncode == 0, result.stderr
    assert grant in result.stdout.splitlines()
    assert "- HACF_PY310=/x/py" in result.stdout.splitlines()
    assert "none recorded" not in result.stdout
    assert "orch-lead@hacf" in result.stdout
    assert OTHER_RIG not in result.stdout


def test_case_c_missing_rig_spec_refused(tmp_path: Path) -> None:
    """Case C: a record without `Rig spec:` is refused with exit 2."""
    record = [line for line in BASE_RECORD if not line.startswith("- Rig spec:")]
    _, env = _setup(tmp_path, "hacf", record)
    result = _run(env, "execute", SLICE, "OPR.1.1", "2222222")
    assert result.returncode == 2
    assert "Rig spec" in result.stderr
    assert result.stdout == ""


def test_case_d_rig_failure_refused(tmp_path: Path) -> None:
    """Case D: a failing `rig` is refused with exit 2."""
    _, env = _setup(tmp_path, "hacf", BASE_RECORD, rig_exit=1)
    result = _run(env, "execute", SLICE, "OPR.1.1", "2222222")
    assert result.returncode == 2
    assert "rig" in result.stderr
    assert result.stdout == ""


def test_rig_missing_or_nameless_refused(tmp_path: Path) -> None:
    """A `rig` with no rigName, or no `rig` at all, is refused with exit 2."""
    _, env = _setup(tmp_path, "hacf", BASE_RECORD)
    _fake_rig(tmp_path / "bin", None)
    assert _run(env, "execute", SLICE, "S", "1").returncode == 2
    (tmp_path / "bin" / "rig").unlink()
    result = _run(env, "execute", SLICE, "S", "1")
    assert result.returncode == 2
    assert "rig" in result.stderr


def test_case_e_legacy_name_resolves(tmp_path: Path) -> None:
    """Case E: a legacy NN_Name resolves to spec/compiled/MiniPRD_NN_Name.md."""
    _, env = _setup(tmp_path, "hacf", BASE_RECORD)
    result = _run(env, "execute", "07_Legacy", "S07", "2222222")
    assert result.returncode == 0, result.stderr
    assert "spec/compiled/MiniPRD_07_Legacy.md" in result.stdout


def test_slice_dir_and_missing_slice(tmp_path: Path) -> None:
    """A slice dir resolves to its SPEC.md; a missing slice is refused."""
    _, env = _setup(tmp_path, "hacf", BASE_RECORD)
    result = _run(env, "audit", "missions/m1/slices/01-first", "S", "1")
    assert result.returncode == 0, result.stderr
    assert SLICE in result.stdout
    assert "~/.hacf/hacf/loops/hacf-loop-01-first.md" in result.stdout
    assert _run(env, "execute", "missions/m1/slices/99-none", "S", "1").returncode == 2
    assert _run(env, "execute", "08_Missing", "S", "1").returncode == 2


def test_usage_refused(tmp_path: Path) -> None:
    """A bad kind or too few arguments is refused with exit 2."""
    _, env = _setup(tmp_path, "hacf", BASE_RECORD)
    assert _run(env, "review", SLICE, "S", "1").returncode == 2
    assert _run(env, "execute", SLICE).returncode == 2
