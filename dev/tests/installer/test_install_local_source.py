"""Tests for HACF-install.sh --source/--ref (slice 31, OPR.0.6.0.31; mission SPEC R56).

A local source is a non-bare repo built from build_upstream's payload (the working tree), copied
per test when the test changes it. Every run happens in a temp project under an isolated git
config (conftest.git_env); nothing is written inside the HACF checkout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import build_upstream, git, git_env, run_installer, sha256, snapshot

NOTHING = "Nothing was written."


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------


class Source:
    """A local HACF source checkout and the commits the ref tests name."""

    def __init__(self, path: Path, url: str, refs: dict[str, str]) -> None:
        """Record the checkout path, the bare URL and the named commits."""
        self.path = path
        self.url = url  # the bare clone's file:// URL, used only for HACF_REPO_URL
        self.refs = refs

    @property
    def head(self) -> str:
        """Return the source's HEAD commit."""
        return self.refs["head"]


def commit_tree(src: Path, env: dict[str, str], parent: str, message: str) -> str:
    """Return a new commit with parent's tree; the work tree and index are not touched."""
    return git(["commit-tree", f"{parent}^{{tree}}", "-p", parent, "-m", message], src, env).strip()


@pytest.fixture(scope="session")
def base_source(tmp_path_factory: pytest.TempPathFactory) -> Source:
    """Build one local source with a main branch, a branch, a tag and a remote-only commit.

    main: C0 (the payload) <- C1 (HEAD). feature -> C2, tag v-test (annotated) -> C3,
    refs/remotes/origin/x -> C4 (reachable from no branch or tag). All share C0's tree.
    """
    root = tmp_path_factory.mktemp("local-source")
    scratch = root / "scratch"
    upstream = build_upstream(root, scratch)
    src = root / "src"
    env = git_env(scratch)
    c0 = upstream.sha
    c1 = commit_tree(src, env, c0, "c1 on main")
    git(["update-ref", "refs/heads/main", c1], src, env)
    c2 = commit_tree(src, env, c0, "c2 on feature")
    git(["update-ref", "refs/heads/feature", c2], src, env)
    c3 = commit_tree(src, env, c0, "c3 tagged")
    git(["-c", "tag.gpgsign=false", "tag", "-a", "-m", "test tag", "v-test", c3], src, env)
    c4 = commit_tree(src, env, c0, "c4 only on origin/x")
    git(["update-ref", "refs/remotes/origin/x", c4], src, env)
    refs = {"head": c1, "c0": c0, "feature": c2, "tag": c3, "origin": c4}
    return Source(src, upstream.url, refs)


@pytest.fixture
def source_copy(tmp_path: Path, base_source: Source) -> Source:
    """Return a private copy of the base source, for tests that change it."""
    dest = tmp_path / "hacf-src"
    shutil.copytree(base_source.path, dest, symlinks=True)
    return Source(dest, base_source.url, dict(base_source.refs))


def install(scratch: Path, src: Source, cwd: Path, *args: str, **kwargs):
    """Run the installer in cwd with --source=<src> and the given arguments."""
    return run_installer(scratch, src.url, cwd, f"--source={src.path}", *args, **kwargs)


def manifest_of(project: Path) -> dict:
    """Return the project's parsed manifest."""
    return json.loads((project / ".agents/hacf-manifest.json").read_text(encoding="utf-8"))


def blob(src: Path, sha: str, rel: str, env: dict[str, str]) -> bytes:
    """Return the bytes of rel in commit sha of src (git show <sha>:<path>)."""
    proc = subprocess.run(
        ["git", "show", f"{sha}:{rel}"], cwd=src, env=env, capture_output=True, check=True
    )
    return proc.stdout


def wrapper_bin(tmp_path: Path, name: str, body: str) -> Path:
    """Return a bin dir holding a `git` wrapper whose shell body runs before the real git."""
    bin_dir = tmp_path / name
    bin_dir.mkdir()
    real = shutil.which("git")
    assert real is not None
    script = bin_dir / "git"
    script.write_text(f'#!/bin/sh\n{body}\nexec "{real}" "$@"\n', encoding="utf-8")
    script.chmod(0o755)
    return bin_dir


