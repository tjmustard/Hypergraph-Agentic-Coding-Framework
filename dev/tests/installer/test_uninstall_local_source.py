"""Tests for HACF-uninstall.sh with a local source (slice 32, OPR.0.6.0.32; mission SPEC R58).

A project installed with HACF-install.sh --source is uninstalled from the recorded source and
installed_commit by default, or from an explicit --source/--ref, and never from a remote when the
recorded source cannot be used. Every run happens in a temp project under an isolated git config
(conftest.git_env); nothing is written inside the HACF checkout.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from .conftest import (
    add_commit,
    git,
    git_argv_logger,
    git_env,
    make_local_source,
    run_installer,
    run_uninstall_script,
    snapshot,
)

CHANGED = "Nothing was changed."
MANIFEST_REL = ".agents/hacf-manifest.json"
SOURCE_REL = ".agents/state/install/source.json"
NO_URL = {"HACF_REPO_URL": None}  # HACF_TEST stays 1; without a URL the record is read


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, str]:
    """Return (checkout, HEAD) of a private local source for this test."""
    return make_local_source(tmp_path / "local-source", tmp_path / "source-scratch")


def new_project(tmp_path: Path, scratch: Path, name: str) -> Path:
    """Return an empty temp project (`git init`, no commits)."""
    path = tmp_path / name
    path.mkdir(parents=True)
    git(["init", "-q", "-b", "main"], path, git_env(scratch))
    return path


def argv_lines(log: Path) -> list[str]:
    """Return the logged git argv lines (empty when git never ran)."""
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def leftovers(project: Path) -> dict[str, str]:
    """Return the snapshot of a project after an uninstall, without timestamped backup dirs."""
    return {
        rel: value
        for rel, value in snapshot(project).items()
        if not rel.startswith((".git/", ".agents/.backup/"))
    }


# ---------------------------------------------------------------------------
# Test 1 (US-002): plain -y uses the recorded source and installed_commit
# ---------------------------------------------------------------------------


def test_recorded_source_uninstall(tmp_path: Path, scratch: Path, upstream, source) -> None:
    """Test 1: no clone, no URL; the banner names the record; the leftovers equal a remote's."""
    src, head = source
    project = new_project(tmp_path, scratch, "project")
    run = run_installer(
        scratch, upstream.url, project, "-y", "--ides=claude,cursor", f"--source={src}"
    )
    assert run.returncode == 0, run.output

    log = tmp_path / "git-argv.log"
    prefix = git_argv_logger(tmp_path, log)
    run = run_uninstall_script(scratch, upstream.url, project, "-y", env=NO_URL, path_prefix=prefix)
    assert run.returncode == 0, run.output
    assert f"upstream: local {src.resolve()} @ {head} (recorded by the install)" in run.output
    assert f"upstream local {src.resolve()} @ {head}" in run.output
    argv = argv_lines(log)
    assert argv, "the git wrapper logged nothing"
    assert not [line for line in argv if "://" in line or "clone" in line], argv
    assert not (project / MANIFEST_REL).exists()

    # "As before": the same payload installed from the remote and uninstalled through its clone
    # leaves the same tree; the emptied .agents/state/install/ is the one extra leftover
    # (.agents/state/ is NEVER_COPY; the uninstaller removes source.json itself, KI-3).
    control = new_project(tmp_path / "remote-flow", scratch, "project")  # same name: SPEC.md
    run = run_installer(scratch, upstream.url, control, "-y", "--ides=claude,cursor")
    assert run.returncode == 0, run.output
    run = run_uninstall_script(scratch, upstream.url, control, "-y")
    assert run.returncode == 0, run.output
    local, remote = leftovers(project), leftovers(control)
    extra = {".agents/state/install/"}  # lead ruling P36: only the directory; the file is gone
    assert set(local) - set(remote) == extra
    assert {rel: value for rel, value in local.items() if rel not in extra} == remote


# ---------------------------------------------------------------------------
# Test 2 (US-001): explicit --source and --ref
# ---------------------------------------------------------------------------


def test_explicit_source_and_ref(tmp_path: Path, scratch: Path, upstream, source) -> None:
    """Test 2: --source alone uses installed_commit; --ref=<sha> works; --ref alone exits 2."""
    src, head = source
    env = git_env(scratch)
    other = tmp_path / "other-clone"
    git(["clone", "-q", "--no-local", str(src), str(other)], tmp_path, env)
    newer = add_commit(other, env, "newer than the install")

    project = new_project(tmp_path, scratch, "project")
    run = run_installer(scratch, upstream.url, project, "-y", "--ides=claude", f"--source={src}")
    assert run.returncode == 0, run.output
    run = run_uninstall_script(scratch, upstream.url, project, "-y", f"--source={other}")
    assert run.returncode == 0, run.output
    assert f"upstream local {other.resolve()} @ {head}" in run.output
    assert f"(using {head}, which is not HEAD)" in run.output
    assert not (project / MANIFEST_REL).exists()

    second = new_project(tmp_path, scratch, "second")
    run = run_installer(scratch, upstream.url, second, "-y", "--ides=claude", f"--source={src}")
    assert run.returncode == 0, run.output
    run = run_uninstall_script(
        scratch, upstream.url, second, "-y", f"--source={other}", f"--ref={newer}"
    )
    assert run.returncode == 0, run.output
    assert f"upstream local {other.resolve()} @ {newer}" in run.output
    assert not (second / MANIFEST_REL).exists()

    third = new_project(tmp_path, scratch, "third")
    before = snapshot(third)
    run = run_uninstall_script(scratch, upstream.url, third, "-y", f"--ref={head}")
    assert run.returncode == 2, run.output
    assert "--ref needs --source" in run.output and "Usage:" in run.output
    assert snapshot(third) == before


