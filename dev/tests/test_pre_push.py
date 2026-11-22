"""Tests for .agents/scripts/pre-push: swimlane and research work never reaches a remote."""

from __future__ import annotations

import shutil

import pytest
from conftest import REPO_ROOT

HOOK = REPO_ROOT / ".agents" / "scripts" / "pre-push"
ZERO = "0" * 40


def run_hook(repo, *lines: str):
    """Feed the hook its stdin lines, as git does for `git push origin ...`."""
    stdin = "".join(f"{line}\n" for line in lines)
    return repo.run(["bash", str(HOOK), "origin", "file:///dev/null"], stdin=stdin)


@pytest.fixture
def lanes(tmp_git_repo):
    """Return (repo, main_sha, swim_sha): swim_sha is a commit only on swimlane/x."""
    main_sha = tmp_git_repo.git("rev-parse", "HEAD").strip()
    tmp_git_repo.git("checkout", "-q", "-b", "swimlane/x")
    tmp_git_repo.write("work.txt", "swimlane work\n")
    swim_sha = tmp_git_repo.commit("swimlane work", "work.txt")
    tmp_git_repo.git("checkout", "-q", "main")
    return tmp_git_repo, main_sha, swim_sha


def test_pre_push_refuses_swimlane_sha_to_main(lanes):
    """`<sha>:refs/heads/main` is refused while only swimlane/x has the sha."""
    repo, main_sha, swim_sha = lanes
    proc = run_hook(repo, f"{swim_sha} {swim_sha} refs/heads/main {main_sha}")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "only on research/ or swimlane/ branches" in proc.stdout

    # The branch point is on main too, so it may be pushed.
    proc = run_hook(repo, f"refs/heads/main {main_sha} refs/heads/main {ZERO}")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # Once main contains the sha, pushing it passes.
    repo.git("merge", "-q", "--ff-only", "swimlane/x")
    proc = run_hook(repo, f"{swim_sha} {swim_sha} refs/heads/main {main_sha}")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pre_push_refuses_research_only_sha(lanes):
    """A sha contained only in research/* and swimlane/* branches is refused."""
    repo, main_sha, swim_sha = lanes
    repo.git("branch", "research/y", swim_sha)
    proc = run_hook(repo, f"refs/heads/other {swim_sha} refs/heads/other {ZERO}")
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_pre_push_refuses_non_heads_refs(lanes):
    """A push to a remote ref outside refs/heads/ and refs/tags/ is refused."""
    repo, main_sha, _ = lanes
    for remote_ref in ("refs/hacf/x", "refs/notes/commits", "refs/remotes/origin/main"):
        proc = run_hook(repo, f"refs/heads/main {main_sha} {remote_ref} {ZERO}")
        assert proc.returncode == 1, remote_ref
        assert "only refs/heads/ and refs/tags/" in proc.stdout
    repo.git("tag", "v1", main_sha)
    proc = run_hook(repo, f"refs/tags/v1 {main_sha} refs/tags/v1 {ZERO}")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pre_push_refuses_swimlane_branch(lanes):
    """Pushing a local swimlane/ or research/ branch is refused, whatever the remote name."""
    repo, main_sha, swim_sha = lanes
    proc = run_hook(repo, f"refs/heads/swimlane/x {swim_sha} refs/heads/main {ZERO}")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    repo.git("branch", "research/z", main_sha)
    proc = run_hook(repo, f"refs/heads/research/z {main_sha} refs/heads/research/z {ZERO}")
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_pre_push_deletion_and_unknown_objects(lanes):
    """A deletion skips the reachability check; a sha git cannot check is refused."""
    repo, main_sha, _ = lanes
    proc = run_hook(repo, f"(delete) {ZERO} refs/heads/old {main_sha}")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    blob = repo.run(["git", "hash-object", "-w", "--stdin"], stdin="blob\n").stdout.strip()
    proc = run_hook(repo, f"{blob} {blob} refs/tags/blob {ZERO}")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Cannot tell which branches contain" in proc.stdout


def test_pre_push_real_push(lanes, tmp_path):
    """Through a real `git push`, the sha refspec trick is refused and main still pushes."""
    repo, _, swim_sha = lanes
    remote = tmp_path / "remote.git"
    repo.run(["git", "init", "-q", "--bare", str(remote)])
    hooks = tmp_path / "push-hooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "pre-push")
    (hooks / "pre-push").chmod(0o755)
    repo.git("config", "core.hooksPath", str(hooks))
    repo.git("remote", "add", "origin", str(remote))

    proc = repo.run(["git", "push", "origin", f"{swim_sha}:refs/heads/main"])
    assert proc.returncode != 0
    assert "PUSH BLOCKED" in proc.stdout + proc.stderr
    proc = repo.run(["git", "push", "origin", "swimlane/x"])
    assert proc.returncode != 0
    proc = repo.run(["git", "push", "origin", "main"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    pushed = repo.run(["git", "--git-dir", str(remote), "rev-parse", "main"]).stdout.strip()
    assert pushed == repo.git("rev-parse", "main").strip()