# ---------------------------------------------------------------------------
# Test 1 (US-001): byte equality, the manifest record and the sha printed first
# ---------------------------------------------------------------------------


def test_local_install_bytes_and_manifest(
    project: Path, scratch: Path, base_source: Source
) -> None:
    """Test 1: installed bytes equal the commit's blobs; the sha is printed before any write."""
    run = install(scratch, base_source, project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    sha = base_source.head
    label = f"local {base_source.path} @ {sha}"
    assert label in run.output
    first_write = run.output.index("✅")
    assert run.output.index(sha) < first_write, run.output[:first_write]
    report = run.output[run.output.index("📝  Install report") :]
    assert label in report
    env = git_env(scratch)
    manifest = manifest_of(project)
    assert manifest["installed_commit"] == sha
    assert manifest["source"] == {"kind": "local", "commit": sha}
    assert "path" not in manifest["source"]
    assert str(base_source.path) not in json.dumps(manifest)
    assert len(manifest["files"]) > 100
    for rel in manifest["files"]:
        assert (project / rel).read_bytes() == blob(base_source.path, sha, rel, env), rel
    record = json.loads((project / ".agents/state/install/source.json").read_text(encoding="utf-8"))
    assert record["kind"] == "local"
    assert record["schema"] == "hacf.install-source/v1"
    assert record["path"] == str(base_source.path.resolve())
    # C3 (OPR.99.0.10.3): the ref is recorded as the resolved full sha, never "HEAD"
    assert record["ref"] == sha and record["commit"] == sha
    assert f"--source={base_source.path} --ref={sha}" in report


# ---------------------------------------------------------------------------
# Test 2 (US-002): every kind of revision installs its commit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "key"),
    [
        (None, "c0"),  # a full sha (filled in from the source)
        ("feature", "feature"),
        ("v-test", "tag"),
        ("HEAD~1", "c0"),
        ("origin/x", "origin"),
    ],
)
def test_ref_forms(project: Path, scratch: Path, base_source: Source, ref, key) -> None:
    """Test 2: a sha, branch, tag, HEAD~1 and an origin-only commit each install that commit."""
    expected = base_source.refs[key]
    run = install(scratch, base_source, project, "-y", "--ides=claude", f"--ref={ref or expected}")
    assert run.returncode == 0, run.output
    manifest = manifest_of(project)
    assert manifest["installed_commit"] == expected
    assert manifest["source"]["commit"] == expected
    assert f"@ {expected}" in run.output
    assert f"(installing {expected}, which is not HEAD)" in run.output


# ---------------------------------------------------------------------------
# Test 3 (US-002 refusals)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ref_arg", ["--ref=no-such-ref", "--ref=", "--ref=-x"])
def test_bad_refs_refused(project: Path, scratch: Path, base_source: Source, ref_arg: str) -> None:
    """Test 3: an unknown, empty or dash-led --ref exits 1 and writes nothing."""
    before = snapshot(project)
    run = install(scratch, base_source, project, "-y", "--ides=claude", ref_arg)
    assert run.returncode == 1, run.output
    assert NOTHING in run.output
    assert snapshot(project) == before


# ---------------------------------------------------------------------------
# Test 4 (US-003): uncommitted changes never reach the project
# ---------------------------------------------------------------------------