@pytest.mark.parametrize(
    "args",
    [
        ["--source=SRC", "--repo-url=https://example.invalid/hacf.git"],
        ["--source=SRC", "--branch=main"],
        ["--source="],
        ["--source=SRC", "--ref="],
    ],
    ids=["with-repo-url", "with-branch", "empty-source", "empty-ref"],
)
def test_usage_errors(tmp_path: Path, scratch: Path, upstream, source, args) -> None:
    """Test 2: each bad option combination exits 2 with usage and changes nothing."""
    src, _ = source
    project = new_project(tmp_path, scratch, "project")
    before = snapshot(project)
    argv = [a.replace("SRC", str(src)) for a in args]
    run = run_uninstall_script(scratch, upstream.url, project, "-y", *argv)
    assert run.returncode == 2, run.output
    assert CHANGED in run.output and "Usage:" in run.output
    assert snapshot(project) == before


# ---------------------------------------------------------------------------
# Test 3 (US-003): an unusable recorded source refuses; it never falls back to a remote
# ---------------------------------------------------------------------------


def _break(case: str, project: Path, src: Path, head: str, env: dict[str, str]) -> str:
    """Make the recorded source unusable in one way; return the expected reason text."""
    if case == "source-removed":
        shutil.rmtree(src)
        return "the source not a directory"
    if case == "file-deleted":
        (project / SOURCE_REL).unlink()
        return f"{SOURCE_REL} is missing"
    if case == "file-commit-edited":
        path = project / SOURCE_REL
        record = json.loads(path.read_text(encoding="utf-8"))
        record["commit"] = "0" * 40
        path.write_text(json.dumps(record), encoding="utf-8")
        return f"records commit {'0' * 40}, not installed_commit {head}"
    if case == "manifest-stale":
        path = project / MANIFEST_REL
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["installed_commit"] = "1" * 40
        path.write_text(json.dumps(manifest), encoding="utf-8")
        stale = "1" * 40
        return (
            f"the recorded local source is stale (source.commit {head} != installed_commit {stale}"
        )
    if case == "commit-missing":
        parent = git(["rev-parse", "HEAD~1"], src, env).strip()
        git(["update-ref", "refs/heads/main", parent], src, env)
        git(["reset", "-q", "--hard", parent], src, env)
        git(["reflog", "expire", "--expire=now", "--all"], src, env)
        git(["gc", "-q", "--prune=now"], src, env)
        return f"commit {head} is not in"
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    ["source-removed", "file-deleted", "file-commit-edited", "manifest-stale", "commit-missing"],
)
def test_unusable_record_refuses(tmp_path: Path, scratch: Path, upstream, source, case) -> None:
    """Test 3: exit 1, `Nothing was changed.`, `Pass --source`, nothing changed and no clone."""
    src, _ = source
    env = git_env(scratch)
    head = add_commit(src, env, "the installed commit")  # HEAD~1 exists for commit-missing
    project = new_project(tmp_path, scratch, "project")
    run = run_installer(scratch, upstream.url, project, "-y", "--ides=claude", f"--source={src}")
    assert run.returncode == 0, run.output
    expected = _break(case, project, src, head, env)

    log = tmp_path / "git-argv.log"
    prefix = git_argv_logger(tmp_path, log)
    before = snapshot(project)
    run = run_uninstall_script(scratch, upstream.url, project, "-y", env=NO_URL, path_prefix=prefix)
    assert run.returncode == 1, run.output
    assert CHANGED in run.output and "Pass --source" in run.output
    assert "this project was installed from a local HACF checkout" in run.output
    assert expected in run.output, run.output
    assert snapshot(project) == before
    assert not [line for line in argv_lines(log) if "clone" in line or "://" in line]


# ---------------------------------------------------------------------------
# Test 4: a remote install still uninstalls through its clone
# ---------------------------------------------------------------------------


def test_remote_install_uses_clone(tmp_path: Path, scratch: Path, upstream) -> None:
    """Test 4: a HACF_TEST=1 remote install is uninstalled through a clone of its URL."""
    project = new_project(tmp_path, scratch, "project")
    run = run_installer(scratch, upstream.url, project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    log = tmp_path / "git-argv.log"
    prefix = git_argv_logger(tmp_path, log)
    run = run_uninstall_script(scratch, upstream.url, project, "-y", path_prefix=prefix)
    assert run.returncode == 0, run.output
    assert f"upstream: {upstream.url} (branch main)" in run.output
    assert f"upstream {upstream.url} @ {upstream.sha}" in run.output
    assert [line for line in argv_lines(log) if line.startswith("clone") and upstream.url in line]
    assert not (project / MANIFEST_REL).exists()
