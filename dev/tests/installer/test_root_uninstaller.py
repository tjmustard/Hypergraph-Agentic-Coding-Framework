"""Tests for the project's own copy of HACF-uninstall.sh (slice 32, OPR.0.6.0.32; mission SPEC R64).

The installer copies HACF-uninstall.sh into the project root and records it in the manifest, an
upgrade refreshes it, and the uninstall deletes it last, after the manifest, only when it is
unchanged. Every run happens in a temp project; nothing is written inside the HACF checkout.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from .conftest import (
    UNINSTALLER,
    git,
    git_env,
    make_local_source,
    run_installer,
    run_uninstall_script,
    sha256,
    snapshot,
)

MANIFEST_REL = ".agents/hacf-manifest.json"
SELF = "HACF-uninstall.sh"
NO_URL = {"HACF_REPO_URL": None}  # HACF_TEST stays 1; without a URL the record is read


def manifest_of(project: Path) -> dict:
    """Return the project's parsed manifest."""
    return json.loads((project / MANIFEST_REL).read_text(encoding="utf-8"))


def installed(project: Path, run_install) -> None:
    """Install claude only from the session upstream, -y."""
    run = run_install(project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output


# ---------------------------------------------------------------------------
# Test 9: the install copies and records it
# ---------------------------------------------------------------------------


def test_install_copies_and_records(project: Path, run_install) -> None:
    """Test 9: ./HACF-uninstall.sh has the upstream bytes, owner-execute and a manifest entry."""
    installed(project, run_install)
    copy = project / SELF
    assert copy.read_bytes() == UNINSTALLER.read_bytes()
    assert copy.stat().st_mode & stat.S_IXUSR
    assert manifest_of(project)["files"][SELF] == {"sha256": sha256(UNINSTALLER), "owner": "hacf"}


# ---------------------------------------------------------------------------
# Test 10: an upgrade refreshes it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("edited", [False, True], ids=["unchanged", "user-edited"])
def test_upgrade_refreshes(tmp_path: Path, project: Path, scratch: Path, edited: bool) -> None:
    """Test 10: c1 installed, c2 changes the uninstaller, a plain -y upgrade copies c2's bytes."""
    src, c1 = make_local_source(tmp_path / "local-source", tmp_path / "source-scratch")
    run = run_installer(scratch, "file:///nonexistent", project, "-y", "--ides=claude",
                        f"--source={src}", f"--ref={c1}")  # fmt: skip
    assert run.returncode == 0, run.output
    env = git_env(scratch)
    with (src / SELF).open("a", encoding="utf-8") as handle:
        handle.write("# c2: a newer uninstaller\n")
    git(["add", "--", SELF], src, env)
    git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "c2"], src, env)
    c2_bytes = (src / SELF).read_bytes()

    copy = project / SELF
    if edited:
        copy.write_bytes(copy.read_bytes() + b"# my own edit\n")
    edited_bytes = copy.read_bytes()

    run = run_installer(scratch, "file:///nonexistent", project, "-y", env=NO_URL)
    assert run.returncode == 0, run.output
    assert copy.read_bytes() == c2_bytes
    assert manifest_of(project)["files"][SELF] == {"sha256": sha256(src / SELF), "owner": "hacf"}
    backups = list((project / ".agents/.backup").glob(f"*/{SELF}"))
    if edited:
        assert [b.read_bytes() for b in backups] == [edited_bytes]
    else:
        assert backups == []


# ---------------------------------------------------------------------------
# Test 11: the uninstall removes it last
# ---------------------------------------------------------------------------


def test_uninstall_removes_it_last(project: Path, run_install, scratch: Path, upstream) -> None:
    """Test 11: the project's own copy deletes itself after the manifest."""
    installed(project, run_install)
    run = run_uninstall_script(scratch, upstream.url, project, "-y", script=project / SELF)
    assert run.returncode == 0, run.output
    assert not (project / SELF).exists()
    assert not (project / MANIFEST_REL).exists()
    manifest_line = run.output.index(f"✗  {MANIFEST_REL}")
    assert run.output.index(f"✗  {SELF}\n", manifest_line) > manifest_line


def test_uninstall_dry_run(project: Path, run_install, scratch: Path, upstream) -> None:
    """Test 11: --dry-run lists it as deleted last and changes nothing."""
    installed(project, run_install)
    before = snapshot(project)
    run = run_uninstall_script(
        scratch, upstream.url, project, "-y", "--dry-run", script=project / SELF
    )
    assert run.returncode == 0, run.output
    assert f"✗  {SELF} (deleted last)" in run.output
    assert f"[DRY-RUN] would delete {SELF} last" in run.output
    assert snapshot(project) == before


def test_uninstall_keeps_edited_copy(project: Path, run_install, scratch: Path, upstream) -> None:
    """Test 11: a user-edited copy is kept, and the report names it as changed."""
    installed(project, run_install)
    copy = project / SELF
    copy.write_bytes(copy.read_bytes() + b"# my own edit\n")
    edited = copy.read_bytes()
    run = run_uninstall_script(scratch, upstream.url, project, "-y", script=copy)
    assert run.returncode == 0, run.output
    assert copy.read_bytes() == edited
    assert f"kept (changed since HACF installed it): {SELF}" in run.output
    assert not (project / MANIFEST_REL).exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_uninstall_failed_step_keeps_it(
    project: Path, run_install, scratch: Path, upstream
) -> None:
    """Test 11: a failed step keeps both the copy and the manifest, with a report line."""
    installed(project, run_install)
    locked = project / ".agents/scripts"
    mode = locked.stat().st_mode & 0o777
    locked.chmod(0o555)
    try:
        # unshare -rn maps this user to root in its namespace, which ignores the chmod; the
        # network is not needed here (the upstream is a file:// clone).
        run = run_uninstall_script(
            scratch, upstream.url, project, "-y", script=project / SELF, wrapper=False
        )
    finally:
        locked.chmod(mode)
    assert run.returncode == 0, run.output
    assert (project / SELF).is_file()
    assert (project / MANIFEST_REL).is_file()
    assert f"{SELF} kept: " in run.output
    assert "step(s) failed; fix them and re-run it." in run.output