def test_dirty_source_installs_commit_only(
    project: Path, scratch: Path, source_copy: Source
) -> None:
    """Test 4: uncommitted edits are warned about, never installed; the index is unchanged."""
    src = source_copy.path
    tracked = ".agents/rules/python.md"
    untracked = ".agents/rules/zz-untracked-note.md"
    with (src / tracked).open("a", encoding="utf-8") as handle:
        handle.write("\nUNCOMMITTED EDIT\n")
    (src / untracked).write_text("not committed\n", encoding="utf-8")
    index_before = sha256(src / ".git" / "index")

    run = install(scratch, source_copy, project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    env = git_env(scratch)
    assert (project / tracked).read_bytes() == blob(src, source_copy.head, tracked, env)
    assert not (project / untracked).exists()
    warning = run.output[run.output.index("uncommitted changes in") :]
    warning = warning[: warning.index("source: local")]
    assert "are NOT installed" in warning
    assert tracked in warning and untracked in warning
    assert sha256(src / ".git" / "index") == index_before


# ---------------------------------------------------------------------------
# Test 5 (US-004): bad sources and option combinations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["--ref=HEAD"],
        ["--source=SRC", "--repo-url=https://example.invalid/hacf.git"],
        ["--source=SRC", "--branch=main"],
        ["--source="],
    ],
    ids=["ref-without-source", "with-repo-url", "with-branch", "empty-source"],
)
def test_usage_errors(project: Path, scratch: Path, base_source: Source, args) -> None:
    """Test 5: each bad option combination exits 2 with usage and writes nothing."""
    before = snapshot(project)
    argv = [a.replace("SRC", str(base_source.path)) for a in args]
    run = run_installer(scratch, base_source.url, project, "-y", *argv)
    assert run.returncode == 2, run.output
    assert NOTHING in run.output and "Usage:" in run.output
    assert snapshot(project) == before


def _refusal(case: str, tmp_path: Path, project: Path, src: Source, env: dict[str, str]):
    """Prepare one refusal case; return (source value, cwd, extra args, path prefix, expected)."""
    path = src.path
    if case == "not-a-directory":
        return str(tmp_path / "missing"), project, [], None, "not a directory"
    if case == "control-character":
        return str(path) + "\x07", project, [], None, "control character"
    if case == "not-a-git-repo":
        plain = tmp_path / "plain"
        plain.mkdir()
        return str(plain), project, [], None, "not a git repository"
    if case == "dubious-ownership":
        body = (
            'case "$*" in *--is-bare-repository*)\n'
            "  echo \"fatal: detected dubious ownership in repository at '$2'\" >&2\n"
            "  exit 128;;\nesac"
        )
        return str(path), project, [], wrapper_bin(tmp_path, "dubious-bin", body), "safe.directory"
    if case == "partial-clone":
        git(["config", "extensions.partialClone", "origin"], path, env)
        return str(path), project, [], None, "partial clone"
    if case == "target-is-source":
        return str(path), path, [], None, "is the source checkout"
    if case == "target-inside-source":
        inner = path / "inner-target"
        inner.mkdir()
        return str(path), inner, [], None, "lies inside it"
    if case == "worktree-target":
        worktree = tmp_path / "wt"
        git(["worktree", "add", "-q", "--detach", str(worktree)], path, env)
        return str(path), worktree, [], None, "shares its git directory"
    if case == "version-mismatch":
        installer = path / "HACF-install.sh"
        text = installer.read_text(encoding="utf-8")
        installer.write_text(text.replace('HACF_VERSION="0.6.7"', 'HACF_VERSION="0.5.9"', 1))
        git(["-c", "commit.gpgsign=false", "commit", "-q", "-am", "old version"], path, env)
        return str(path), project, [], None, "carries HACF 0.5.9; this installer is 0.6.7"
    if case == "symlinked-payload-root":
        git(["rm", "-r", "-q", "--", ".agents/rules"], path, env)
        os.symlink("../dev", path / ".agents" / "rules")
        git(["add", "--", ".agents/rules"], path, env)
        git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "symlinked rules"], path, env)
        return str(path), project, [], None, ".agents/rules is a symlink"
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "not-a-directory",
        "control-character",
        "not-a-git-repo",
        "dubious-ownership",
        "partial-clone",
        "target-is-source",
        "target-inside-source",
        "worktree-target",
        "version-mismatch",
        "symlinked-payload-root",
    ],
)
def test_refusals(tmp_path: Path, project: Path, scratch: Path, source_copy: Source, case) -> None:
    """Test 5: each bad source exits 1, names its reason and writes nothing anywhere."""
    env = git_env(scratch)
    value, cwd, extra, prefix, expected = _refusal(case, tmp_path, project, source_copy, env)
    hooks = source_copy.path / ".git" / "hooks"
    hooks_before = sorted(os.listdir(hooks))
    before = snapshot(cwd)
    run = run_installer(
        scratch, source_copy.url, cwd, "-y", "--ides=claude", f"--source={value}", *extra,
        path_prefix=prefix,
    )  # fmt: skip
    assert run.returncode == 1, run.output
    assert NOTHING in run.output
    assert expected in run.output, run.output
    assert snapshot(cwd) == before
    assert sorted(os.listdir(hooks)) == hooks_before


