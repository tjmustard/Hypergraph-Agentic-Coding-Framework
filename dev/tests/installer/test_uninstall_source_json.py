"""Tests for HACF-uninstall.sh removing .agents/state/install/source.json (OPR.99.0.11.3, KI-3).

The per-machine install record of a --source install is read by the uninstaller to find its
source, so it goes last and only when no step failed: a failed uninstall stays re-runnable. The rest
of `.agents/state/` is kept byte for byte. Every run happens in a temp project; nothing is written
inside the HACF checkout.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .conftest import (
    git,
    git_env,
    make_local_source,
    run_installer,
    run_uninstall_script,
    sha256,
)

MANIFEST_REL = ".agents/hacf-manifest.json"
SOURCE_REL = ".agents/state/install/source.json"
OTHER_REL = ".agents/state/install/other-machine-state.txt"
NO_URL = {"HACF_REPO_URL": None}  # HACF_TEST stays 1; without a URL the record is read


@pytest.fixture
def installed(tmp_path: Path, scratch: Path, upstream) -> tuple[Path, Path]:
    """Return (project, local source): a `--source` install plus one unrelated state file."""
    src, _head = make_local_source(tmp_path / "local-source", tmp_path / "source-scratch")
    project = tmp_path / "project"
    project.mkdir()
    git(["init", "-q", "-b", "main"], project, git_env(scratch))
    run = run_installer(scratch, upstream.url, project, "-y", "--ides=claude", f"--source={src}")
    assert run.returncode == 0, run.output
    assert (project / SOURCE_REL).is_file()
    (project / OTHER_REL).write_bytes(b"keep me\x00byte for byte\n")
    return project, src


def test_a_clean_uninstall_removes_the_record_and_keeps_the_rest(
    installed: tuple[Path, Path], scratch: Path, upstream
) -> None:
    """(a) A clean uninstall removes source.json, keeps other state byte for byte, exits 0."""
    project, _src = installed
    other = (project / OTHER_REL).read_bytes()
    other_sha = sha256(project / OTHER_REL)
    run = run_uninstall_script(scratch, upstream.url, project, "-y", env=NO_URL)
    assert run.returncode == 0, run.output
    assert not (project / SOURCE_REL).exists()
    assert not (project / MANIFEST_REL).exists()
    assert (project / OTHER_REL).read_bytes() == other
    assert sha256(project / OTHER_REL) == other_sha
    assert f"removed {SOURCE_REL}" in run.output


def test_a_dry_run_names_it_and_removes_nothing(
    installed: tuple[Path, Path], scratch: Path, upstream
) -> None:
    """MR 3: a dry run says it would remove the record and leaves it in place."""
    project, _src = installed
    before = (project / SOURCE_REL).read_bytes()
    run = run_uninstall_script(scratch, upstream.url, project, "--dry-run", env=NO_URL)
    assert run.returncode == 0, run.output
    assert f"[DRY-RUN] would remove {SOURCE_REL}" in run.output
    assert (project / SOURCE_REL).read_bytes() == before
    assert (project / MANIFEST_REL).is_file()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_b_c_a_failed_step_keeps_the_record_and_the_rerun_finishes(
    installed: tuple[Path, Path], scratch: Path, upstream
) -> None:
    """(b) A failed step keeps source.json and says so; (c) after the fix the re-run finishes."""
    project, _src = installed
    locked = project / ".agents/scripts"
    mode = locked.stat().st_mode & 0o777
    locked.chmod(0o555)
    try:
        # `unshare -rn` maps this user to root, which ignores the chmod: run without the wrapper.
        run = run_uninstall_script(scratch, upstream.url, project, "-y", env=NO_URL, wrapper=False)
        assert run.returncode == 0, run.output
        assert (project / SOURCE_REL).is_file()
        assert (project / MANIFEST_REL).is_file()
        msg = f"kept {SOURCE_REL} (uninstall had failures; re-run to finish)"
        assert msg in run.output
        assert f"removed {SOURCE_REL}" not in run.output
    finally:
        locked.chmod(mode)

    # (c) the cause is fixed: the re-run still finds its source, completes, and removes the record
    run = run_uninstall_script(scratch, upstream.url, project, "-y", env=NO_URL)
    assert run.returncode == 0, run.output
    assert "is missing" not in run.output
    assert not (project / SOURCE_REL).exists()
    assert not (project / MANIFEST_REL).exists()
    assert (project / OTHER_REL).read_bytes() == b"keep me\x00byte for byte\n"


def test_d_a_symlink_named_source_json_is_not_followed(
    installed: tuple[Path, Path], tmp_path: Path, scratch: Path, upstream
) -> None:
    """(d) The record is only ever a regular file: a symlink is left, and so is its target."""
    project, _src = installed
    real = project / SOURCE_REL
    target = tmp_path / "elsewhere.json"
    target.write_bytes(real.read_bytes())
    real.unlink()
    real.symlink_to(target)
    # the uninstaller reads the record through the link (a local install), then must not delete it
    run = run_uninstall_script(scratch, upstream.url, project, "-y", env=NO_URL)
    assert run.returncode == 0, run.output
    assert target.is_file()
    assert real.is_symlink()
    assert f"{SOURCE_REL} kept: it is not a regular file." in run.output


def test_the_removal_is_one_named_path_not_a_pattern() -> None:
    """MR 2: the carve-out is the single SOURCE_FILE_REL path; .agents/state/ stays never-delete."""
    text = (Path(__file__).resolve().parents[3] / "HACF-uninstall.sh").read_text(encoding="utf-8")
    assert '".agents/state/"' in text  # still on the never-delete list
    body = text.split("remove_source_record() {", 1)[1].split("\n}\n", 1)[0]
    deletions = [ln.strip() for ln in body.splitlines() if ln.strip().startswith(("rm ", "find "))]
    assert deletions == ['rm -f -- "$SOURCE_FILE_REL"']
