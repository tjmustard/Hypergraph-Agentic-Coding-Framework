"""Tests of `release_export.py scan --tree worktree|head` (v064-upgrade-path OPR.99.0.10.2, R31).

Offline and synthetic: the world of test_build.py, its profile (latest row 0.3.0, which excludes
`lore/` and `late.txt`) and a temporary digest file. The home root is built from parts, and the
digest-window case plants a stand-in literal; no private literal or digest of one is written here.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_build import HOME_ROOT, World, clean_env, g, make_world, tool, write
from test_scan import FAKE_ADDRESS, STAND_IN, sha, window_line

UNKNOWN = (
    "scan {tree}: unknown (no digest file: pass --digests, set HACF_EXPORT_DIGESTS "
    "or place the per-user file)"
)
LEAK = HOME_ROOT + "someone/private\n"


@pytest.fixture
def world(tmp_path: Path) -> World:
    """The synthetic world of test_build.py."""
    return make_world(tmp_path)


@pytest.fixture
def digests(tmp_path: Path) -> Path:
    """A digest file holding a fake address's digest and a stand-in literal's window line."""
    path = tmp_path / "digests.sha256"
    path.write_text(f"{sha(FAKE_ADDRESS.lower())} fake\n{window_line(STAND_IN)}\n")
    return path


def env_without_digests(tmp_path: Path) -> dict[str, str]:
    """The environment with no digest file: HACF_EXPORT_DIGESTS unset, XDG_CONFIG_HOME empty."""
    env = os.environ.copy()
    env.pop("HACF_EXPORT_DIGESTS", None)
    config = tmp_path / "xdg-config"
    config.mkdir(exist_ok=True)
    env["XDG_CONFIG_HOME"] = str(config)
    return env


def scan_tree(world: World, tree: str, digests: Path | None, *extra: str):
    """Run `scan --tree <tree>` on the world's work tree; no digest path or stand-in is printed."""
    args = ["scan", "--tree", tree, *extra]
    if digests is not None:
        args += ["--digests", str(digests)]
    env = env_without_digests(world.tmp)
    proc = tool(world, *args, profile=world.profile(), env=env)
    for stream in (proc.stdout, proc.stderr):
        assert STAND_IN not in stream
        assert "digests.sha256" not in stream
    return proc


def commit(world: World, *paths: str) -> None:
    """Commit these work-tree paths on the world's main branch."""
    env = clean_env(
        GIT_AUTHOR_NAME="Dev",
        GIT_AUTHOR_EMAIL="dev@example.org",
        GIT_COMMITTER_NAME="Dev",
        GIT_COMMITTER_EMAIL="dev@example.org",
    )
    g(world.src, "add", "--", *paths, env=env)
    g(world.src, "commit", "-q", "-m", "planted", env=env)


def test_untracked_file_is_scanned(world: World, digests: Path) -> None:
    """(a) A home-root literal in an untracked, not ignored file fails the scan."""
    write(world.src, "notes/new.txt", LEAK)
    proc = scan_tree(world, "worktree", digests)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "scan worktree: " in proc.stdout and ", latest row 0.3.0" in proc.stdout
    assert "FAIL home-dir  notes/new.txt  x1 lines 1" in proc.stdout
    assert "strict failures: 1" in proc.stdout


def test_ignored_file_is_skipped(world: World, digests: Path) -> None:
    """(b) The same literal in a git-ignored file is not scanned."""
    write(world.src, ".gitignore", "secret.txt\n")
    write(world.src, "secret.txt", LEAK)
    proc = scan_tree(world, "worktree", digests)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "secret.txt" not in proc.stdout
    assert "strict failures: 0" in proc.stdout


def test_latest_row_exclude_drops_the_path(world: World, digests: Path) -> None:
    """(c) The literal in a path an exclude row covers for the latest version is not scanned."""
    write(world.src, "late.txt", LEAK)  # tracked; excluded for >=0.2.0, so for 0.3.0
    write(world.src, "lore/new.md", LEAK)  # untracked; excluded for all
    proc = scan_tree(world, "worktree", digests)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "late.txt" not in proc.stdout and "lore/new.md" not in proc.stdout
    assert "strict failures: 0" in proc.stdout


def test_digest_window_hit_is_masked(world: World, digests: Path) -> None:
    """(d) A digest window literal in a tracked file fails, its path shown as <label>."""
    write(world.src, f"dev/{STAND_IN}.txt", f"see {STAND_IN}\n")
    commit(world, f"dev/{STAND_IN}.txt")
    proc = scan_tree(world, "worktree", digests)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAIL stand-in  dev/<stand-in>.txt  x1 lines 1  (never allowlisted)" in proc.stdout


@pytest.mark.parametrize("tree", ["worktree", "head"])
def test_no_digest_file_is_unknown(world: World, tree: str) -> None:
    """(e) No digest file anywhere: exit 3 and the unknown line, never a verdict."""
    write(world.src, "notes/new.txt", LEAK)
    proc = scan_tree(world, tree, None)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert proc.stdout.strip() == UNKNOWN.format(tree=tree)


def test_digest_file_from_the_environment(world: World, digests: Path) -> None:
    """HACF_EXPORT_DIGESTS, then the per-user file under XDG_CONFIG_HOME, are read in turn."""
    write(world.src, "notes/new.txt", LEAK)
    env = env_without_digests(world.tmp)
    env["HACF_EXPORT_DIGESTS"] = str(digests)
    proc = tool(world, "scan", "--tree", "worktree", profile=world.profile(), env=env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    env = env_without_digests(world.tmp)
    user = Path(env["XDG_CONFIG_HOME"]) / "hacf" / "export-digests"
    user.parent.mkdir(parents=True)
    user.write_text(digests.read_text())
    proc = tool(world, "scan", "--tree", "worktree", profile=world.profile(), env=env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "export-digests" not in proc.stdout + proc.stderr


def test_malformed_digest_file_refuses(world: World, tmp_path: Path) -> None:
    """A present but malformed digest file still refuses (exit 2), as the gate does."""
    bad = tmp_path / "bad.sha256"
    bad.write_text("not a digest line\n")
    proc = scan_tree(world, "worktree", bad)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "malformed" in proc.stderr


def test_head_sees_only_committed_files(world: World, digests: Path) -> None:
    """(f) `--tree head` ignores an uncommitted planted file and sees a committed one."""
    write(world.src, "loose.txt", LEAK)
    proc = scan_tree(world, "head", digests)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "scan head: " in proc.stdout
    write(world.src, "kept.txt", LEAK)
    commit(world, "kept.txt")
    proc = scan_tree(world, "head", digests)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAIL home-dir  kept.txt  x1 lines 1" in proc.stdout
    assert "loose.txt" not in proc.stdout


def test_symlink_by_target_and_deleted_file_skipped(world: World, digests: Path) -> None:
    """A symlink is scanned by its target text; a deleted tracked file is skipped."""
    (world.src / "link").symlink_to(HOME_ROOT + "someone")
    (world.src / "README.md").unlink()
    proc = scan_tree(world, "worktree", digests)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAIL home-dir  link  x1 lines 1" in proc.stdout
    assert "README.md" not in proc.stdout


def test_tree_and_version_together_refused(world: World, digests: Path) -> None:
    """(g) `--tree` with `--version` exits 2."""
    proc = scan_tree(world, "worktree", digests, "--version", "0.3.0")
    assert proc.returncode == 2
    assert "not allowed with" in proc.stderr