def test_source_inside_target_accepted(project: Path, scratch: Path, base_source: Source) -> None:
    """Test 5: a source checkout inside the target project is accepted."""
    inner = project / "vendor" / "hacf"
    shutil.copytree(base_source.path, inner, symlinks=True)
    inner_source = Source(inner, base_source.url, base_source.refs)
    run = install(scratch, inner_source, project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    assert manifest_of(project)["source"] == {"kind": "local", "commit": base_source.head}


# ---------------------------------------------------------------------------
# Test 6 (US-006): no network process; consent-first unchanged
# ---------------------------------------------------------------------------


def test_no_network_and_consent_first(
    tmp_path: Path, project: Path, scratch: Path, base_source: Source
) -> None:
    """Test 6: no ls-remote and no URL in any git argv; no terminal and no -y consent to nothing."""
    log = tmp_path / "git-argv.log"
    prefix = wrapper_bin(tmp_path, "git-log-bin", f'printf \'%s\\n\' "$*" >> "{log}"')
    run = install(scratch, base_source, project, "-y", "--ides=claude", path_prefix=prefix)
    assert run.returncode == 0, run.output
    argv = log.read_text(encoding="utf-8").splitlines()
    assert argv, "the git wrapper logged nothing"
    assert not [line for line in argv if "ls-remote" in line], argv
    assert not [line for line in argv if "://" in line], argv
    assert run.shim_calls == []

    # No terminal and no -y: the run consents to nothing.
    second = tmp_path / "second"
    second.mkdir()
    git(["init", "-q", "-b", "main"], second, git_env(scratch))
    run = install(scratch, base_source, second, "--ides=claude", "--code-graph=codebase-memory-mcp")
    assert run.returncode == 0, run.output
    assert run.shim_calls == []  # no pip, uv, uvx, curl or npx
    assert "declined (no terminal to ask on)" in run.output
    settings = json.loads((second / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "statusLine" not in settings
    assert not (second / ".agents/state/codegraph/project.json").exists()


# ---------------------------------------------------------------------------
# Test 7 (US-005): the per-machine file is ignored; a remote install removes it
# ---------------------------------------------------------------------------


def test_source_file_ignored_and_removed_by_remote(
    project: Path, scratch: Path, base_source: Source, upstream
) -> None:
    """Test 7: source.json is git-ignored; a later remote install removes it."""
    run = install(scratch, base_source, project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    rel = ".agents/state/install/source.json"
    assert (project / rel).is_file()
    check = subprocess.run(
        ["git", "check-ignore", "-q", rel], cwd=project, env=git_env(scratch), check=False
    )
    assert check.returncode == 0

    run = run_installer(scratch, upstream.url, project, "-y", "--ides=claude")
    assert run.returncode == 0, run.output
    assert not (project / rel).exists()
    manifest = manifest_of(project)
    assert manifest["source"] == {
        "kind": "remote", "url": upstream.url, "branch": "main", "commit": upstream.sha,
    }  # fmt: skip
    assert manifest["installed_commit"] == upstream.sha
